#!/usr/bin/env python3
"""
Social science paper ingestion via OpenAlex API (replaces SSRN scraper).

OpenAlex is a free, open bibliographic database covering economics, finance,
law, and management — including papers originally posted on SSRN.

Usage:
    python ingest_ssrn.py --categories "Economics" "Finance" "Law" --max-per-cat 100
    python ingest_ssrn.py --from-raw
    python ingest_ssrn.py --from-raw path/to/file.jsonl.gz
"""

import argparse
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils import (
    chunk_text,
    embed_batch,
    bulk_upsert_chunks,
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

OPENALEX_URL = "https://api.openalex.org/works"
BATCH_SIZE = 32

DEFAULT_CATEGORIES = [
    "Economics",
    "Finance",
    "Law",
    "Management",
    "Accounting",
    "Political Science",
]

# OpenAlex concept IDs for each category
CATEGORY_CONCEPT_MAP = {
    "Economics": "C162324750",
    "Finance": "C187736073",
    "Law": "C18903297",
    "Management": "C144133560",
    "Accounting": "C127413603",
    "Political Science": "C17744445",
}


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    words = [""] * (max(pos for positions in inverted_index.values() for pos in positions) + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words)


async def fetch_recent_papers(category: str, max_results: int) -> list[dict]:
    """Fetch recent papers for a category via OpenAlex API."""
    concept_id = CATEGORY_CONCEPT_MAP.get(category)
    if not concept_id:
        logger.warning(f"No OpenAlex concept ID for category: {category}")
        return []

    papers = []
    per_page = min(max_results, 50)
    fetched = 0

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            while fetched < max_results:
                params = {
                    "filter": f"concepts.id:{concept_id},has_abstract:true",
                    "sort": "publication_date:desc",
                    "per-page": per_page,
                    "page": fetched // per_page + 1,
                    "select": "id,title,abstract_inverted_index,publication_date,primary_location,authorships",
                    "mailto": "agent@pi-agent.local",
                }
                r = await client.get(OPENALEX_URL, params=params)
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])
                if not results:
                    break

                for work in results:
                    title = work.get("title", "")
                    if not title:
                        continue
                    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
                    if not abstract:
                        continue

                    loc = work.get("primary_location") or {}
                    link = loc.get("landing_page_url") or work.get("id", "")
                    authors = [
                        a.get("author", {}).get("display_name", "")
                        for a in (work.get("authorships") or [])[:10]
                        if a.get("author", {}).get("display_name")
                    ]
                    date_str = work.get("publication_date", "")[:10]

                    papers.append({
                        "title": title,
                        "abstract": abstract,
                        "link": link,
                        "authors": authors,
                        "date": date_str,
                        "category": category,
                        "openalex_id": work.get("id", ""),
                    })

                fetched += len(results)
                if len(results) < per_page:
                    break
                await asyncio.sleep(0.1)

    except Exception as e:
        logger.warning(f"Failed to fetch OpenAlex category '{category}': {e}")

    logger.info(f"  {category}: {len(papers)} papers")
    return papers


async def fetch_all_categories(categories: list[str], max_per_cat: int) -> list[dict]:
    """Phase 1: Fetch all papers across categories — pure HTTP, no DB or embedding."""
    all_papers: list[dict] = []
    seen_titles: set[str] = set()

    for cat in categories:
        logger.info(f"Fetching SSRN category: {cat}")
        try:
            papers = await fetch_recent_papers(cat, max_per_cat)
            for p in papers:
                title_key = p["title"].lower()
                if title_key not in seen_titles:
                    seen_titles.add(title_key)
                    all_papers.append(p)
        except Exception as e:
            logger.error(f"Failed for category '{cat}': {e}")
        await asyncio.sleep(2)

    return all_papers


async def process_papers(papers: list[dict], conn, job_id: int) -> int:
    """Phase 2: Chunk, embed, and upsert papers."""
    rows = []
    total = 0

    async def flush():
        nonlocal total
        if not rows:
            return
        texts = [r[3] for r in rows]
        embeddings = await embed_batch(texts)
        if embeddings is None:
            logger.error("Embedding failed, skipping batch.")
            rows.clear()
            return
        good_rows = [
            row[:5] + (emb, row[6])
            for row, emb in zip(rows, embeddings)
            if emb is not None
        ]
        if good_rows:
            await bulk_upsert_chunks(conn, good_rows, on_conflict="update")
            total += len(good_rows)
        rows.clear()

    for paper in papers:
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")
        if not title:
            continue

        full_text = f"{title}\n\n{abstract}" if abstract else title
        chunks = chunk_text(full_text, 300, 40)

        link = paper.get("link", "")
        openalex_id = paper.get("openalex_id", "")
        source_id = openalex_id if openalex_id else f"openalex_{hashlib.sha256(title.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]}"

        metadata = {
            "title": title,
            "link": link,
            "authors": paper.get("authors", []),
            "date": paper.get("date", ""),
            "category": paper.get("category", ""),
        }

        for chunk_idx, chunk_body in enumerate(chunks):
            rows.append(
                (
                    "ssrn",
                    source_id,
                    chunk_idx,
                    chunk_body,
                    metadata,
                    None,
                    "mxbai-embed-large",
                )
            )

            if len(rows) >= BATCH_SIZE:
                await flush()

    await flush()
    await update_job_progress(conn, job_id, len(papers))
    return total


async def main_async(categories: list[str], max_per_cat: int, from_raw: Path | None):
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            papers = list(iter_raw(from_raw))
        else:
            papers = await fetch_all_categories(categories, max_per_cat)
            save_raw(papers, "ssrn")

        logger.info(f"Processing {len(papers)} SSRN preprints…")
        conn = await get_db_connection()
        job_id = await create_job(conn, "ssrn", len(papers))
        await conn.commit()

        total = await process_papers(papers, conn, job_id)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(f"SSRN ingestion complete: {total} chunks from {len(papers)} preprints")
    except Exception as exc:
        logger.error(f"ssrn ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Ingest SSRN preprints into pgvector")
    parser.add_argument("--categories", nargs="+", default=DEFAULT_CATEGORIES)
    parser.add_argument("--max-per-cat", type=int, default=100)
    parser.add_argument(
        "--from-raw",
        metavar="PATH",
        default=None,
        help="Replay from a saved JSONL.gz file instead of hitting the API.",
    )
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("ssrn")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(args.categories, args.max_per_cat, raw_path))


if __name__ == "__main__":
    main()
