#!/usr/bin/env python3
"""
News/current events ingestion via RSS feeds.

Fetches recent articles from curated RSS feeds across general news, tech,
and science. Attempts to extract full article body; falls back to summary.
Safe to run repeatedly — duplicate URLs are skipped via ON CONFLICT.

Usage:
    python ingest_news.py                        # all feeds, last 7 days
    python ingest_news.py --days 1               # just today
    python ingest_news.py --feeds tech science   # specific categories
"""

import argparse
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils import (
    chunk_text,
    compute_content_hash,
    embed_batch,
    upsert_chunks,
    get_db_connection,
    create_job,
    finish_job,
    update_job_progress,
    write_to_dlq,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 16  # smaller than arXiv — articles are longer
RATE_DELAY = 1.0  # seconds between API calls (news sources vary)

FEEDS: dict[str, list[dict]] = {
    "general": [
        {"name": "NYT Homepage",               "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"},
        {"name": "NYT World",                  "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"},
        {"name": "BBC Top Stories",            "url": "https://feeds.bbci.co.uk/news/rss.xml"},
        {"name": "BBC World",                  "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
        {"name": "BBC Europe",                 "url": "https://feeds.bbci.co.uk/news/world/europe/rss.xml"},
        {"name": "BBC US & Canada",            "url": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"},
        {"name": "BBC Asia",                   "url": "https://feeds.bbci.co.uk/news/world/asia/rss.xml"},
        {"name": "CBS News",                   "url": "https://www.cbsnews.com/latest/rss/main"},
        {"name": "The Guardian World",         "url": "https://www.theguardian.com/world/rss"},
        {"name": "The Guardian UK",            "url": "https://www.theguardian.com/uk/rss"},
        {"name": "The Guardian US",            "url": "https://www.theguardian.com/us-news/rss"},
        {"name": "Al Jazeera",                 "url": "https://www.aljazeera.com/xml/rss/all.xml"},
        {"name": "NPR News",                   "url": "https://feeds.npr.org/1001/rss.xml"},
        {"name": "PBS NewsHour",               "url": "https://www.pbs.org/newshour/feeds/rss/headlines"},
        {"name": "The Conversation",           "url": "https://theconversation.com/global/articles.atom"},
        {"name": "DW News",                    "url": "https://rss.dw.com/xml/rss-en-all"},
        {"name": "RFI English",                "url": "https://www.rfi.fr/en/rss"},
        {"name": "France 24",                  "url": "https://www.france24.com/en/rss"},
        {"name": "Euronews",                   "url": "https://feeds.feedburner.com/euronews/en/news/"},
    ],
    "tech": [
        {"name": "Hacker News",                "url": "https://hnrss.org/frontpage"},
        {"name": "Ars Technica",               "url": "https://feeds.arstechnica.com/arstechnica/index"},
        {"name": "Ars Technica AI",            "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
        {"name": "The Verge",                  "url": "https://www.theverge.com/rss/index.xml"},
        {"name": "TechCrunch",                 "url": "https://techcrunch.com/feed/"},
        {"name": "TechCrunch AI",              "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"name": "The Register",               "url": "https://www.theregister.com/headlines.atom"},
        {"name": "Slashdot",                   "url": "https://rss.slashdot.org/Slashdot/slashdotMain"},
        {"name": "Phoronix",                   "url": "https://www.phoronix.com/rss.php"},
        {"name": "InfoQ",                      "url": "https://feed.infoq.com/"},
        {"name": "LWN.net",                    "url": "https://lwn.net/headlines/rss"},
        {"name": "BBC Technology",             "url": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
        {"name": "ZDNet",                      "url": "https://www.zdnet.com/news/rss.xml"},
    ],
    "science": [
        {"name": "Nature News",                "url": "https://www.nature.com/nature.rss"},
        {"name": "Science Daily",              "url": "https://www.sciencedaily.com/rss/all.xml"},
        {"name": "Phys.org",                   "url": "https://phys.org/rss-feed/"},
        {"name": "Popular Science",            "url": "https://www.popsci.com/feed/"},
        {"name": "PLOS Biology",               "url": "https://journals.plos.org/plosbiology/feed/atom"},
        {"name": "PLOS ONE",                   "url": "https://journals.plos.org/plosone/feed/atom"},
        {"name": "BBC Science",                "url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"},
        {"name": "BBC Health",                 "url": "https://feeds.bbci.co.uk/news/health/rss.xml"},
        {"name": "arXiv CS (new)",             "url": "https://rss.arxiv.org/rss/cs"},
        {"name": "arXiv q-bio (new)",          "url": "https://rss.arxiv.org/rss/q-bio"},
        {"name": "arXiv physics (new)",        "url": "https://rss.arxiv.org/rss/physics"},
        {"name": "NASA News",                  "url": "https://www.nasa.gov/news-release/feed/"},
        {"name": "New Scientist",              "url": "https://www.newscientist.com/feed/home/"},
    ],
    "finance": [
        {"name": "Yahoo Finance",              "url": "https://finance.yahoo.com/news/rssindex"},
        {"name": "Yahoo Finance Top Stories",  "url": "https://finance.yahoo.com/rss/topfinstories"},
        {"name": "MarketWatch",                "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"},
        {"name": "BBC Business",               "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
        {"name": "The Guardian Business",      "url": "https://www.theguardian.com/uk/business/rss"},
    ],
    "policy": [
        {"name": "UN News",                    "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml"},
        {"name": "BBC Politics",               "url": "https://feeds.bbci.co.uk/news/politics/rss.xml"},
        {"name": "The Conversation",           "url": "https://theconversation.com/global/articles.atom"},
        {"name": "NYT Politics",               "url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"},
        {"name": "The Guardian Politics",      "url": "https://www.theguardian.com/politics/rss"},
    ],
}

# Tags to strip when extracting text from HTML
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(date_str.rstrip("Z")).replace(tzinfo=timezone.utc)
        except Exception:
            return None


async def fetch_feed(client: httpx.AsyncClient, feed: dict) -> list[dict]:
    """Fetch and parse an RSS/Atom feed, return list of article dicts."""
    try:
        r = await client.get(feed["url"], follow_redirects=True, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"Feed fetch failed [{feed['name']}]: {e}")
        return []
    await asyncio.sleep(RATE_DELAY)

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        logger.warning(f"Feed parse failed [{feed['name']}]: {e}")
        return []

    articles = []

    # RSS 2.0
    for item in root.findall(".//item"):
        title   = (item.findtext("title") or "").strip()
        url     = (item.findtext("link") or item.findtext("guid") or "").strip()
        summary = _strip_html(item.findtext("description") or "")
        pubdate = _parse_date(item.findtext("pubDate"))
        if url:
            articles.append({"title": title, "url": url, "summary": summary, "published": pubdate, "feed": feed["name"]})

    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title   = (entry.findtext("atom:title", "", ns) or "").strip()
        url     = ""
        for link in entry.findall("atom:link", ns):
            if link.attrib.get("rel", "alternate") == "alternate":
                url = link.attrib.get("href", "")
                break
        if not url:
            url = entry.findtext("atom:id", "", ns) or ""
        summary = _strip_html(entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns) or "")
        pubdate = _parse_date(entry.findtext("atom:published", "", ns) or entry.findtext("atom:updated", "", ns))
        if url:
            articles.append({"title": title, "url": url, "summary": summary, "published": pubdate, "feed": feed["name"]})

    return articles


async def fetch_full_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Try to extract main article text from URL. Returns None on failure."""
    try:
        r = await client.get(url, follow_redirects=True, timeout=15)
        r.raise_for_status()
        # Very simple extraction: grab all <p> text
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", r.text, re.DOTALL | re.IGNORECASE)
        text = " ".join(_strip_html(p) for p in paragraphs if len(p) > 80)
        return text if len(text) > 200 else None
    except Exception:
        return None
    await asyncio.sleep(RATE_DELAY)


async def ingest_articles(
    articles: list[dict],
    conn,
    job_id: int,
    fetch_full: bool,
    total_inserted: list[int],
):
    pending_texts: list[str] = []
    pending_meta: list[dict] = []

    async def flush():
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
                content_hashes=[compute_content_hash(text)],
            )
            total_inserted[0] += n
        pending_texts.clear()
        pending_meta.clear()

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; pi-agent/1.0)"},
        timeout=15,
    ) as client:
        for article in articles:
            text = None
            if fetch_full:
                text = await fetch_full_text(client, article["url"])
            if not text:
                text = f"{article['title']}\n\n{article['summary']}"
            if len(text) < 50:
                continue

            meta = {
                "title":     article["title"],
                "url":       article["url"],
                "feed":      article["feed"],
                "published": article["published"].isoformat() if article["published"] else "",
            }
            pending_texts.append(text)
            pending_meta.append({**meta, "url": article["url"]})

            if len(pending_texts) >= BATCH_SIZE:
                await flush()

    await flush()


async def main_async(categories: list[str], days: int, fetch_full: bool):
    conn = None
    job_id = None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        selected_feeds = []
        for cat in categories:
            selected_feeds.extend(FEEDS.get(cat, []))

        logger.info(f"Fetching {len(selected_feeds)} feeds (last {days} days)…")

        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (compatible; pi-agent/1.0)"},
            timeout=20,
        ) as client:
            feed_results = await asyncio.gather(
                *[fetch_feed(client, f) for f in selected_feeds],
                return_exceptions=True,
            )

        all_articles: list[dict] = []
        for result in feed_results:
            if isinstance(result, list):
                for a in result:
                    if a["published"] is None or a["published"] >= cutoff:
                        all_articles.append(a)

        # Deduplicate by URL
        seen: set[str] = set()
        unique = []
        for a in all_articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                unique.append(a)

        logger.info(f"Found {len(unique)} unique articles after dedup")

        conn = await get_db_connection()
        job_id = await create_job(conn, "news", len(unique))
        await conn.commit()

        total_inserted = [0]
        await ingest_articles(unique, conn, job_id, fetch_full, total_inserted)

        await update_job_progress(conn, job_id, len(unique))
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again

        logger.info(f"News ingestion complete: {total_inserted[0]} new chunks from {len(unique)} articles")
    except Exception as exc:
        logger.error(f"news ingestion failed: {exc}", exc_info=True)
        if conn is not None and job_id is not None:
            try:
                await write_to_dlq(conn, source="news", source_id=f"job:{job_id}",
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


def main():
    all_cats = list(FEEDS.keys())
    parser = argparse.ArgumentParser(description="Ingest news via RSS into pgvector")
    parser.add_argument("--feeds", nargs="+", default=all_cats,
                        choices=all_cats,
                        help=f"Feed categories to ingest: {all_cats}")
    parser.add_argument("--days", type=int, default=7,
                        help="Only ingest articles published in the last N days")
    parser.add_argument("--full-text", action="store_true",
                        help="Attempt to fetch full article body (slower, may hit paywalls)")
    args = parser.parse_args()
    asyncio.run(main_async(args.feeds, args.days, args.full_text))


if __name__ == "__main__":
    main()
