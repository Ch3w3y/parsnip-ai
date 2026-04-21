#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "psycopg[binary]>=3.1",
#   "pgvector>=0.3",
#   "pyarrow>=15.0",
#   "python-dotenv>=1.0",
# ]
# ///
"""
Backup knowledge_chunks to Parquet (embeddings included by default).

Compression: zstd (best ratio for float32 vectors — ~2-3x smaller than snappy).
Output: one file per source by default, or a single combined file with --single-file.

The combined file is ideal for GCS upload and BigQuery external tables:
    gcloud storage cp backups/knowledge_chunks_20260417.parquet gs://your-bucket/pi_agent/

BigQuery can query it directly without import:
    CREATE EXTERNAL TABLE pi_agent.knowledge_chunks
    OPTIONS (format='PARQUET', uris=['gs://your-bucket/pi_agent/*.parquet']);

Size estimates (zstd, with embeddings):
    Current (~8k chunks):      ~120 MB
    Full Wikipedia (~35M):     ~80-120 GB

Usage:
    uv run scripts/backup_db.py                        # per-source files, with embeddings
    uv run scripts/backup_db.py --single-file          # one combined file
    uv run scripts/backup_db.py --no-embeddings        # content+metadata only (~30x smaller)
    uv run scripts/backup_db.py --source news          # single source
    uv run scripts/backup_db.py --out /mnt/fileserver/pi_agent_backups
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

load_dotenv(Path(__file__).parent.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_SIZE   = 10_000  # rows streamed per DB fetch

SCHEMA_BASE = pa.schema([
    pa.field("source",      pa.string()),
    pa.field("source_id",   pa.string()),
    pa.field("chunk_index", pa.int32()),
    pa.field("content",     pa.string()),
    pa.field("metadata",    pa.string()),    # JSON string — preserves all fields
    pa.field("created_at",  pa.string()),    # ISO8601 UTC
    pa.field("updated_at",  pa.string()),    # ISO8601 UTC, nullable
])

SCHEMA_WITH_EMB = pa.schema([
    *SCHEMA_BASE,
    pa.field("embedding", pa.list_(pa.float32())),  # 4096-dim
])


def build_writer(path: Path, include_embeddings: bool) -> pq.ParquetWriter:
    schema = SCHEMA_WITH_EMB if include_embeddings else SCHEMA_BASE
    return pq.ParquetWriter(path, schema, compression="zstd", compression_level=3)


def flush_batch(rows: list[tuple], writer: pq.ParquetWriter, include_embeddings: bool):
    if not rows:
        return
    t = list(zip(*rows))
    arrays = [
        pa.array(t[0], type=pa.string()),
        pa.array(t[1], type=pa.string()),
        pa.array(t[2], type=pa.int32()),
        pa.array(t[3], type=pa.string()),
        pa.array(
            [json.dumps(m) if isinstance(m, dict) else (m or "{}") for m in t[4]],
            type=pa.string(),
        ),
        pa.array([str(v) if v else None for v in t[5]], type=pa.string()),
        pa.array([str(v) if v else None for v in t[6]], type=pa.string()),
    ]
    if include_embeddings:
        arrays.append(pa.array(
            [list(v) if v is not None else None for v in t[7]],
            type=pa.list_(pa.float32()),
        ))
    schema = SCHEMA_WITH_EMB if include_embeddings else SCHEMA_BASE
    writer.write_batch(pa.record_batch(arrays, schema=schema))


def stream_source(conn, source: str | None, writer: pq.ParquetWriter, include_embeddings: bool) -> int:
    cols = ["source", "source_id", "chunk_index", "content", "metadata", "created_at", "updated_at"]
    if include_embeddings:
        cols.append("embedding")

    where = "WHERE source = %s" if source else ""
    params = (source,) if source else ()
    query = f"SELECT {', '.join(cols)} FROM knowledge_chunks {where} ORDER BY source, id"

    written = 0
    batch: list[tuple] = []

    with conn.cursor(name="backup_cur") as cur:
        cur.itersize = BATCH_SIZE
        cur.execute(query, params)
        for row in cur:
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                flush_batch(batch, writer, include_embeddings)
                written += len(batch)
                batch = []
                print(f"    {written:,} rows written…", end="\r", flush=True)
        flush_batch(batch, writer, include_embeddings)
        written += len(batch)

    return written


def main():
    parser = argparse.ArgumentParser(description="Backup knowledge_chunks to Parquet")
    parser.add_argument("--out", default="./backups", help="Output directory")
    parser.add_argument("--source", default=None, help="Backup a single source (default: all)")
    parser.add_argument("--single-file", action="store_true",
                        help="Write all sources to one combined file (best for GCS/BigQuery)")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Exclude embedding vectors (~30x smaller; restore will re-embed)")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    include_embeddings = not args.no_embeddings
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    conn = psycopg.connect(DATABASE_URL)
    register_vector(conn)

    if args.source:
        sources = [args.source]
    else:
        rows = conn.execute("SELECT DISTINCT source FROM knowledge_chunks ORDER BY source").fetchall()
        sources = [r[0] for r in rows]

    if not sources:
        print("No data in knowledge_chunks yet.")
        conn.close()
        return

    emb_label = "with embeddings" if include_embeddings else "no embeddings"
    total_rows = 0

    if args.single_file:
        out_path = out_dir / f"knowledge_chunks_{timestamp}.parquet"
        print(f"Backing up {len(sources)} source(s) → {out_path.name} ({emb_label}, zstd)")
        writer = build_writer(out_path, include_embeddings)
        total_rows = stream_source(conn, None, writer, include_embeddings)
        writer.close()
        size_mb = out_path.stat().st_size / 1_048_576
        size_label = f"{size_mb/1024:.2f} GB" if size_mb > 1024 else f"{size_mb:.1f} MB"
        print(f"\n  {total_rows:,} rows  {size_label}  → {out_path}")
    else:
        print(f"Backing up {len(sources)} source(s) to {out_dir}/ ({emb_label}, zstd)")
        for source in sources:
            row = conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE source = %s", (source,)).fetchone()
            count = row[0] if row else 0
            if count == 0:
                print(f"  {source}: empty, skipping")
                continue
            out_path = out_dir / f"{source}_{timestamp}.parquet"
            print(f"  {source}: {count:,} rows → {out_path.name}")
            writer = build_writer(out_path, include_embeddings)
            n = stream_source(conn, source, writer, include_embeddings)
            writer.close()
            size_mb = out_path.stat().st_size / 1_048_576
            size_label = f"{size_mb/1024:.2f} GB" if size_mb > 1024 else f"{size_mb:.1f} MB"
            print(f"    {n:,} rows  {size_label}")
            total_rows += n

    conn.close()
    print(f"\nDone. {total_rows:,} rows backed up to {out_dir.resolve()}/")


if __name__ == "__main__":
    main()
