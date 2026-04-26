#!/usr/bin/env python3
"""
Hacker News ingestion: fetch top/front stories, extract content, chunk, and embed.

Usage:
    python ingest_hackernews.py                  # latest front page
    python ingest_hackernews.py --top 50         # top N stories
    python ingest_hackernews.py --best           # best stories (quality-filtered)
    python ingest_hackernews.py --from-raw       # replay from latest raw file
    python ingest_hackernews.py --from-raw path  # replay from specific file
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
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
    save_raw,
    iter_raw,
    latest_raw,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HN_API = "https://hacker-news.firebaseio.com/v0"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
BATCH_SIZE = 32
MAX_TEXT_LENGTH = 10000  # skip stories with extremely long text

# Story types to ingest
STORY_TYPES = {
    "top": f"{HN_API}/topstories.json",
    "best": f"{HN_API}/beststories.json",
    "front": f"{HN_API}/topstories.json",  # same endpoint, fewer items
}


async def fetch_story(client: httpx.AsyncClient, story_id: int) -> dict | None:
    """Fetch a single HN story by ID."""
    try:
        r = await client.get(f"{HN_API}/item/{story_id}.json", timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data or data.get("type") != "story":
            return None
        if not data.get("title"):
            return None
        return data
    except Exception as e:
        logger.debug(f"Failed to fetch story {story_id}: {e}")
        return None


async def fetch_stories(story_ids: list[int], limit: int) -> list[dict]:
    """Fetch multiple HN stories concurrently."""
    stories = []
    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch in batches to avoid overwhelming the API
        for i in tqdm(
            range(0, len(story_ids), 20), desc="Fetching HN stories", unit="batch"
        ):
            batch = story_ids[i : i + 20]
            tasks = [fetch_story(client, sid) for sid in batch]
            results = await asyncio.gather(*tasks)
            for story in results:
                if story and story.get("title"):
                    # Accept stories with text OR URL (most HN stories are links)
                    if story.get("text") or story.get("url"):
                        stories.append(story)
                if len(stories) >= limit:
                    return stories
            # Polite delay
            await asyncio.sleep(0.5)
    return stories


async def fetch_all_hn(story_type: str = "top", limit: int = 50) -> list[dict]:
    """Phase 1: Fetch HN stories — pure API, no DB or embedding."""
    url = STORY_TYPES.get(story_type, STORY_TYPES["top"])
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        r.raise_for_status()
        story_ids = r.json()

    # Limit to requested count
    story_ids = story_ids[:limit]
    logger.info(f"Fetching {len(story_ids)} {story_type} stories from HN...")
    stories = await fetch_stories(story_ids, limit)
    logger.info(f"Fetched {len(stories)} stories with text content")
    return stories


async def process_stories(stories: list[dict], conn, job_id: int) -> int:
    """Phase 2: Chunk, embed, and upsert stories."""
    rows = []
    total = 0
    source_chunk_counts: dict[str, int] = {}

    async def flush():
        nonlocal total
        if not rows:
            return
        texts = [r[3] for r in rows]
        embeddings = await embed_batch(texts, model=EMBED_MODEL)
        if embeddings is None:
            logger.error("Embedding failed, skipping batch.")
            rows.clear()
            source_chunk_counts.clear()
            return
        good_rows = [
            row[:6] + (emb,) + row[7:]
            for row, emb in zip(rows, embeddings)
            if emb is not None
        ]
        await bulk_upsert_chunks(conn, good_rows, on_conflict="update")
        total += len(good_rows)

        for sid, count in source_chunk_counts.items():
            await cleanup_orphan_chunks(conn, "hackernews", sid, count)
        source_chunk_counts.clear()

        rows.clear()

    for story in stories:
        story_id = story.get("id")
        title = story.get("title", "")
        text = story.get("text", "")
        url = story.get("url", "")
        score = story.get("score", 0)
        by = story.get("by", "")
        timestamp = story.get("time", 0)
        date_str = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
            if timestamp
            else ""
        )

        # Build content: title + text + metadata
        content_parts = [f"Title: {title}"]
        if url:
            content_parts.append(f"URL: {url}")
        if by:
            content_parts.append(f"Author: {by}")
        if score:
            content_parts.append(f"Score: {score}")
        if date_str:
            content_parts.append(f"Date: {date_str}")
        if text:
            # Strip HTML tags from HN text
            import re

            clean_text = re.sub(r"<[^>]+>", "", text)
            content_parts.append(f"\n{clean_text}")
        elif url:
            content_parts.append(f"\nLink submission: {url}")

        full_content = "\n".join(content_parts)
        chunks = chunk_text(full_content, 300, 40)

        source_id = f"hn_{story_id}"
        metadata = {
            "hn_id": story_id,
            "title": title,
            "url": url,
            "author": by,
            "score": score,
            "date": date_str,
            "source": "hackernews",
        }

        for chunk_idx, chunk_body in enumerate(chunks):
            rows.append(
                (
                    "hackernews",
                    source_id,
                    chunk_idx,
                    chunk_body,
                    compute_content_hash(chunk_body),
                    metadata,
                    None,
                    EMBED_MODEL,
                )
            )

            if len(rows) >= BATCH_SIZE:
                await flush()

        source_chunk_counts[source_id] = len(chunks)

    await flush()
    await update_job_progress(conn, job_id, len(stories))
    return total


async def main_async(story_type: str, limit: int, from_raw: Path | None):
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            stories = list(iter_raw(from_raw))
        else:
            stories = await fetch_all_hn(story_type, limit)
            save_raw(stories, "hackernews")

        logger.info(f"Processing {len(stories)} HN stories...")
        conn = await get_db_connection()
        job_id = await create_job(conn, "hackernews", len(stories))
        await conn.commit()

        total = await process_stories(stories, conn, job_id)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(
            f"Hacker News ingestion complete: {total} chunks from {len(stories)} stories"
        )
    except Exception as exc:
        logger.error(f"hackernews ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Ingest Hacker News into pgvector")
    parser.add_argument("--top", type=int, default=0, help="Fetch top N stories")
    parser.add_argument("--best", action="store_true", help="Fetch best stories")
    parser.add_argument(
        "--from-raw",
        metavar="PATH",
        default=None,
        help="Replay from a saved JSONL.gz file instead of hitting the API.",
    )
    args = parser.parse_args()

    story_type = "best" if args.best else "top"
    limit = args.top if args.top > 0 else 30

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("hackernews")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(story_type, limit, raw_path))


if __name__ == "__main__":
    main()
