#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "psycopg[binary]>=3.1",
#   "pgvector>=0.3",
#   "pyarrow>=15.0",
#   "httpx>=0.25",
#   "python-dotenv>=1.0",
# ]
# ///
"""
Restore knowledge_chunks from a Parquet backup.

If the backup includes embeddings, rows are inserted directly.
If not (the default), content is re-embedded via Ollama before insert.

Usage:
    # Restore a single file (re-embeds if no embedding column):
    uv run scripts/restore_db.py backups/news_20260417_120000.parquet

    # Restore all parquet files in a directory:
    uv run scripts/restore_db.py backups/

    # Skip re-embedding check (insert only rows with embeddings present):
    uv run scripts/restore_db.py backups/ --no-reembed

    # Dry run — count rows without writing anything:
    uv run scripts/restore_db.py backups/ --dry-run
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import psycopg
import pyarrow.parquet as pq
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
import os

from throttle import BackupThrottle

load_dotenv(Path(__file__).parent.parent / ".env")

DATABASE_URL   = os.environ.get("DATABASE_URL", "")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "qwen3-embedding:8b")

EMBED_BATCH    = 64     # texts per Ollama call
INSERT_BATCH   = 500    # rows per DB transaction


def embed_batch_sync(texts: list[str], retries: int = 3) -> list[list[float]] | None:
    for attempt in range(retries):
        try:
            r = httpx.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": texts},
                timeout=120,
            )
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception as e:
            wait = 2 ** attempt
            print(f"  Embed attempt {attempt+1}/{retries} failed: {e} — retry in {wait}s")
            time.sleep(wait)
    return None


UPSERT_SQL = """
    INSERT INTO knowledge_chunks
        (source, source_id, chunk_index, content, metadata, embedding, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (source, source_id, chunk_index) DO NOTHING
"""


def restore_file(path: Path, conn, dry_run: bool, no_reembed: bool, throttle: BackupThrottle | None = None) -> dict:
    table = pq.read_table(path)
    col_names = table.schema.names
    has_embeddings = "embedding" in col_names

    total = len(table)
    print(f"\n  {path.name}: {total:,} rows  {'(has embeddings)' if has_embeddings else '(no embeddings — will re-embed)'}")

    if not has_embeddings and no_reembed:
        print("  --no-reembed set, skipping file with no embeddings.")
        return {"skipped": total}

    if dry_run:
        print(f"  [dry-run] would restore {total:,} rows")
        return {"dry_run": total}

    sources      = table["source"].to_pylist()
    source_ids   = table["source_id"].to_pylist()
    chunk_idxs   = table["chunk_index"].to_pylist()
    contents     = table["content"].to_pylist()
    metadatas    = table["metadata"].to_pylist()
    created_ats  = table["created_at"].to_pylist()
    embeddings   = table["embedding"].to_pylist() if has_embeddings else None

    inserted = 0
    skipped  = 0

    for batch_start in range(0, total, INSERT_BATCH):
        batch_end = min(batch_start + INSERT_BATCH, total)
        batch_contents = contents[batch_start:batch_end]

        if has_embeddings:
            batch_embs = embeddings[batch_start:batch_end]
        else:
            batch_embs = None
            # Re-embed in sub-batches
            all_embs: list[list[float]] = []
            for i in range(0, len(batch_contents), EMBED_BATCH):
                sub = batch_contents[i : i + EMBED_BATCH]
                embs = embed_batch_sync(sub)
                if embs is None:
                    print(f"  ERROR: embedding failed at row {batch_start + i}, skipping batch")
                    break
                all_embs.extend(embs)
            if len(all_embs) != len(batch_contents):
                skipped += len(batch_contents)
                continue
            batch_embs = all_embs

        with conn.transaction():
            for i in range(batch_end - batch_start):
                idx = batch_start + i
                meta = metadatas[idx]
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                try:
                    result = conn.execute(
                        UPSERT_SQL,
                        (
                            sources[idx],
                            source_ids[idx],
                            chunk_idxs[idx],
                            batch_contents[i],
                            psycopg.types.json.Jsonb(meta),
                            batch_embs[i],
                            created_ats[idx],
                        ),
                    )
                    if result.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"  Insert error row {idx}: {e}")
                    skipped += 1

        print(f"    {inserted + skipped:,}/{total:,} processed ({inserted:,} inserted, {skipped:,} skipped)", end="\r")
        if throttle:
            throttle.sleep_between_batches()

    print(f"    {total:,} rows: {inserted:,} inserted, {skipped:,} already existed")
    return {"inserted": inserted, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description="Restore knowledge_chunks from Parquet backup")
    parser.add_argument("path", help="Parquet file or directory of parquet files")
    parser.add_argument("--no-reembed", action="store_true",
                        help="Skip files that have no embedding column (instead of re-embedding)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count rows only, do not write to DB")
    parser.add_argument("--throttle", action="store_true", default=True,
                        help="Enable CPU/network throttling (default: enabled)")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.glob("*.parquet"))
    elif target.suffix == ".parquet":
        files = [target]
    else:
        print(f"ERROR: {target} is not a .parquet file or directory")
        sys.exit(1)

    if not files:
        print("No .parquet files found.")
        sys.exit(1)

    print(f"Restoring {len(files)} file(s)...")

    conn = psycopg.connect(DATABASE_URL)
    register_vector(conn)

    throttle = BackupThrottle.from_env()
    throttle.nice()
    throttle.log_config()

    totals = {"inserted": 0, "skipped": 0, "dry_run": 0}
    for f in files:
        result = restore_file(f, conn, args.dry_run, args.no_reembed, throttle=throttle)
        for k, v in result.items():
            totals[k] = totals.get(k, 0) + v

    conn.close()
    print(f"\nDone. Inserted: {totals.get('inserted', 0):,}  Already existed: {totals.get('skipped', 0):,}")


if __name__ == "__main__":
    main()
