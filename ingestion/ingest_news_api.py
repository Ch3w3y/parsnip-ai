#!/usr/bin/env python3
"""
News ingestion via NewsAPI.org.

Queries top-headlines by category and topic searches via /v2/everything.
Free tier: 100 requests/day — each run uses ~20 requests.

Usage:
    python ingest_news_api.py               # all categories + topics, last 7 days
    python ingest_news_api.py --days 1      # last 24 hours only
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils import (
    chunk_text,
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

NEWSAPI_KEY = os.environ.get("NEWS_API_KEY", "")
NEWSAPI_BASE = "https://newsapi.org/v2"
BATCH_SIZE = 32
_rate_sem = asyncio.Semaphore(2)  # max 2 concurrent requests to avoid 429

# Top-headlines categories (one request each)
HEADLINE_CATEGORIES = ["business", "general", "health", "science", "technology"]

# Topic searches via /v2/everything (one request each)
TOPIC_SEARCHES = [
    "artificial intelligence OR machine learning",
    "climate change OR global warming",
    "geopolitics OR foreign policy OR international relations",
    "inflation OR interest rates OR central bank",
    "cybersecurity OR data breach OR hacking",
    "space exploration OR NASA OR ESA",
    "biotech OR genomics OR CRISPR",
    "quantum computing OR quantum physics",
    "renewable energy OR solar OR wind power",
    "cryptocurrency OR blockchain OR bitcoin",
    "Ukraine OR Russia OR NATO",
    "China economy OR Taiwan OR South China Sea",
    "Middle East OR Gaza OR Israel",
    "pandemic OR WHO OR disease outbreak",
    "nuclear energy OR fusion power",
]


async def fetch_headlines(client: httpx.AsyncClient, category: str, from_date: str) -> list[dict]:
    async with _rate_sem:
        await asyncio.sleep(0.5)
        try:
            r = await client.get(
                f"{NEWSAPI_BASE}/top-headlines",
                params={
                    "category": category,
                    "language": "en",
                    "pageSize": 100,
                    "apiKey": NEWSAPI_KEY,
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                logger.warning(f"NewsAPI error for category {category}: {data.get('message')}")
                return []
            return data.get("articles", [])
        except Exception as e:
            logger.warning(f"Failed to fetch headlines [{category}]: {e}")
            return []


async def fetch_topic(client: httpx.AsyncClient, query: str, from_date: str) -> list[dict]:
    async with _rate_sem:
        await asyncio.sleep(0.5)
        try:
            r = await client.get(
                f"{NEWSAPI_BASE}/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 100,
                    "from": from_date,
                    "apiKey": NEWSAPI_KEY,
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                logger.warning(f"NewsAPI error for query '{query}': {data.get('message')}")
                return []
            return data.get("articles", [])
        except Exception as e:
            logger.warning(f"Failed to fetch topic '{query}': {e}")
            return []


def article_to_text(article: dict) -> str:
    parts = [article.get("title") or ""]
    if article.get("description"):
        parts.append(article["description"])
    if article.get("content"):
        # NewsAPI truncates content at 200 chars with "[+N chars]" — strip that
        content = article["content"].split("[+")[0].strip()
        if content:
            parts.append(content)
    return "\n\n".join(p for p in parts if p)


async def ingest_articles(articles: list[dict], conn) -> int:
    pending_texts: list[str] = []
    pending_meta: list[dict] = []
    total_inserted = 0

    async def flush():
        nonlocal total_inserted
        if not pending_texts:
            return
        embeddings = await embed_batch(pending_texts)
        if embeddings is None:
            logger.error("Embedding failed, skipping batch.")
            pending_texts.clear()
            pending_meta.clear()
            return
        for text, emb, meta in zip(pending_texts, embeddings, pending_meta):
            n = await upsert_chunks(
                conn,
                source="news",
                source_id=meta["url"],
                chunks=[text],
                embeddings=[emb],
                metadata=meta,
                on_conflict="nothing",
            )
            total_inserted += n
        pending_texts.clear()
        pending_meta.clear()

    for article in articles:
        url = article.get("url", "")
        if not url or url == "https://removed.com":
            continue

        published_str = article.get("publishedAt", "")

        text = article_to_text(article)
        if len(text) < 50:
            continue

        meta = {
            "title":     article.get("title", ""),
            "url":       url,
            "source":    (article.get("source") or {}).get("name", ""),
            "published": published_str,
        }
        pending_texts.append(text)
        pending_meta.append(meta)

        if len(pending_texts) >= BATCH_SIZE:
            await flush()

    await flush()
    return total_inserted


async def fetch_all_articles(days: int) -> list[dict]:
    """Fetch all articles from NewsAPI — pure API, no DB or embedding."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    from_date = cutoff.strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=30) as client:
        headline_tasks = [fetch_headlines(client, cat, from_date) for cat in HEADLINE_CATEGORIES]
        topic_tasks    = [fetch_topic(client, q, from_date) for q in TOPIC_SEARCHES]
        logger.info(f"Fetching {len(HEADLINE_CATEGORIES)} headline categories + {len(TOPIC_SEARCHES)} topic searches…")
        results = await asyncio.gather(*headline_tasks, *topic_tasks)

    seen: set[str] = set()
    unique = []
    for batch in results:
        if not isinstance(batch, list):
            continue
        for a in batch:
            url = a.get("url", "")
            if url and url not in seen and url != "https://removed.com":
                seen.add(url)
                unique.append(a)
    return unique


async def main_async(days: int, from_raw: Path | None):
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            articles = list(iter_raw(from_raw))
        else:
            if not NEWSAPI_KEY:
                logger.error("NEWS_API_KEY not set in environment.")
                return
            articles = await fetch_all_articles(days)
            save_raw(articles, "news")

        logger.info(f"Processing {len(articles)} unique articles…")
        conn = await get_db_connection()
        job_id = await create_job(conn, "news", len(articles))
        await conn.commit()

        inserted = await ingest_articles(articles, conn)
        await update_job_progress(conn, job_id, len(articles))
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(f"NewsAPI ingestion complete: {inserted} new chunks from {len(articles)} articles")
    except Exception as exc:
        logger.error(f"news (api) ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Ingest news via NewsAPI.org")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--from-raw", metavar="PATH", default=None,
        help="Replay from a saved JSONL.gz file. Omit path to use the latest saved file.",
    )
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("news")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(args.days, raw_path))


if __name__ == "__main__":
    main()
