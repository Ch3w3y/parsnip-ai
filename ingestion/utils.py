"""
Shared utilities for ingestion scripts.
"""

import asyncio
import gzip
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Iterator

import httpx
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector_async

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# ── Landing zone (raw fetch cache) ────────────────────────────────────────────

RAW_DATA_DIR = Path(__file__).parent / "data" / "raw"


def save_raw(records: list[dict], source: str, label: str = "") -> Path:
    """Save fetched records as JSONL.gz before processing.

    Pattern: fetch → save_raw → embed+upsert
    If embedding/DB fails, replay with --from-raw instead of re-hitting the API.

    Files land at: ingestion/data/raw/<source>/YYYY-MM-DD[_label].jsonl.gz
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{today}_{label}" if label else today
    out_dir = RAW_DATA_DIR / source
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info(f"Saved {len(records)} raw records → {path}")
    return path


def iter_raw(path: Path) -> Iterator[dict]:
    """Yield records from a JSONL.gz landing zone file."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def latest_raw(source: str) -> Path | None:
    """Return the most recent raw file for a source, or None if none exist."""
    source_dir = RAW_DATA_DIR / source
    if not source_dir.exists():
        return None
    files = sorted(source_dir.glob("*.jsonl.gz"))
    return files[-1] if files else None


OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def chunk_text(text: str, chunk_words: int = 200, overlap_words: int = 40) -> list[str]:
    """Split text into overlapping word-count chunks."""
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_words])
        chunks.append(chunk)
        i += chunk_words - overlap_words
    return chunks


def clean_text(text: str) -> str:
    """Strip null bytes and non-printable control chars that cause Ollama 400s."""
    cleaned = "".join(c for c in text if ord(c) >= 32 or c in "\n\t\r")
    return cleaned[:2000]  # hard cap: ~500 tokens for mxbai-embed-large


async def embed_batch(
    texts: list[str], retries: int = 3, model: str | None = None
) -> list[list[float]] | None:
    """Embed a batch of texts via Ollama. Returns None on unrecoverable failure."""
    embed_model = model or EMBED_MODEL

    # Filter out empty/whitespace-only texts — Ollama returns 400 for these
    cleaned = []
    indices = []
    for i, t in enumerate(texts):
        if t and t.strip():
            cleaned.append(t)
            indices.append(i)
    if not cleaned:
        return None

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{OLLAMA_BASE_URL}/api/embed",
                    json={"model": embed_model, "input": cleaned, "truncate": True},
                )
                r.raise_for_status()
                return [[float(v) for v in emb] for emb in r.json()["embeddings"]]
        except httpx.HTTPStatusError as e:
            # 400 from Ollama — try per-item with cleaning, return None-aligned list
            if e.response.status_code == 400 and attempt == 0:
                logger.warning(
                    f"Batch embed returned 400, falling back to per-item embedding "
                    f"for {len(cleaned)} texts"
                )
                embeddings: list[list[float] | None] = []
                for i, t in enumerate(cleaned):
                    t_safe = clean_text(t)
                    try:
                        async with httpx.AsyncClient(timeout=120) as c2:
                            r2 = await c2.post(
                                f"{OLLAMA_BASE_URL}/api/embed",
                                json={
                                    "model": embed_model,
                                    "input": [t_safe],
                                    "truncate": True,
                                },
                            )
                            r2.raise_for_status()
                            emb = r2.json()["embeddings"]
                            embeddings.append([float(v) for v in emb[0]] if emb else None)
                    except Exception as e2:
                        logger.warning(
                            f"Failed text {i}/{len(cleaned)} (len={len(t)}): {e2}"
                        )
                        embeddings.append(None)
                return embeddings if any(e is not None for e in embeddings) else None

            wait = 2**attempt
            logger.warning(
                f"Embed attempt {attempt + 1}/{retries} failed: {e} — retry in {wait}s"
            )
            if attempt < retries - 1:
                await asyncio.sleep(wait)
        except Exception as e:
            wait = 2**attempt
            logger.warning(
                f"Embed attempt {attempt + 1}/{retries} failed: {e} — retry in {wait}s"
            )
            if attempt < retries - 1:
                await asyncio.sleep(wait)
    return None


async def get_db_connection():
    """Return an async psycopg connection with pgvector registered."""
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    await register_vector_async(conn)
    return conn


