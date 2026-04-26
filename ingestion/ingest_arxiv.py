#!/usr/bin/env python3
"""
arXiv ingestion: search by category and embed abstracts + titles into pgvector.

This is lightweight — arXiv abstracts are short, so ingestion is fast.
Run this periodically to keep the knowledge base current.

Usage:
    python ingest_arxiv.py --categories cs.AI cs.LG q-bio --max-per-cat 500
"""

import argparse
import asyncio
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from dotenv import load_dotenv

from structured_logging import configure_basic_logging, get_ingestion_logger, set_correlation_id
from tracing import get_tracer, set_span_error
from lineage import emit_dlq_lineage, emit_job_lineage
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
    save_raw,
    iter_raw,
    latest_raw,
    start_metrics_server,
    INGESTION_CHUNKS_PROCESSED,
    INGESTION_JOBS_FAILED,
    INGESTION_DLQ_MESSAGES,
)

load_dotenv(Path(__file__).parent.parent / ".env")

configure_basic_logging("arxiv")
logger = get_ingestion_logger("arxiv")
_tracer = get_tracer("parsnip.ingestion.arxiv")

ARXIV_API = "https://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}
BATCH_SIZE = 32
FETCH_BATCH = 100   # papers per API call (arXiv max is 2000 but be polite)

# Default categories covering science, AI/ML, physics, biology, economics
DEFAULT_CATEGORIES = [
    "cs.AI", "cs.LG", "cs.CL", "cs.CV",
    "stat.ML",
    "physics.gen-ph",
    "q-bio.GN", "q-bio.NC",
    "econ.GN",
    "math.ST",
]


