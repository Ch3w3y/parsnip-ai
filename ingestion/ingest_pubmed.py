#!/usr/bin/env python3
"""
PubMed E-Util API ingestion: search by MeSH terms, fetch abstracts, chunk and embed.

Categories: AI in medicine, genomics, neuroscience, epidemiology.

Usage:
    python ingest_pubmed.py --terms "artificial intelligence" "machine learning" --max-per-term 200
    python ingest_pubmed.py --from-raw
    python ingest_pubmed.py --from-raw path/to/file.jsonl.gz
"""

import argparse
import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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

PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BATCH_SIZE = 32
FETCH_BATCH = 100

DEFAULT_TERMS = [
    "artificial intelligence[MeSH Terms]",
    "machine learning[MeSH Terms]",
    "deep learning[MeSH Terms]",
    "genomics[MeSH Terms]",
    "neuroscience[MeSH Terms]",
    "epidemiology[MeSH Terms]",
    "computational biology[MeSH Terms]",
    "bioinformatics[MeSH Terms]",
]


async def esearch(term: str, retmax: int, retstart: int = 0) -> list[str]:
    """Search PubMed and return list of PMIDs."""
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": retmax,
        "retstart": retstart,
        "sort": "relevance",
        "retmode": "json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{PUBMED_EUTILS}/esearch.fcgi", params=params)
        r.raise_for_status()
    data = r.json()
    return data.get("esearchresult", {}).get("idlist", [])


async def efetch(pmids: list[str]) -> list[dict]:
    """Fetch full records for a list of PMIDs."""
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{PUBMED_EUTILS}/efetch.fcgi", params=params)
        r.raise_for_status()

    root = ET.fromstring(r.text)
    papers = []
    for article in root.findall("PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            medline = article.find("Article")
        if medline is None:
            continue

        article_elem = medline.find("Article")
        if article_elem is None:
            continue

        journal = article_elem.find("Journal")
        journal_title = ""
        if journal is not None:
            journal_title = journal.findtext("Title", "") or journal.findtext(
                "ISOAbbreviation", ""
            )

        article_title = article_elem.findtext("ArticleTitle", "")
        if not article_title:
            continue

        abstract_elem = article_elem.find("Abstract")
        abstract = ""
        if abstract_elem is not None:
            abstract_parts = []
            for label_elem in abstract_elem.findall("AbstractText"):
                label = label_elem.get("Label", "")
                text = (label_elem.text or "").strip()
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)
            abstract = "\n".join(abstract_parts)

        if not abstract:
            continue

        pub_date = article_elem.find("Journal/JournalIssue/PubDate")
        pub_year = ""
        if pub_date is not None:
            pub_year = pub_date.findtext("Year", "")

        authors = []
        author_list = article_elem.find("AuthorList")
        if author_list is not None:
            for author in author_list.findall("Author"):
                last = author.findtext("LastName", "")
                fore = author.findtext("ForeName", "")
                if last or fore:
                    authors.append(f"{fore} {last}".strip())

        pmid_elem = medline.find("PMID")
        pmid = pmid_elem.text if pmid_elem is not None else ""

        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        papers.append(
            {
                "pmid": pmid,
                "title": article_title,
                "abstract": abstract,
                "journal": journal_title,
                "year": pub_year,
                "authors": authors[:10],
                "link": link,
            }
        )

    return papers


async def fetch_all_terms(terms: list[str], max_per_term: int) -> list[dict]:
    """Phase 1: Fetch all papers across search terms — pure API, no DB."""
    all_papers: list[dict] = []
    seen_pmids: set[str] = set()

    for term in terms:
        logger.info(f"Searching PubMed: {term}")
        start = 0
        while start < max_per_term:
            batch_size = min(FETCH_BATCH, max_per_term - start)
            try:
                pmids = await esearch(term, batch_size, start)
            except Exception as e:
                logger.error(f"Search failed for '{term}' at {start}: {e}")
                break

            if not pmids:
                break

            new_pmids = [p for p in pmids if p not in seen_pmids]
            if new_pmids:
                try:
                    papers = await efetch(new_pmids)
                    for p in papers:
                        if p["pmid"] not in seen_pmids:
                            seen_pmids.add(p["pmid"])
                            p["search_term"] = term
                            all_papers.append(p)
                except Exception as e:
                    logger.error(f"Fetch failed for term '{term}': {e}")

            start += len(pmids)
            await asyncio.sleep(1)

    return all_papers


async def process_papers(papers: list[dict], conn, job_id: int) -> int:
    """Phase 2: Chunk, embed, and upsert papers."""
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
            pending_texts.clear()
            pending_papers.clear()
            return
        for paper, emb in zip(pending_papers, embeddings):
            text = f"{paper['title']}\n\n{paper['abstract']}"
            total += await upsert_chunks(
                conn,
                source="pubmed",
                source_id=f"pmid_{paper['pmid']}",
                chunks=[text],
                embeddings=[emb],
                metadata={
                    "title": paper["title"],
                    "journal": paper["journal"],
                    "year": paper["year"],
                    "authors": paper["authors"],
                    "link": paper["link"],
                    "search_term": paper.get("search_term", ""),
                },
                on_conflict="nothing",
            )
        pending_texts.clear()
        pending_papers.clear()

    for paper in papers:
        if not paper.get("pmid") or not paper.get("abstract"):
            continue
        pending_texts.append(f"{paper['title']}\n\n{paper['abstract']}")
        pending_papers.append(paper)
        if len(pending_texts) >= BATCH_SIZE:
            await flush()

    await flush()
    await update_job_progress(conn, job_id, len(papers))
    return total


async def main_async(terms: list[str], max_per_term: int, from_raw: Path | None):
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            papers = list(iter_raw(from_raw))
        else:
            papers = await fetch_all_terms(terms, max_per_term)
            save_raw(papers, "pubmed")

        logger.info(f"Processing {len(papers)} PubMed articles…")
        conn = await get_db_connection()
        job_id = await create_job(conn, "pubmed", len(papers))
        await conn.commit()

        total = await process_papers(papers, conn, job_id)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(
            f"PubMed ingestion complete: {total} chunks from {len(papers)} articles"
        )
    except Exception as exc:
        logger.error(f"pubmed ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Ingest PubMed articles into pgvector")
    parser.add_argument("--terms", nargs="+", default=DEFAULT_TERMS)
    parser.add_argument("--max-per-term", type=int, default=200)
    parser.add_argument(
        "--from-raw",
        metavar="PATH",
        default=None,
        help="Replay from a saved JSONL.gz file instead of hitting the API.",
    )
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("pubmed")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(args.terms, args.max_per_term, raw_path))


if __name__ == "__main__":
    main()
