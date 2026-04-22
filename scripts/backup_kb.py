#!/usr/bin/env python3
"""
Daily backup of knowledge_chunks and agent_memories to GCS as Parquet.

Usage:
  python backup_kb.py                  # Full backup to GCS
  python backup_kb.py --local          # Full backup to local directory only
  python backup_kb.py --gcs-bucket my-bucket  # Override bucket name
  python backup_kb.py --full           # Full backup (default)
  python backup_kb.py --retain 7      # Keep last N backups on GCS (default 7)

Outputs:
  gs://<bucket>/backups/YYYY-MM-DD/knowledge_chunks.parquet
  gs://<bucket>/backups/YYYY-MM-DD/agent_memories.parquet
  gs://<bucket>/backups/YYYY-MM-DD/metadata.json

Parquet format with snappy compression for BigQuery/Cloud SQL compatibility.
Partition columns: source (for knowledge_chunks), category (for agent_memories).
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://agent:[REDACTED]@localhost:5432/agent_kb")
JOPLIN_DB_URL = os.environ.get("JOPLIN_DATABASE_URL", "postgresql://agent:[REDACTED]@localhost:5432/joplin")


def export_table_to_parquet(conn, table: str, columns: list[str], output_path: Path, partition_col: str | None = None):
    """Export a PostgreSQL table to Parquet via pandas."""
    import pandas as pd
    import numpy as np

    col_str = ", ".join(columns)
    query = f"SELECT {col_str} FROM {table}"

    logger.info(f"Exporting {table}...")
    try:
        df = pd.read_sql(query, conn)
    except Exception as e:
        logger.error(f"  Failed to export {table}: {e}")
        return

    if df.empty:
        logger.warning(f"No rows in {table}")
        return

    logger.info(f"  {len(df)} rows, {len(df.columns)} columns")

    # Convert timestamp columns to timezone-aware for Parquet compatibility
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if df[col].dt.tz is None:
                df[col] = df[col].dt.tz_localize("UTC")

    # Serialize JSONB columns to string — pyarrow can't handle mixed list/dict objects
    import json as _json
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().head(10)
            if sample.apply(lambda x: isinstance(x, (dict, list))).any():
                df[col] = df[col].apply(lambda x: _json.dumps(x) if x is not None else None)

    # Convert pgvector embedding to float arrays
    if "embedding" in df.columns:
        def _to_float_list(x):
            if x is None: return None
            if isinstance(x, str):
                try: return [float(v) for v in x.strip("[]").split(",") if v.strip()]
                except Exception: return None
            if isinstance(x, (list, np.ndarray)): return [float(v) for v in x]
            return list(x)
        df["embedding"] = df["embedding"].apply(_to_float_list)

    df.to_parquet(output_path, engine="pyarrow", compression="snappy", index=False)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"  Written to {output_path} ({size_mb:.1f} MB)")


def backup_to_local(output_dir: Path) -> dict:
    """Backup all tables from both databases to local Parquet files."""
    import pandas as pd
    from pgvector.psycopg import register_vector

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_dir = output_dir / today
    backup_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "date": today,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "databases": {},
    }

    # ── 1. Backup Agent KB ──
    logger.info("Backing up Agent KB...")
    conn = psycopg.connect(DB_URL)
    register_vector(conn)
    try:
        kb_path = backup_dir / "knowledge_chunks.parquet"
        mem_path = backup_dir / "agent_memories.parquet"

        export_table_to_parquet(
            conn,
            "knowledge_chunks",
            ["id", "source", "source_id", "chunk_index", "content", "metadata",
             "embedding", "embedding_model", "user_id", "created_at", "updated_at"],
            kb_path,
        )

        export_table_to_parquet(
            conn,
            "agent_memories",
            ["id", "category", "content", "importance", "created_at", "updated_at",
             "deleted_at", "metadata"],
            mem_path,
        )

        metadata["databases"]["agent_kb"] = {
            "knowledge_chunks_count": pd.read_parquet(kb_path).shape[0] if kb_path.exists() else 0,
            "agent_memories_count": pd.read_parquet(mem_path).shape[0] if mem_path.exists() else 0,
        }
    finally:
        conn.close()

    # ── 2. Backup Joplin DB ──
    logger.info("Backing up Joplin DB...")
    try:
        conn_j = psycopg.connect(JOPLIN_DB_URL)
        try:
            # Joplin schema uses 'name' for the filename and 'content' for the body
            # jop_type: 1=note, 2=folder, 3=setting, 4=resource, etc.
            items_path = backup_dir / "joplin_items.parquet"
            export_table_to_parquet(
                conn_j,
                "items",
                ["id", "name", "content_size", "jop_updated_time", 
                 "jop_type", "owner_id", "jop_parent_id"],
                items_path,
            )
            metadata["databases"]["joplin"] = {
                "items_count": pd.read_parquet(items_path).shape[0] if items_path.exists() else 0,
            }
        finally:
            conn_j.close()
    except Exception as e:
        logger.error(f"Joplin DB backup failed: {e}")

    logger.info(f"Local backup complete: {backup_dir}")
    meta_path = backup_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def upload_to_gcs(local_dir: Path, bucket_name: str, retain: int = 14):

    """Upload local backup to GCS and rotate old backups."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "storage"))
    from gcs import GCSClient

    gcs = GCSClient()
    # Override bucket name if provided
    gcs.bucket_name = bucket_name
    gcs._bucket = None

    if not gcs.available:
        logger.error("GCS not available. Cannot upload.")
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    local_today = local_dir / today

    if not local_today.exists():
        logger.error(f"No local backup found for {today}")
        return False

    # Upload each file
    for f in local_today.iterdir():
        gcs_path = f"backups/{today}/{f.name}"
        content_type = "application/octet-stream" if f.suffix == ".parquet" else "application/json"
        gcs.upload_file(str(f), gcs_path, content_type=content_type)
        logger.info(f"  Uploaded {f.name} to gs://{bucket_name}/{gcs_path}")

    # Rotate old backups
    all_backups = gcs.list_objects("backups/")
    backup_dates = sorted(set(obj.split("/")[1] for obj in all_backups if obj.startswith("backups/")))
    for old_date in backup_dates[:-retain]:
        deleted = gcs.delete_prefix(f"backups/{old_date}/")
        logger.info(f"  Rotated backup {old_date} ({deleted} objects)")

    # Also upload to "latest" symlink
    for f in local_today.iterdir():
        gcs_path = f"backups/latest/{f.name}"
        gcs.upload_file(str(f), gcs_path, content_type="application/octet-stream" if f.suffix == ".parquet" else "application/json")

    logger.info(f"GCS backup complete: gs://{bucket_name}/backups/{today}/")
    return True


def main():
    parser = argparse.ArgumentParser(description="Backup knowledge base to Parquet")
    parser.add_argument("--local", action="store_true", help="Only backup locally, skip GCS upload")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", "agentic-data-storage"), help="GCS bucket name")
    parser.add_argument("--output-dir", default=os.environ.get("BACKUP_DIR", "/tmp/kb-backups"), help="Local output directory")
    parser.add_argument("--retain", type=int, default=7, help="Number of GCS backups to retain (default 7)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = backup_to_local(output_dir)

    if not args.local:
        upload_to_gcs(output_dir, args.gcs_bucket, args.retain)

    logger.info(f"Backup complete. Stats: {json.dumps(metadata, indent=2)}")


if __name__ == "__main__":
    main()
