#!/usr/bin/env python3
"""
bioRxiv/medRxiv ingestion pipeline.

Fetches preprints from the bioRxiv/medRxiv API by date range and category.
Abstracts are embedded and stored — DO NOTHING on conflict (preprints are immutable).

Usage:
    python ingest_biorxiv.py                          # last 7 days, default categories
    python ingest_biorxiv.py --days 30                # last 30 days
    python ingest_biorxiv.py --server medrxiv         # medRxiv instead
    python ingest_biorxiv.py --categories neuroscience bioinformatics
"""

import argparse
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils import (
    compute_content_hash,
    embed_batch,
    upsert_chunks,
    get_db_connection,
    create_job,
    finish_job,
    update_job_progress,
    save_raw,
    iter_raw,
    latest_raw,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_BASE = "https://api.biorxiv.org/details"
BATCH_SIZE = 32
PAGE_SIZE = 100  # API max per request

DEFAULT_CATEGORIES = [
    "neuroscience",
    "bioinformatics",
    "genomics",
    "cancer-biology",
    "immunology",
    "microbiology",
    "cell-biology",
    "evolutionary-biology",
]


async def fetch_page(client: httpx.AsyncClient, server: str, start_date: str, end_date: str, cursor: int) -> dict:
    url = f"{API_BASE}/{server}/{start_date}/{end_date}/{cursor}/json"
    r = await client.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


async def fetch_all_papers(server: str, days: int) -> list[dict]:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=days)
    start_date = start.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")

    # Probe cursor-0; if the API errors or returns empty (future dates),
    # shift the window back in 30-day increments until we find valid data.
    async with httpx.AsyncClient(timeout=30) as _probe:
        shift = 0
        for attempt in range(12):
            try:
                data = await fetch_page(_probe, server, start_date, end_date, 0)
                collection = data.get("collection", [])
                total = int(data.get("messages", [{}])[0].get("total", 0))
                if collection or total > 0:
                    if shift > 0:
                        logger.warning(
                            f"bioRxiv API returned empty/future date range; "
                            f"shifted back {shift} days"
                        )
                    break
                # Empty result — likely future dates, shift back
                raise ValueError(f"Empty result for {start_date}–{end_date}")
            except Exception:
                shift += 30
                end = end - timedelta(days=30)
                start = start - timedelta(days=30)
                start_date = start.strftime("%Y-%m-%d")
                end_date = end.strftime("%Y-%m-%d")
        else:
            logger.error("Could not find a valid bioRxiv date range after 12 attempts")
            return []

    logger.info(f"Fetching {server} papers from {start_date} to {end_date}")

    all_papers: list[dict] = []
    cursor = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                data = await fetch_page(client, server, start_date, end_date, cursor)
            except Exception as e:
                logger.error(f"API error at cursor {cursor}: {e}")
                break

            collection = data.get("collection", [])
            if not collection:
                break

            all_papers.extend(collection)
            total = int(data.get("messages", [{}])[0].get("total", 0))
            cursor += len(collection)

            logger.info(f"  Fetched {cursor}/{total} papers")

            if cursor >= total:
                break

            await asyncio.sleep(0.5)  # polite rate limiting

    return all_papers


def filter_by_category(papers: list[dict], categories: list[str]) -> list[dict]:
    if not categories:
        return papers
    cat_set = {c.lower() for c in categories}
    return [p for p in papers if (p.get("category") or "").lower() in cat_set]


async def ingest_papers(papers: list[dict], conn, source: str, job_id: int) -> int:
    pending_texts: list[str] = []
    pending_papers: list[dict] = []
    total_inserted = 0

    async def flush():
        nonlocal total_inserted
        if not pending_texts:
            return
        embeddings = await embed_batch(pending_texts)
        if embeddings is None:
            logger.error("Embedding failed, skipping batch.")
            pending_texts.clear()
            pending_papers.clear()
            return
        for paper, emb in zip(pending_papers, embeddings):
            text = f"{paper['title']}\n\n{paper['abstract']}"
            n = await upsert_chunks(
                conn,
                source=source,
                source_id=paper["doi"],
                chunks=[text],
                embeddings=[emb],
                metadata={
                    "title":      paper["title"],
                    "doi":        paper["doi"],
                    "authors":    paper.get("authors", ""),
                    "category":   paper.get("category", ""),
                    "published":  paper.get("date", ""),
                    "url":        f"https://www.{source}.org/content/{paper['doi']}",
                    "server":     source,
                },
                on_conflict="nothing",
                content_hashes=[compute_content_hash(text)],
            )
            total_inserted += n
        pending_texts.clear()
        pending_papers.clear()

    for paper in papers:
        if not paper.get("doi") or not paper.get("abstract"):
            continue
        text = f"{paper['title']}\n\n{paper['abstract']}"
        pending_texts.append(text)
        pending_papers.append(paper)
        if len(pending_texts) >= BATCH_SIZE:
            await flush()

    await flush()
    return total_inserted


async def main_async(
    server: str, days: int, categories: list[str],
    limit: int | None, from_raw: Path | None,
):
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            papers = list(iter_raw(from_raw))
        else:
            papers = await fetch_all_papers(server, days)
            save_raw(papers, server)

        if categories:
            papers = filter_by_category(papers, categories)
            logger.info(f"After category filter: {len(papers)} papers in {categories}")
        if limit:
            papers = papers[:limit]

        logger.info(f"Ingesting {len(papers)} {server} papers…")
        conn = await get_db_connection()
        job_id = await create_job(conn, server, len(papers))
        await conn.commit()

        t0 = time.time()
        inserted = await ingest_papers(papers, conn, server, job_id)
        await update_job_progress(conn, job_id, len(papers))
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again

        elapsed = time.time() - t0
        logger.info(f"{server} ingestion complete: {inserted} new chunks from {len(papers)} papers in {elapsed:.0f}s")
    except Exception as exc:
        logger.error(f"{server} ingestion failed: {exc}", exc_info=True)
        if conn is not None and job_id is not None:
            try:
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


def main():
    parser = argparse.ArgumentParser(description="Ingest bioRxiv/medRxiv preprints into pgvector")
    parser.add_argument("--server", default="biorxiv", choices=["biorxiv", "medrxiv"])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--categories", nargs="+", default=DEFAULT_CATEGORIES)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--from-raw", metavar="PATH", default=None,
        help="Replay from a saved JSONL.gz file. Omit path to use the latest saved file.",
    )
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw(args.server)
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(args.server, args.days, args.categories, args.limit, raw_path))


if __name__ == "__main__":
    main()
