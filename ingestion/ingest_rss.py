#!/usr/bin/env python3
"""
Generic RSS feed ingestion: parse RSS/Atom feeds, extract articles, chunk and embed.

Feed URLs are read from the RSS_FEEDS environment variable (comma-separated) or
from a JSON file at ingestion/data/rss_feeds.json.

Usage:
    python ingest_rss.py
    python ingest_rss.py --feeds "https://example.com/feed.xml" "https://other.com/rss"
    python ingest_rss.py --feed-file /path/to/feeds.json
    python ingest_rss.py --from-raw
    python ingest_rss.py --from-raw path/to/file.jsonl.gz
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from dotenv import load_dotenv

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

BATCH_SIZE = 32
DEFAULT_FEEDS_FILE = Path(__file__).parent / "data" / "rss_feeds.json"
RATE_DELAY = 1.0  # seconds between API calls (RSS feeds can be slow)

DEFAULT_FEEDS = [
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.LG",
    "https://www.nature.com/nature.rss",
    "https://hnrss.org/frontpage",
]

ATOM_NS = "http://www.w3.org/2005/Atom"
RSS_2_0_NS = ""


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_rss_feed(xml_content: str, feed_url: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed and return list of article dicts."""
    articles = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"Failed to parse feed {feed_url}: {e}")
        return []

    tag = root.tag.lower()

    if "feed" in tag or root.tag == f"{{{ATOM_NS}}}feed":
        articles = _parse_atom(root, feed_url)
    elif "rss" in tag or root.tag == "rss":
        articles = _parse_rss2(root, feed_url)
    else:
        channel = root.find("channel")
        if channel is not None:
            articles = _parse_rss2_items(channel, feed_url)

    return articles


def _parse_atom(root: ET.Element, feed_url: str) -> list[dict]:
    articles = []
    ns = ATOM_NS if root.tag == f"{{{ATOM_NS}}}feed" else ""
    prefix = f"{{{ns}}}" if ns else ""

    for entry in root.findall(f"{prefix}entry"):
        title_elem = entry.find(f"{prefix}title")
        title = (title_elem.text or "").strip() if title_elem is not None else ""
        if not title:
            continue

        content_elem = entry.find(f"{prefix}content")
        summary_elem = entry.find(f"{prefix}summary")

        content = ""
        if content_elem is not None and content_elem.text:
            content = strip_html(content_elem.text)
        elif summary_elem is not None and summary_elem.text:
            content = strip_html(summary_elem.text)

        link = ""
        for link_elem in entry.findall(f"{prefix}link"):
            rel = link_elem.get("rel", "alternate")
            if rel == "alternate":
                link = link_elem.get("href", "")
                break
        if not link:
            link_elem = entry.find(f"{prefix}link")
            if link_elem is not None:
                link = link_elem.get("href", "")

        published = ""
        pub_elem = entry.find(f"{prefix}published") or entry.find(f"{prefix}updated")
        if pub_elem is not None and pub_elem.text:
            published = pub_elem.text[:10]

        articles.append(
            {
                "title": title,
                "content": content,
                "link": link,
                "published": published,
                "feed_url": feed_url,
                "source": "rss",
            }
        )

    return articles


def _parse_rss2(root: ET.Element, feed_url: str) -> list[dict]:
    channel = root.find("channel")
    if channel is None:
        return []
    return _parse_rss2_items(channel, feed_url)


def _parse_rss2_items(channel: ET.Element, feed_url: str) -> list[dict]:
    articles = []
    for item in channel.findall("item"):
        title_elem = item.find("title")
        title = (title_elem.text or "").strip() if title_elem is not None else ""
        if not title:
            continue

        desc_elem = item.find("description")
        content_elem = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")

        content = ""
        if content_elem is not None and content_elem.text:
            content = strip_html(content_elem.text)
        elif desc_elem is not None and desc_elem.text:
            content = strip_html(desc_elem.text)

        link_elem = item.find("link")
        link = (link_elem.text or "").strip() if link_elem is not None else ""

        pub_date = ""
        pub_elem = item.find("pubDate")
        if pub_elem is not None and pub_elem.text:
            pub_date = pub_elem.text[:10]

        articles.append(
            {
                "title": title,
                "content": content,
                "link": link,
                "published": pub_date,
                "feed_url": feed_url,
                "source": "rss",
            }
        )

    return articles


