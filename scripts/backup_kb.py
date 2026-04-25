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

import pyarrow as pa

import psycopg

from throttle import BackupThrottle

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
def _normalise_chunk(df, spec):
    import pandas as pd

    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if df[col].dt.tz is None:
                df[col] = df[col].dt.tz_localize("UTC")

    # JSONB / dict / list columns → JSON string
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().head(20)
            if sample.apply(lambda x: isinstance(x, (dict, list))).any():
                df[col] = df[col].apply(lambda x: json.dumps(x) if x is not None else None)

    # pgvector: keep raw string, encode to bytes for pa.binary() — avoids
    # the triple-copy string→list[float]→Arrow that OOMs on large tables.
    # On restore, the raw string is cast back via $embedding::vector.
    if "embedding" in df.columns and spec.has_vector:
        def _encode_embedding(x):
            if x is None or isinstance(x, float):
                return None
            if isinstance(x, str):
                return x.encode("utf-8")
            return str(list(x)).encode("utf-8")

        df["embedding"] = df["embedding"].apply(_encode_embedding)

    return df


def _chunk_size_for(spec: TableSpec) -> int:
    if spec.has_vector:
        return 2_000
    if spec.has_bytea:
        return 5_000
    return 50_000


def _get_timestamptz_cols(conn, table: str, col_set: set[str]) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND data_type = 'timestamp with time zone' "
            "AND column_name = ANY(%s)",
            (table, list(col_set)),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _pg_type_to_arrow(data_type: str) -> pa.DataType | None:
    mapping = {
        "bigint": pa.int64(),
        "integer": pa.int32(),
        "smallint": pa.int16(),
        "double precision": pa.float64(),
        "real": pa.float32(),
        "boolean": pa.bool_(),
        "text": pa.large_string(),
        "character varying": pa.large_string(),
        "jsonb": pa.large_string(),
        "timestamp with time zone": pa.timestamp("us", tz="UTC"),
        "timestamp without time zone": pa.timestamp("us"),
        "date": pa.date32(),
        "bytea": pa.large_binary(),
        "uuid": pa.large_string(),
    }
    return mapping.get(data_type)


def _build_arrow_schema(conn, table: str, columns: list[str], has_vector: bool) -> pa.Schema | None:
    try:
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = ANY(%s) ORDER BY ordinal_position",
            (table, columns),
        ).fetchall()
    except Exception:
        return None

    col_map = {r[0]: r[1] for r in rows}
    fields = []
    for col in columns:
        pg_type = col_map.get(col)
        if pg_type is None:
            return None
        if has_vector and col == "embedding":
            fields.append(pa.field("embedding", pa.binary()))
            continue
        arrow_type = _pg_type_to_arrow(pg_type)
        if arrow_type is None:
            return None
        fields.append(pa.field(col, arrow_type))
    return pa.schema(fields)


