#!/usr/bin/env python3
"""
Re-embed all knowledge_chunks that lack embeddings.

Usage:
    cd ingestion
    export DATABASE_URL=postgresql://agent:PASSWORD@localhost:5432/agent_kb
    export OLLAMA_BASE_URL=http://your-local-gpu-ip:11434
    export EMBED_MODEL=mxbai-embed-large
    uv run python reembed_chunks.py --batch-size 64

This updates the embedding column in-place using batches.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")


async def fetch_missing_chunks(conn, batch_size: int):
    """Yield batches of (id, content) tuples that need embeddings."""
    # Use a named cursor for server-side streaming
    async with conn.cursor(name="reembed_stream") as cur:
        await cur.execute(
            "SELECT id, content FROM knowledge_chunks WHERE embedding IS NULL ORDER BY id"
        )
        batch = []
        async for row in cur:
            batch.append(row)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


async def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """Call Ollama embed API."""
    cleaned = []
    for t in texts:
        # Strip null bytes and control chars
        c = "".join(c for c in t if ord(c) >= 32 or c in "\n\t\r")
        cleaned.append(c[:2000])  # ~500 tokens cap

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(
                f"{OLLAMA_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": cleaned, "truncate": True},
            )
            r.raise_for_status()
            data = r.json()
            return data["embeddings"]
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return None


async def update_embeddings(conn, ids: list[int], embeddings: list[list[float]]):
    """Update embedding column for given IDs."""
    async with conn.cursor() as cur:
        # Use executemany for efficiency
        await cur.executemany(
            "UPDATE knowledge_chunks SET embedding = %s::vector WHERE id = %s",
            [(emb, id_) for id_, emb in zip(ids, embeddings)],
        )
        await conn.commit()


async def main():
    parser = argparse.ArgumentParser(description="Re-embed knowledge chunks")
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding batch size")
    parser.add_argument("--limit", type=int, default=None, help="Max chunks to process")
    args = parser.parse_args()

    if not DB_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    conn = await psycopg.AsyncConnection.connect(DB_URL)

    # Count total missing
    async with conn.cursor() as cur:
        await cur.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NULL")
        row = await cur.fetchone()
        total_missing = row[0] if row else 0

    if total_missing == 0:
        logger.info("All chunks already have embeddings. Nothing to do.")
        await conn.close()
        return

    logger.info(f"Found {total_missing:,} chunks without embeddings")
    logger.info(f"Batch size: {args.batch_size}")

    processed = 0
    failed = 0
    t0 = time.time()

    pbar = tqdm(total=min(total_missing, args.limit or total_missing), unit="chunks")

    async for batch in fetch_missing_chunks(conn, args.batch_size):
        if args.limit and processed >= args.limit:
            break
            
        # Ensure we don't exceed limit in the last batch
        if args.limit and (processed + len(batch)) > args.limit:
            batch = batch[:(args.limit - processed)]

        if not batch:
            break

        ids = [row[0] for row in batch]
        texts = [row[1] for row in batch]

        embeddings = await embed_batch(texts)
        if embeddings is None:
            failed += len(batch)
            logger.warning(f"Batch failed, skipping {len(batch)} chunks")
            continue

        await update_embeddings(conn, ids, embeddings)
        processed += len(batch)
        pbar.update(len(batch))

        # Progress log every 1000
        if processed % 1000 < args.batch_size:
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            logger.info(f"Progress: {processed:,} / {total_missing:,} ({rate:.1f} chunks/sec)")

    pbar.close()
    await conn.close()

    elapsed = time.time() - t0
    logger.info(f"Done. Processed: {processed:,}, Failed: {failed:,}, Time: {elapsed/60:.1f}min")


if __name__ == "__main__":
    asyncio.run(main())
