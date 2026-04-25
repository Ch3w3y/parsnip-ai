#!/usr/bin/env python3
"""
Logical backup of agent_kb (and joplin) to Parquet on GCS.

Modes:
  --mode incremental  (default):  WHERE updated_at > last_cutoff per table.
  --mode full:                    full table snapshot, ignores cutoffs.

A small JSON manifest at gs://<bucket>/backups/parquet/_manifest.json tracks
the last_updated_at cutoff for each (table, run-mode). Restore reads all
partitions; ON CONFLICT DO NOTHING dedupes.

Tables covered (in addition to the original two):
  agent_kb:
    - knowledge_chunks       (vector + content)
    - agent_memories
    - notes / notebooks / note_resources / note_tags / tags
    - hitl_sessions / thread_metadata
    - forex_rates / world_bank_data
    - LangGraph: checkpoints / checkpoint_blobs / checkpoint_writes
  joplin:
    - items                  (legacy, only if jop_type IN (1,2,4) for content)

Usage:
  python backup_kb.py                          # incremental → GCS, all tables
  python backup_kb.py --mode full              # full snapshot
  python backup_kb.py --local                  # incremental locally only
  python backup_kb.py --table notes            # incremental for one table only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backup_kb")

DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://agent:agent@localhost:5432/agent_kb"
)
JOPLIN_DB_URL = os.environ.get(
    "JOPLIN_DATABASE_URL", "postgresql://agent:agent@localhost:5432/joplin"
)
MANIFEST_GCS_PATH = "backups/parquet/_manifest.json"
MANIFEST_LOCAL_NAME = "_manifest.json"


@dataclass
class TableSpec:
    """Describes how to back up a table."""
    name: str
    db_url: str
    columns: list[str]
    cursor_column: str | None  # column for incremental cutoff; None = full only
    has_vector: bool = False
    has_bytea: bool = False


# ── Table catalog ─────────────────────────────────────────────────────────────
KB_TABLES: list[TableSpec] = [
    TableSpec(
        name="knowledge_chunks",
        db_url=DB_URL,
        columns=["id", "source", "source_id", "chunk_index", "content", "metadata",
                 "embedding", "embedding_model", "user_id", "created_at", "updated_at"],
        cursor_column="updated_at",
        has_vector=True,
    ),
    TableSpec(
        name="agent_memories",
        db_url=DB_URL,
        columns=["id", "category", "content", "importance",
                 "created_at", "updated_at", "deleted_at", "metadata"],
        cursor_column="updated_at",
    ),
    TableSpec(
        name="notes",
        db_url=DB_URL,
        columns=["id", "title", "content", "notebook_id", "is_todo", "todo_completed",
                 "source_url", "author", "deleted_at", "created_at", "updated_at"],
        cursor_column="updated_at",
    ),
    TableSpec(
        name="notebooks",
        db_url=DB_URL,
        columns=["id", "title", "parent_id", "\"order\"", "created_at", "updated_at"],
        cursor_column="updated_at",
    ),
    TableSpec(
        name="note_resources",
        db_url=DB_URL,
        columns=["id", "note_id", "filename", "mime_type", "content", "size", "created_at"],
        cursor_column="created_at",
        has_bytea=True,
    ),
    TableSpec(
        name="note_tags",
        db_url=DB_URL,
        columns=["note_id", "tag_id"],
        cursor_column=None,  # junction table, full snapshot only
    ),
    TableSpec(
        name="tags",
        db_url=DB_URL,
        columns=["id", "name", "created_at"],
        cursor_column="created_at",
    ),
    TableSpec(
        name="hitl_sessions",
        db_url=DB_URL,
        columns=["id", "note_id", "last_llm_content", "last_llm_hash",
                 "cycle_count", "status", "created_at", "updated_at"],
        cursor_column="updated_at",
    ),
    TableSpec(
        name="thread_metadata",
        db_url=DB_URL,
        columns=["thread_id", "title", "message_count", "created_at", "updated_at"],
        cursor_column="updated_at",
    ),
    TableSpec(
        name="forex_rates",
        db_url=DB_URL,
        columns=["id", "pair", "base_ccy", "quote_ccy", "rate",
                 "rate_date", "source", "fetched_at"],
        cursor_column="fetched_at",
    ),
    TableSpec(
        name="world_bank_data",
        db_url=DB_URL,
        columns=["id", "country_code", "country_name", "indicator_code",
                 "indicator_name", "year", "value", "unit", "source", "fetched_at"],
        cursor_column="fetched_at",
    ),
    # LangGraph checkpoint tables — created at agent startup, schema may evolve.
    # We back up via SELECT *; restore reads back via SELECT * and inserts after
    # AsyncPostgresSaver.setup() creates the schema.
    TableSpec(
        name="checkpoints", db_url=DB_URL, columns=["*"], cursor_column=None,
        has_bytea=True,
    ),
    TableSpec(
        name="checkpoint_blobs", db_url=DB_URL, columns=["*"], cursor_column=None,
        has_bytea=True,
    ),
    TableSpec(
        name="checkpoint_writes", db_url=DB_URL, columns=["*"], cursor_column=None,
        has_bytea=True,
    ),
]

JOPLIN_TABLES: list[TableSpec] = [
    TableSpec(
        name="items", db_url=JOPLIN_DB_URL,
        columns=["id", "name", "content", "mime_type", "content_size",
                 "jop_id", "jop_updated_time", "jop_type",
                 "owner_id", "jop_parent_id"],
        cursor_column="jop_updated_time",
        has_bytea=True,
    ),
]

ALL_TABLES = KB_TABLES + JOPLIN_TABLES


# ── Manifest handling ─────────────────────────────────────────────────────────
def load_manifest(gcs, local_dir: Path, use_gcs: bool) -> dict:
    if use_gcs and gcs and gcs.available:
        try:
            data = gcs.download_bytes(MANIFEST_GCS_PATH)
            if data:
                return json.loads(data.decode("utf-8"))
        except Exception as e:
            logger.warning(f"  Could not read manifest from GCS: {e}")
    local_path = local_dir / MANIFEST_LOCAL_NAME
    if local_path.exists():
        return json.loads(local_path.read_text())
    return {"version": 1, "tables": {}}


def save_manifest(gcs, local_dir: Path, manifest: dict, use_gcs: bool):
    payload = json.dumps(manifest, indent=2, default=str).encode("utf-8")
    (local_dir / MANIFEST_LOCAL_NAME).write_bytes(payload)
    if use_gcs and gcs and gcs.available:
        gcs.upload_bytes(payload, MANIFEST_GCS_PATH, content_type="application/json")


# ── Per-table export ─────────────────────────────────────────────────────────
def export_table(conn, spec: TableSpec, out_path: Path, cutoff: datetime | None) -> dict:
    """Export one table to Parquet. Returns stats dict."""
    import pandas as pd

    cols = ", ".join(spec.columns)
    where = ""
    params: tuple = ()
    if spec.cursor_column and cutoff:
        where = f" WHERE {spec.cursor_column} > %s"
        params = (cutoff,)
    query = f"SELECT {cols} FROM {spec.name}{where}"

    try:
        df = pd.read_sql(query, conn, params=params)
    except Exception as e:
        logger.warning(f"  {spec.name}: query failed ({e}) — skipping")
        return {"rows": 0, "size_bytes": 0, "max_cursor": None, "skipped": True}

    if df.empty:
        return {"rows": 0, "size_bytes": 0, "max_cursor": None}

    # Normalize columns for Parquet compatibility
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]) and df[col].dt.tz is None:
            df[col] = df[col].dt.tz_localize("UTC")

    # JSONB / dict / list columns → JSON string
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().head(20)
            if sample.apply(lambda x: isinstance(x, (dict, list))).any():
                df[col] = df[col].apply(lambda x: json.dumps(x) if x is not None else None)

    # pgvector → list[float]
    if "embedding" in df.columns and spec.has_vector:
        import numpy as np

        def _to_list(x):
            if x is None:
                return None
            if isinstance(x, str):
                try:
                    return [float(v) for v in x.strip("[]").split(",") if v.strip()]
                except Exception:
                    return None
            if isinstance(x, (list, np.ndarray)):
                return [float(v) for v in x]
            return list(x)

        df["embedding"] = df["embedding"].apply(_to_list)

    # bytea → keep as bytes (pyarrow handles it)
    df.to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)

    max_cursor = None
    if spec.cursor_column and spec.cursor_column in df.columns:
        max_cursor = df[spec.cursor_column].max()
        if hasattr(max_cursor, "isoformat"):
            max_cursor = max_cursor.isoformat()
        else:
            max_cursor = str(max_cursor)

    return {
        "rows": len(df),
        "size_bytes": out_path.stat().st_size,
        "max_cursor": max_cursor,
    }


def main():
    parser = argparse.ArgumentParser(description="Incremental KB backup to Parquet")
    parser.add_argument("--mode", choices=["incremental", "full"], default="incremental",
                        help="incremental (default): WHERE cursor > last; full: snapshot all")
    parser.add_argument("--local", action="store_true", help="local-only, skip GCS")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", ""))
    parser.add_argument("--output-dir", default=os.environ.get("BACKUP_DIR", "/tmp/kb-backups"))
    parser.add_argument("--table", help="Backup only this table (default: all)")
    parser.add_argument("--retain-fulls", type=int, default=4,
                        help="Number of full snapshots to keep on GCS")
    args = parser.parse_args()

    use_gcs = not args.local
    sys.path.insert(0, str(Path(__file__).parent.parent / "storage"))
    from gcs import GCSClient  # noqa: E402

    gcs = GCSClient()
    if args.gcs_bucket:
        gcs.bucket_name = args.gcs_bucket
        gcs._bucket = None

    if use_gcs and not gcs.available:
        logger.error("GCS not available; rerun with --local or fix credentials.")
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = out_dir / today
    backup_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(gcs, out_dir, use_gcs)
    manifest_tables = manifest.get("tables", {})

    targets = [t for t in ALL_TABLES if not args.table or t.name == args.table]
    summary = {"started_at": datetime.now(timezone.utc).isoformat(),
               "mode": args.mode, "tables": {}}

    # Group tables by db_url so we open one connection per DB
    by_db: dict[str, list[TableSpec]] = {}
    for spec in targets:
        by_db.setdefault(spec.db_url, []).append(spec)

    for db_url, specs in by_db.items():
        try:
            conn = psycopg.connect(db_url)
        except Exception as e:
            logger.error(f"Could not connect to {db_url.split('@')[-1]}: {e}")
            for spec in specs:
                summary["tables"][spec.name] = {"error": "connection_failed"}
            continue

        try:
            for spec in specs:
                cutoff = None
                if args.mode == "incremental" and spec.cursor_column:
                    last = manifest_tables.get(spec.name, {}).get("last_cursor")
                    if last:
                        cutoff = last  # psycopg accepts ISO string for timestamptz
                logger.info(f"  {spec.name}: mode={args.mode} cutoff={cutoff}")

                fname = f"{spec.name}_{args.mode}_{timestamp}.parquet"
                out_path = backup_dir / fname
                stats = export_table(conn, spec, out_path, cutoff)
                summary["tables"][spec.name] = stats

                if stats["rows"] == 0:
                    if out_path.exists():
                        out_path.unlink()
                    continue

                if use_gcs:
                    gcs_path = f"backups/parquet/{spec.name}/dt={today}/{fname}"
                    gcs.upload_file(str(out_path), gcs_path)
                    logger.info(f"    uploaded {stats['rows']} rows → gs://{gcs.bucket_name}/{gcs_path}")

                # Update manifest cursor
                if stats.get("max_cursor"):
                    manifest_tables[spec.name] = {
                        "last_cursor": stats["max_cursor"],
                        "last_run": datetime.now(timezone.utc).isoformat(),
                        "last_mode": args.mode,
                    }
        finally:
            conn.close()

    manifest["tables"] = manifest_tables
    manifest["last_run"] = datetime.now(timezone.utc).isoformat()
    save_manifest(gcs, out_dir, manifest, use_gcs)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary_path = backup_dir / f"summary_{timestamp}.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    if use_gcs:
        gcs.upload_bytes(json.dumps(summary, indent=2).encode("utf-8"),
                         f"backups/parquet/_runs/{timestamp}.json",
                         content_type="application/json")

    total_rows = sum(t.get("rows", 0) for t in summary["tables"].values())
    total_mb = sum(t.get("size_bytes", 0) for t in summary["tables"].values()) / 1_048_576
    logger.info(f"Done. {total_rows:,} rows total, {total_mb:.1f} MiB written.")


if __name__ == "__main__":
    main()