def export_table(conn, spec: TableSpec, out_path: Path, cutoff: datetime | None, throttle: BackupThrottle | None = None) -> dict:
    """Export one table to Parquet via server-side cursor streaming to avoid OOM.

    Uses a named psycopg cursor so the DB holds the resultset and rows are
    fetched in small batches (itersize).  This avoids loading the entire table
    into client memory before the first chunk is yielded — the root cause of
    OOM on knowledge_chunks (16 M rows × 4 KB embeddings).
    """
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    cols = ", ".join(spec.columns)
    where = ""
    params: tuple = ()
    if spec.cursor_column and cutoff:
        where = f" WHERE {spec.cursor_column} > %s"
        params = (cutoff,)
    query = f"SELECT {cols} FROM {spec.name}{where}"

    # Wrap timestamp columns with clamp (psycopg can't parse year > 10K)
    ts_col_names = _get_timestamptz_cols(conn, spec.name, set(spec.columns))
    if ts_col_names:
        safe_cols = []
        for c in spec.columns:
            if c in ts_col_names:
                safe_cols.append(
                    f"LEAST({c}, '2262-04-11 23:47:16.854775'::timestamptz) AS {c}"
                )
            else:
                safe_cols.append(c)
        query = f"SELECT {', '.join(safe_cols)} FROM {spec.name}{where}"

    chunk_size = _chunk_size_for(spec)
    if throttle and throttle.cursor_itersize:
        chunk_size = throttle.cursor_itersize
    total_rows = 0
    max_cursor_val = None
    writer: pq.ParquetWriter | None = None
    arrow_schema: pa.Schema | None = _build_arrow_schema(
        conn, spec.name, spec.columns, spec.has_vector
    )

    # Clamp ceiling — if the max cursor value we observe equals this,
    # it came from LEAST() clamping and we must query the real max instead.
    CLAMP_CEILING = datetime(2262, 4, 11, 23, 47, 16, 854775, tzinfo=timezone.utc)

    try:
        # Named cursor → server-side cursor; itersize controls network fetch size
        with conn.cursor(name=f"bkup_{spec.name}") as cur:
            cur.itersize = chunk_size
            cur.execute(query, params)
            col_names = [d.name for d in cur.description] if cur.description else []

            while True:
                rows = cur.fetchmany(chunk_size)
                if not rows:
                    break
                try:
                    chunk_df = pd.DataFrame(rows, columns=col_names)

                    if spec.cursor_column and spec.cursor_column in chunk_df.columns:
                        col_vals = chunk_df[spec.cursor_column].dropna()
                        if len(col_vals) > 0:
                            col_max = col_vals.max()
                            if max_cursor_val is None or col_max > max_cursor_val:
                                max_cursor_val = col_max

                    chunk_df = _normalise_chunk(chunk_df, spec)
                    table = pa.Table.from_pandas(
                        chunk_df, preserve_index=False, schema=arrow_schema
                    )

                    if spec.has_vector and "embedding" in table.schema.names:
                        idx = table.schema.get_field_index("embedding")
                        field = table.schema.field(idx)
                        if field.type != pa.binary():
                            table = table.set_column(
                                idx, field.name, table.column(idx).cast(pa.binary())
                            )

                    if writer is None:
                        writer_schema = arrow_schema or table.schema
                        writer = pq.ParquetWriter(out_path, writer_schema, compression="zstd")
                    writer.write_table(table)
                    total_rows += len(chunk_df)
                    logger.info(f"    {spec.name}: wrote {len(chunk_df)} rows (total {total_rows:,})")
                    if throttle:
                        throttle.sleep_between_batches()
                except (pa.ArrowInvalid, pa.ArrowTypeError) as e:
                    logger.warning(f"  {spec.name}: skipping chunk at row {total_rows:,} ({e})")
                    continue
    except Exception as e:
        logger.warning(f"  {spec.name}: query failed ({e}) — keeping partial export")
    finally:
        if writer is not None:
            writer.close()

    if total_rows == 0:
        return {"rows": 0, "size_bytes": 0, "max_cursor": None}

    max_cursor = None
    if spec.cursor_column and max_cursor_val is not None:
        real_max = max_cursor_val
        if isinstance(real_max, datetime) and real_max >= CLAMP_CEILING:
            logger.warning(
                f"  {spec.name}: max cursor hit clamp ceiling {CLAMP_CEILING}; "
                f"querying real max from DB"
            )
            try:
                real_max_row = conn.execute(
                    f"SELECT MAX({spec.cursor_column}) FROM {spec.name} "
                    f"WHERE {spec.cursor_column} < '2262-04-11 23:47:16.854775'::timestamptz"
                ).fetchone()
                real_max = real_max_row[0] if real_max_row and real_max_row[0] else max_cursor_val
            except Exception as e:
                logger.warning(f"  {spec.name}: real max query failed ({e}); using clamp ceiling")
        max_cursor = real_max
        if hasattr(max_cursor, "isoformat"):
            max_cursor = max_cursor.isoformat()
        else:
            max_cursor = str(max_cursor)

    return {
        "rows": total_rows,
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
    throttle = BackupThrottle.from_env()
    throttle.nice()
    throttle.log_config()
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
                stats = export_table(conn, spec, out_path, cutoff, throttle=throttle)
                summary["tables"][spec.name] = stats

                if stats["rows"] == 0:
                    if out_path.exists():
                        out_path.unlink()
                    continue

                if use_gcs:
                    gcs_path = f"backups/parquet/{spec.name}/dt={today}/{fname}"
                    if throttle:
                        throttle.upload_file(gcs, str(out_path), gcs_path)
                    else:
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
