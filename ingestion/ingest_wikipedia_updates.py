#!/usr/bin/env python3
"""
Wikipedia incremental update via the MediaWiki API.

The one-time dump seeds the full corpus. This script runs weekly to refresh
articles edited since the last run — no re-download of the 25GB dump needed.

Usage:
    python ingest_wikipedia_updates.py             # articles changed in last 7 days
    python ingest_wikipedia_updates.py --days 14   # wider window
    python ingest_wikipedia_updates.py --limit 500 # cap articles processed
"""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils import (
    chunk_text,
    embed_batch,
    cleanup_orphan_chunks,
    get_db_connection,
    create_job,
    finish_job,
    update_job_progress,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
BATCH_SIZE = 32       # chunks per embed call
TITLES_PER_REQUEST = 20  # Wikipedia API max for extracts
RATE_DELAY = 0.5      # seconds between API calls (polite)


async def get_changed_titles(client: httpx.AsyncClient, since: datetime) -> list[str]:
    """Return titles of main-namespace articles edited since `since`."""
    titles: list[str] = []
    rccontinue = None

    while True:
        params = {
            "action": "query",
            "list": "recentchanges",
            "rclimit": "500",
            "rctype": "edit|new",
            "rcnamespace": "0",
            "rcprop": "title|timestamp",
            "rcstart": datetime.now(timezone.utc).isoformat(),
            "rcend": since.isoformat(),
            "rcdir": "older",
            "format": "json",
        }
        if rccontinue:
            params["rccontinue"] = rccontinue

        try:
            r = await client.get(WIKI_API, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error(f"recentchanges API error: {e}")
            break

        for rc in data.get("query", {}).get("recentchanges", []):
            titles.append(rc["title"])

        cont = data.get("continue", {})
        rccontinue = cont.get("rccontinue")
        if not rccontinue:
            break

        await asyncio.sleep(RATE_DELAY)

    # Deduplicate (same article may appear multiple times in recentchanges)
    return list(dict.fromkeys(titles))


async def fetch_article_texts(client: httpx.AsyncClient, titles: list[str]) -> list[dict]:
    """Fetch plain-text extracts for a batch of titles via the extracts API."""
    params = {
        "action": "query",
        "prop": "extracts|info",
        "titles": "|".join(titles),
        "explaintext": "true",
        "exsectionformat": "plain",
        "inprop": "url",
        "format": "json",
    }
    try:
        r = await client.get(WIKI_API, params=params, timeout=30)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
    except Exception as e:
        logger.error(f"extracts API error: {e}")
        return []

    articles = []
    for page in pages.values():
        if page.get("ns", 0) != 0:
            continue
        text = page.get("extract", "")
        if not text or len(text) < 200:
            continue
        articles.append({
            "title": page.get("title", ""),
            "url": page.get("fullurl", f"https://en.wikipedia.org/wiki/{page.get('title', '').replace(' ', '_')}"),
            "wiki_id": str(page.get("pageid", "")),
            "text": text,
        })
    return articles


async def process_articles(articles: list[dict], conn) -> int:
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
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO knowledge_chunks
                            (source, source_id, chunk_index, content, metadata, embedding, embedding_model)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source, source_id, chunk_index)
                         DO UPDATE SET
                            content         = EXCLUDED.content,
                            embedding       = EXCLUDED.embedding,
                            embedding_model = EXCLUDED.embedding_model,
                            updated_at      = NOW()
                        """,
                         ("wikipedia", meta["source_id"], meta["chunk_idx"],
                          text, __import__("psycopg").types.json.Jsonb(meta["metadata"]), emb, "mxbai-embed-large"),
                     )
                    total_inserted += 1
            except Exception as e:
                logger.error(f"Insert error for {meta['source_id']}: {e}")
        pending_texts.clear()
        pending_meta.clear()

    for article in articles:
        chunks = chunk_text(article["text"])
        source_id = article["title"]

        for idx, chunk in enumerate(chunks):
            pending_texts.append(chunk)
            pending_meta.append({
                "source_id": source_id,
                "chunk_idx": idx,
                "metadata": {"url": article["url"], "wiki_id": article["wiki_id"]},
            })
            if len(pending_texts) >= BATCH_SIZE:
                await flush()

        if chunks:
            await cleanup_orphan_chunks(conn, "wikipedia", source_id, len(chunks))

    await flush()
    return total_inserted


async def main_async(days: int, limit: int | None):
    conn = None
    job_id = None
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        logger.info(f"Fetching Wikipedia articles changed since {since.date()} ({days} days)…")

        async with httpx.AsyncClient(
            headers={"User-Agent": "pi-agent/1.0 (https://github.com/pi-agent; research bot)"},
        ) as client:
            titles = await get_changed_titles(client, since)
            if limit:
                titles = titles[:limit]

            logger.info(f"Found {len(titles)} changed articles")

            conn = await get_db_connection()
            job_id = await create_job(conn, "wikipedia_update", len(titles))
            await conn.commit()

            total_inserted = 0
            for i in range(0, len(titles), TITLES_PER_REQUEST):
                batch_titles = titles[i:i + TITLES_PER_REQUEST]
                articles = await fetch_article_texts(client, batch_titles)
                n = await process_articles(articles, conn)
                total_inserted += n

                await update_job_progress(conn, job_id, i + len(batch_titles))
                await conn.commit()

                if i % 200 == 0 and i > 0:
                    logger.info(f"Progress: {i}/{len(titles)} titles, {total_inserted} chunks upserted")

                await asyncio.sleep(RATE_DELAY)

        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(f"Done: {total_inserted} chunks upserted across {len(titles)} changed articles")
    except Exception as exc:
        logger.error(f"wikipedia_update ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Incremental Wikipedia update via API")
    parser.add_argument("--days", type=int, default=7, help="Look back N days for changes")
    parser.add_argument("--limit", type=int, default=None, help="Cap articles processed (for testing)")
    args = parser.parse_args()
    asyncio.run(main_async(args.days, args.limit))


if __name__ == "__main__":
    main()