async def fetch_feed(url: str) -> tuple[str, str]:
    """Fetch a single feed URL and return (url, xml_content)."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return url, r.text
    except Exception as e:
        logger.warning(f"Failed to fetch feed {url}: {e}")
        return url, ""
    await asyncio.sleep(RATE_DELAY)


async def fetch_all_feeds(urls: list[str]) -> list[dict]:
    """Phase 1: Fetch all feeds — pure HTTP, no DB or embedding."""
    tasks = [fetch_feed(url) for url in urls]
    results = await asyncio.gather(*tasks)

    all_articles = []
    for url, xml in results:
        if xml:
            articles = parse_rss_feed(xml, url)
            all_articles.extend(articles)
            logger.info(f"  {url}: {len(articles)} articles")

    return all_articles


async def process_articles(articles: list[dict], conn, job_id: int) -> int:
    """Phase 2: Chunk, embed, and upsert articles."""
    rows = []
    total = 0
    source_chunk_counts: dict[str, int] = {}

    async def flush():
        nonlocal total
        if not rows:
            return
        texts = [r[3] for r in rows]
        embeddings = await embed_batch(texts)
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
        if good_rows:
            await bulk_upsert_chunks(conn, good_rows, on_conflict="update")
            total += len(good_rows)

        for sid, count in source_chunk_counts.items():
            await cleanup_orphan_chunks(conn, "rss", sid, count)
        source_chunk_counts.clear()

        rows.clear()

    for article in articles:
        title = article.get("title", "")
        content = article.get("content", "")
        if not title and not content:
            continue

        full_text = f"{title}\n\n{content}" if content else title
        chunks = chunk_text(full_text, 300, 40)

        link = article.get("link", "")
        source_id = f"rss_{hashlib.sha256((link or title).encode('utf-8'), usedforsecurity=False).hexdigest()[:16]}"
        metadata = {
            "title": title,
            "link": link,
            "published": article.get("published", ""),
            "feed_url": article.get("feed_url", ""),
        }

        for chunk_idx, chunk_body in enumerate(chunks):
            rows.append(
                (
                    "rss",
                    source_id,
                    chunk_idx,
                    chunk_body,
                    compute_content_hash(chunk_body),
                    metadata,
                    None,
                    "mxbai-embed-large",
                )
            )

            if len(rows) >= BATCH_SIZE:
                await flush()

        if chunks:
            source_chunk_counts[source_id] = len(chunks)

    await flush()
    await update_job_progress(conn, job_id, len(articles))
    return total


def load_feeds(args_feeds: list[str] | None, feed_file: str | None) -> list[str]:
    """Load feed URLs from args, env var, or file."""
    if args_feeds:
        return args_feeds

    env_feeds = os.environ.get("RSS_FEEDS", "")
    if env_feeds:
        return [f.strip() for f in env_feeds.split(",") if f.strip()]

    path = Path(feed_file) if feed_file else DEFAULT_FEEDS_FILE
    if path.exists():
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "feeds" in data:
                return data["feeds"]

    return DEFAULT_FEEDS


async def main_async(feeds: list[str], feed_file: str | None, from_raw: Path | None):
    conn = None
    job_id = None
    try:
        feed_urls = load_feeds(feeds, feed_file)
        logger.info(f"Loading {len(feed_urls)} RSS feeds…")

        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            articles = list(iter_raw(from_raw))
        else:
            articles = await fetch_all_feeds(feed_urls)
            save_raw(articles, "rss")

        logger.info(f"Processing {len(articles)} RSS articles…")
        conn = await get_db_connection()
        job_id = await create_job(conn, "rss", len(articles))
        await conn.commit()

        total = await process_articles(articles, conn, job_id)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(f"RSS ingestion complete: {total} chunks from {len(articles)} articles")
    except Exception as exc:
        logger.error(f"rss ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Ingest RSS feeds into pgvector")
    parser.add_argument("--feeds", nargs="+", default=None)
    parser.add_argument("--feed-file", default=None)
    parser.add_argument(
        "--from-raw",
        metavar="PATH",
        default=None,
        help="Replay from a saved JSONL.gz file instead of hitting the API.",
    )
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("rss")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(args.feeds, args.feed_file, raw_path))


if __name__ == "__main__":
    main()