async def fetch_papers(category: str, start: int, max_results: int) -> list[dict]:
    params = {
        "search_query": f"cat:{category}",
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(ARXIV_API, params=params)
        r.raise_for_status()

    root = ET.fromstring(r.text)
    papers = []
    for entry in root.findall("atom:entry", NS):
        arxiv_id = (entry.findtext("atom:id", "", NS) or "").split("/abs/")[-1]
        title = (entry.findtext("atom:title", "", NS) or "").replace("\n", " ").strip()
        abstract = (entry.findtext("atom:summary", "", NS) or "").replace("\n", " ").strip()
        published = (entry.findtext("atom:published", "", NS) or "")[:10]
        authors = [
            a.findtext("atom:name", "", NS)
            for a in entry.findall("atom:author", NS)
        ]
        link = f"https://arxiv.org/abs/{arxiv_id}"

        if not arxiv_id or not abstract:
            continue

        papers.append({
            "id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "published": published,
            "authors": authors,
            "link": link,
            "category": category,
        })

    return papers


async def ingest_category(
    conn, category: str, max_papers: int, job_id: int
):
    inserted_total = 0
    start = 0
    pending_texts: list[str] = []
    pending_papers: list[dict] = []

    async def flush():
        nonlocal inserted_total
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
            inserted = await upsert_chunks(
                conn,
                source="arxiv",
                source_id=paper["id"],
                chunks=[text],
                embeddings=[emb],
                metadata={
                    "title": paper["title"],
                    "published": paper["published"],
                    "authors": paper["authors"],
                    "link": paper["link"],
                    "category": paper["category"],
                },
                on_conflict="nothing",
                content_hashes=[compute_content_hash(text)],
            )
            inserted_total += inserted
        pending_texts.clear()
        pending_papers.clear()

    while start < max_papers:
        batch_size = min(FETCH_BATCH, max_papers - start)
        try:
            papers = await fetch_papers(category, start, batch_size)
        except Exception as e:
            logger.error(f"Fetch failed for {category} at {start}: {e}")
            break

        if not papers:
            break

        for paper in papers:
            text = f"{paper['title']}\n\n{paper['abstract']}"
            pending_texts.append(text)
            pending_papers.append(paper)
            if len(pending_texts) >= BATCH_SIZE:
                await flush()

        start += len(papers)
        # Polite delay between arXiv API calls
        await asyncio.sleep(3)

    await flush()
    logger.info(f"  {category}: {inserted_total} new chunks inserted")
    return inserted_total


async def fetch_all_categories(categories: list[str], max_per_cat: int) -> list[dict]:
    """Fetch all papers across categories — pure API, no DB or embedding."""
    all_papers: list[dict] = []
    for cat in categories:
        logger.info(f"Fetching arXiv category: {cat}")
        start = 0
        while start < max_per_cat:
            batch_size = min(FETCH_BATCH, max_per_cat - start)
            try:
                papers = await fetch_papers(cat, start, batch_size)
            except Exception as e:
                logger.error(f"Fetch failed for {cat} at {start}: {e}")
                break
            if not papers:
                break
            all_papers.extend(papers)
            start += len(papers)
            await asyncio.sleep(3)
    return all_papers


async def process_papers(papers: list[dict], conn, job_id: int) -> int:
    """Embed and upsert a flat list of papers (used by both live and --from-raw paths)."""
    pending_texts: list[str] = []
    pending_papers: list[dict] = []
    total = 0

    async def flush():
        nonlocal total
        if not pending_texts:
            return
        embeddings = await embed_batch(pending_texts)
        if embeddings is None:
            logger.error("Embedding failed, skipping batch.")
            pending_texts.clear(); pending_papers.clear()
            return
        for paper, emb in zip(pending_papers, embeddings):
            text = f"{paper['title']}\n\n{paper['abstract']}"
            total += await upsert_chunks(
                conn, source="arxiv", source_id=paper["id"],
                chunks=[text], embeddings=[emb],
                metadata={
                    "title": paper["title"], "published": paper["published"],
                    "authors": paper["authors"], "link": paper["link"],
                    "category": paper["category"],
                },
                on_conflict="nothing",
                content_hashes=[compute_content_hash(text)],
            )
        pending_texts.clear(); pending_papers.clear()

    for paper in papers:
        if not paper.get("id") or not paper.get("abstract"):
            continue
        pending_texts.append(f"{paper['title']}\n\n{paper['abstract']}")
        pending_papers.append(paper)
        if len(pending_texts) >= BATCH_SIZE:
            await flush()
    await flush()
    return total


async def main_async(categories: list[str], max_per_cat: int, from_raw: Path | None):
    start_metrics_server()
    with _tracer.start_as_current_span("ingest_arxiv") as span:
        span.set_attribute("source", "arxiv")
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            papers = list(iter_raw(from_raw))
        else:
            papers = await fetch_all_categories(categories, max_per_cat)
            save_raw(papers, "arxiv")

        logger.info(f"Processing {len(papers)} arXiv papers…")
        conn = await get_db_connection()
        job_id = await create_job(conn, "arxiv", len(papers))
        set_correlation_id(str(job_id))
        span.set_attribute("correlation_id", str(job_id))
        await conn.commit()

        total = await process_papers(papers, conn, job_id)
        INGESTION_CHUNKS_PROCESSED.labels(source="arxiv", status="success").inc(total)
        await update_job_progress(conn, job_id, len(papers))
        await emit_job_lineage(conn, job_id)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(f"arXiv ingestion complete: {total} chunks from {len(papers)} papers")
    except Exception as exc:
        set_span_error(span, exc)
        logger.error(f"arxiv ingestion failed: {exc}", exc_info=True)
        INGESTION_JOBS_FAILED.labels(source="arxiv").inc()
        if conn is not None and job_id is not None:
            try:
                await write_to_dlq(conn, source="arxiv", source_id=f"job:{job_id}",
                                   content=None, metadata={"job_id": job_id}, error=exc)
                INGESTION_DLQ_MESSAGES.labels(source="arxiv").inc()
                await emit_dlq_lineage(conn, source="arxiv", job_id=job_id)
                await finish_job(conn, job_id, "failed", error_message=str(exc)[:500])
                await conn.commit()
            except Exception as finish_exc:
                logger.error(f"Failed to mark job as failed: {finish_exc}")
        raise
    finally:
        set_correlation_id(None)
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
    parser = argparse.ArgumentParser(description="Ingest arXiv papers into pgvector")
    parser.add_argument("--categories", nargs="+", default=DEFAULT_CATEGORIES)
    parser.add_argument("--max-per-cat", type=int, default=500)
    parser.add_argument(
        "--from-raw", metavar="PATH", default=None,
        help="Replay from a saved JSONL.gz file instead of hitting the API. "
             "Omit to use the latest saved file, or pass a path.",
    )
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("arxiv")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(args.categories, args.max_per_cat, raw_path))


if __name__ == "__main__":
    main()