async def upsert_chunks(
    conn,
    source: str,
    source_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    metadata: dict,
    on_conflict: str = "update",  # "update" refreshes changed content; "nothing" skips
) -> int:
    """Insert chunks with embeddings. Returns count of rows written."""
    if on_conflict == "update":
        conflict_clause = """
            ON CONFLICT (source, source_id, chunk_index)
            DO UPDATE SET
                content    = EXCLUDED.content,
                embedding  = EXCLUDED.embedding,
                metadata   = EXCLUDED.metadata,
                updated_at = NOW()
        """
    else:
        conflict_clause = "ON CONFLICT (source, source_id, chunk_index) DO NOTHING"

    inserted = 0
    for idx, (text, emb) in enumerate(zip(chunks, embeddings)):
        try:
            async with conn.transaction():
                result = await conn.execute(
                    f"""
                    INSERT INTO knowledge_chunks
                        (source, source_id, chunk_index, content, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    {conflict_clause}
                    """,
                    (
                        source,
                        source_id,
                        idx,
                        text,
                        psycopg.types.json.Jsonb(metadata),
                        emb,
                    ),
                )
                if result.rowcount > 0:
                    inserted += 1
        except Exception as e:
            logger.error(f"Insert error for {source_id}[{idx}]: {e}")
    return inserted


async def bulk_upsert_chunks(
    conn,
    rows: list[
        tuple
    ],  # (source, source_id, chunk_index, content, metadata_dict, embedding, embedding_model)
    on_conflict: str = "update",
) -> int:
    """Bulk-insert many chunks in a single transaction using executemany.

    Dramatically faster than upsert_chunks for large batches (Wikipedia-scale)
    — replaces N individual transactions with one transaction + N pipelined statements.
    """
    if not rows:
        return 0

    if on_conflict == "update":
        conflict_clause = """
            ON CONFLICT (source, source_id, chunk_index)
            DO UPDATE SET
                content         = EXCLUDED.content,
                embedding       = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model,
                metadata        = EXCLUDED.metadata,
                updated_at      = NOW()
        """
    else:
        conflict_clause = "ON CONFLICT (source, source_id, chunk_index) DO NOTHING"

    sql = f"""
        INSERT INTO knowledge_chunks
            (source, source_id, chunk_index, content, metadata, embedding, embedding_model)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        {conflict_clause}
    """

    params = [
        (src, sid, idx, content, psycopg.types.json.Jsonb(meta), emb, model)
        for src, sid, idx, content, meta, emb, model in rows
    ]

    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.executemany(sql, params)

    return len(rows)


async def update_job_progress(conn, job_id: int, processed: int):
    await conn.execute(
        "UPDATE ingestion_jobs SET processed = %s WHERE id = %s",
        (processed, job_id),
    )


async def create_job(conn, source: str, total: int | None = None) -> int:
    row = await (
        await conn.execute(
            """
            INSERT INTO ingestion_jobs (source, status, total, started_at)
            VALUES (%s, 'running', %s, NOW())
            RETURNING id
            """,
            (source, total),
        )
    ).fetchone()
    return row[0]


async def finish_job(conn, job_id: int, status: str = "done"):
    await conn.execute(
        "UPDATE ingestion_jobs SET status = %s, finished_at = NOW() WHERE id = %s",
        (status, job_id),
    )


async def recover_stuck_jobs(conn, timeout_hours: float = None) -> int:
    """Mark running ingestion jobs as failed if they have been running too long.

    If *timeout_hours* is None, the value is read from the
    ``INGESTION_JOB_TIMEOUT_HOURS`` environment variable (default: 2).

    Returns the number of jobs that were recovered.
    """
    if timeout_hours is None:
        timeout_hours = float(os.environ.get("INGESTION_JOB_TIMEOUT_HOURS", "2"))

    # First, get the stuck jobs so we can log them
    stuck_rows = await (
        await conn.execute(
            f"""
            SELECT id, source, started_at
            FROM ingestion_jobs
            WHERE status = 'running'
              AND started_at < NOW() - INTERVAL '{timeout_hours} hours'
            """
        )
    ).fetchall()

    if not stuck_rows:
        return 0

    # Mark them as failed
    result = await conn.execute(
        f"""
        UPDATE ingestion_jobs
        SET status = 'failed', finished_at = NOW()
        WHERE status = 'running'
          AND started_at < NOW() - INTERVAL '{timeout_hours} hours'
        """
    )

    for row in stuck_rows:
        job_id, source, started_at = row
        duration = datetime.now(timezone.utc) - started_at.replace(tzinfo=timezone.utc) if started_at else None
        duration_str = str(duration) if duration else "unknown"
        logger.warning(
            f"Recovered stuck job: id={job_id} source={source} duration={duration_str}"
        )

    return result.rowcount
