#!/usr/bin/env python3
"""
Wikipedia ingestion pipeline.

Usage:
    # 1. Download and extract the dump first:
    #    bash ../scripts/download_wikipedia.sh

    # 2. Run ingestion (embeddings via remote Ollama + mxbai-embed-large):
    #    cd ingestion
    #    export DATABASE_URL=postgresql://agent:PASSWORD@localhost:5432/agent_kb
    #    export OLLAMA_BASE_URL=http://your-local-gpu-ip:11434
    #    export EMBED_MODEL=mxbai-embed-large
    #    uv run python ingest_wikipedia.py --wiki-dir ./data/wiki_extracted

    # 3. Monitor progress:
    #    curl localhost:8000/stats
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

from utils import (
    chunk_text,
    compute_content_hash,
    embed_batch,
    bulk_upsert_chunks,
    cleanup_orphan_chunks,
    get_db_connection,
    create_job,
    finish_job,
    update_job_progress,
    write_to_dlq,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Tune these for your hardware:
# - Larger BATCH_SIZE = more GPU VRAM, faster throughput
# - mxbai-embed-large (1024 dims, 512-token context): 64 is safe.
#   Stop the scheduler before running Wikipedia ingestion to avoid
#   competing embed requests.
BATCH_SIZE = 64
COMMIT_EVERY = 500  # commit to DB every N articles


def iter_wiki_articles(wiki_dir: Path):
    """Yield dicts {title, url, id, text} from wikiextractor JSONL output."""
    for path in sorted(wiki_dir.rglob("wiki_*")):
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    article = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not article.get("text") or len(article["text"]) < 100:
                    continue
                yield article


async def process_articles(wiki_dir: Path, skip: int = 0, limit: int | None = None):
    conn = None
    job_id = None
    try:
        conn = await get_db_connection()
        job_id = await create_job(conn, "wikipedia")
        await conn.commit()

        # Pending batch: accumulate until BATCH_SIZE, then embed + bulk insert together
        pending_texts: list[str] = []
        pending_rows: list[tuple] = []  # (source_id, metadata_dict) per chunk
        # Track chunk counts per source_id for orphan cleanup
        source_chunk_counts: dict[str, int] = {}

        total_articles = 0
        total_chunks_inserted = 0
        skipped = 0
        t0 = time.time()

        async def flush_batch():
            nonlocal total_chunks_inserted
            if not pending_texts:
                return
            embeddings = await embed_batch(pending_texts)
            if embeddings is None:
                logger.error(
                    f"Embedding failed for batch of {len(pending_texts)}, skipping."
                )
                pending_texts.clear()
                pending_rows.clear()
                source_chunk_counts.clear()
                return

            bulk_rows = [
                (
                    "wikipedia",
                    source_id,
                    chunk_idx,
                    text,
                    compute_content_hash(text),
                    metadata,
                    emb,
                    "mxbai-embed-large",
                )
                for (source_id, chunk_idx, metadata), text, emb in zip(
                    pending_rows, pending_texts, embeddings
                )
                if emb is not None
            ]
            inserted = await bulk_upsert_chunks(conn, bulk_rows, on_conflict="update")
            total_chunks_inserted += inserted

            # Clean up orphan chunks for source_ids in this batch
            for sid, count in source_chunk_counts.items():
                await cleanup_orphan_chunks(conn, "wikipedia", sid, count)
            source_chunk_counts.clear()

            pending_texts.clear()
            pending_rows.clear()

        articles = iter_wiki_articles(wiki_dir)

        # Apply skip
        for _ in range(skip):
            try:
                next(articles)
                skipped += 1
            except StopIteration:
                break

        with tqdm(desc="Wikipedia articles", unit="art", dynamic_ncols=True) as pbar:
            for article in articles:
                if limit and total_articles >= limit:
                    break

                chunks = chunk_text(article["text"])
                if not chunks:
                    continue

                metadata = {
                    "url": article.get("url", ""),
                    "wiki_id": article.get("id", ""),
                }

                source_id = article["title"]
                for idx, chunk in enumerate(chunks):
                    pending_texts.append(chunk)
                    pending_rows.append((source_id, idx, metadata))

                    if len(pending_texts) >= BATCH_SIZE:
                        await flush_batch()

                source_chunk_counts[source_id] = len(chunks)

                total_articles += 1
                pbar.update(1)

                if total_articles % COMMIT_EVERY == 0:
                    await flush_batch()
                    await update_job_progress(conn, job_id, total_articles)
                    await conn.commit()

                    elapsed = time.time() - t0
                    rate = total_articles / elapsed
                    logger.info(
                        f"Progress: {total_articles:,} articles | "
                        f"{total_chunks_inserted:,} chunks | "
                        f"{rate:.0f} art/s | "
                        f"~{int((7_200_000 - total_articles) / rate / 3600)}h remaining"
                    )

        # Final flush
        await flush_batch()
        await update_job_progress(conn, job_id, total_articles)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again

        elapsed = time.time() - t0
        logger.info(
            f"\nDone! {total_articles:,} articles, {total_chunks_inserted:,} chunks "
            f"in {elapsed / 3600:.1f}h"
        )
    except Exception as exc:
        logger.error(f"wikipedia ingestion failed: {exc}", exc_info=True)
        if conn is not None and job_id is not None:
            try:
                await write_to_dlq(conn, source="wikipedia", source_id=f"job:{job_id}",
                                   content=None, metadata={"job_id": job_id}, error=exc)
                await finish_job(conn, job_id, "failed", error_message=str(exc)[:500])
                await conn.commit()
            except Exception as finish_exc:
                logger.error(f"Failed to mark job as failed: {finish_exc}")
        raise
    finally:
        if conn is not None:
            try:
                await conn.rollback()
            except Exception:
                pass
            try:
                await conn.close()
            except Exception:
                pass


async def get_resume_point() -> int:
    """Return the article count from the last wikipedia job, or 0 if none."""
    try:
        conn = await get_db_connection()
        row = await (
            await conn.execute(
                "SELECT processed FROM ingestion_jobs WHERE source='wikipedia' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
        await conn.close()
        return row[0] if row and row[0] else 0
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Ingest Wikipedia into pgvector")
    parser.add_argument(
        "--wiki-dir",
        default="./data/wiki_extracted",
        help="Directory containing wikiextractor JSONL output",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=None,
        help="Skip first N articles. Omit to auto-resume from last DB checkpoint.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Stop after N articles (for testing)"
    )
    args = parser.parse_args()

    wiki_dir = Path(args.wiki_dir)
    if not wiki_dir.exists():
        logger.error(f"Wiki directory not found: {wiki_dir}")
        logger.error("Run: bash ../scripts/download_wikipedia.sh")
        sys.exit(1)

    if not os.environ.get("DATABASE_URL"):
        logger.error(
            "DATABASE_URL not set. Copy .env.example to .env and configure it."
        )
        sys.exit(1)

    skip = args.skip
    if skip is None:
        skip = asyncio.run(get_resume_point())
        if skip > 0:
            logger.info(f"Auto-resuming from article {skip:,} (last DB checkpoint)")

    asyncio.run(process_articles(wiki_dir, skip=skip, limit=args.limit))


if __name__ == "__main__":
    main()
