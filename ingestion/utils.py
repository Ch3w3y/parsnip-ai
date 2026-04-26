"""
Shared utilities for ingestion scripts.
"""

import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Iterator

import httpx
import psycopg
from dotenv import load_dotenv

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]
from pgvector.psycopg import register_vector_async

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)


# ── Circuit breaker for Ollama embedding endpoint ──────────────────────────────
# When Ollama is unreachable, each embed_batch() call retries 3× with 120s timeout
# each = ~6 minutes of blocking. The circuit breaker fast-fails after consecutive
# connection/timeout errors so callers don't burn time waiting for a dead service.
#
# Thresholds:
#   3 consecutive connection failures within 60s → open for 30s (half-open after)
#   5 consecutive connection failures within 60s → open for 300s (5 min)
#
# HTTP 400 (bad input) does NOT trip the circuit — it's a client error, not a
# service-health signal. HTTP 500+ and ConnectError/Timeout DO trip it.


class _CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _EmbedCircuitBreaker:
    """Async-safe circuit breaker wrapping Ollama embed calls.

    Tracks consecutive failures of connection/timeout type. Once a threshold is
    reached, the circuit opens and fast-fails callers without hitting Ollama at
    all. After a cooldown period, the circuit enters half-open where one probe
    call is allowed; if it succeeds the circuit closes, if it fails the circuit
    re-opens with an escalating cooldown.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: _CircuitState = _CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._first_failure_at: float | None = None
        self._opened_at: float | None = None
        self._open_duration: float = 0.0

    async def is_open(self) -> bool:
        """Return True if the circuit is OPEN (callers should fast-fail)."""
        async with self._lock:
            if self._state == _CircuitState.CLOSED:
                return False
            if self._state == _CircuitState.HALF_OPEN:
                return False
            assert self._opened_at is not None
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._open_duration:
                self._state = _CircuitState.HALF_OPEN
                logger.info(
                    "embed circuit breaker: OPEN → HALF_OPEN "
                    f"(after {self._open_duration:.0f}s cooldown)"
                )
                return False
            return True

    async def record_success(self) -> None:
        """Mark a successful call — resets failure count, closes circuit."""
        async with self._lock:
            if self._state != _CircuitState.CLOSED:
                prev = self._state.value
                self._state = _CircuitState.CLOSED
                self._consecutive_failures = 0
                self._first_failure_at = None
                self._opened_at = None
                logger.info(
                    f"embed circuit breaker: {prev} → closed (success)"
                )
            else:
                self._consecutive_failures = 0
                self._first_failure_at = None

    async def record_failure(self, exc: Exception) -> None:
        """Record a call failure. Trips the circuit if thresholds are crossed.

        Only connection/timeout/server errors trip the circuit.
        HTTP 400 errors are ignored (bad input, not service health).
        """
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code == 400:
                return
        # All other errors (ConnectError, TimeoutException, HTTP 5xx) trip the circuit

        async with self._lock:
            now = time.monotonic()
            self._consecutive_failures += 1

            if self._first_failure_at is None:
                self._first_failure_at = now

            window_elapsed = now - self._first_failure_at
            if window_elapsed > 60.0:
                self._first_failure_at = now

            if self._consecutive_failures >= 5:
                new_duration = 300.0
            elif self._consecutive_failures >= 3:
                new_duration = 30.0
            else:
                return

            prev_state = self._state.value
            self._state = _CircuitState.OPEN
            self._opened_at = now
            self._open_duration = max(self._open_duration, new_duration)
            logger.warning(
                f"embed circuit breaker: {prev_state} → OPEN "
                f"({self._consecutive_failures} consecutive failures, "
                f"cooldown={self._open_duration:.0f}s)"
            )


# Module-level circuit breaker instance
_embed_cb = _EmbedCircuitBreaker()


async def reset_circuit_breaker() -> None:
    """Manually reset the embed circuit breaker to closed state.

    Call this after fixing Ollama (e.g. restarting the service) to immediately
    resume embedding calls instead of waiting for the cooldown to expire.
    """
    async with _embed_cb._lock:
        prev = _embed_cb._state.value
        _embed_cb._state = _CircuitState.CLOSED
        _embed_cb._consecutive_failures = 0
        _embed_cb._first_failure_at = None
        _embed_cb._opened_at = None
        _embed_cb._open_duration = 0.0
    logger.info(f"embed circuit breaker: {prev} → closed (manual reset)")

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


def compute_content_hash(text: str) -> str:
    """Return SHA-256 hex digest of text (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def verify_hash(content: str, content_hash: str | None) -> bool:
    """Check whether content matches its stored SHA-256 hash.

    Returns True if content_hash is None (backward compat — unhashed rows)
    or if the computed hash matches content_hash. Returns False only when
    content_hash is set and doesn't match.
    """
    if content_hash is None:
        return True
    return compute_content_hash(content) == content_hash


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
    if await _embed_cb.is_open():
        logger.warning("embed circuit breaker OPEN — fast-failing embed_batch()")
        return None

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

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{OLLAMA_BASE_URL}/api/embed",
                    json={"model": embed_model, "input": cleaned, "truncate": True},
                )
                r.raise_for_status()
                await _embed_cb.record_success()
                return [[float(v) for v in emb] for emb in r.json()["embeddings"]]
        except httpx.HTTPStatusError as e:
            last_exc = e
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
                if any(e is not None for e in embeddings):
                    await _embed_cb.record_success()
                    return embeddings
                return None

            wait = 2**attempt
            logger.warning(
                f"Embed attempt {attempt + 1}/{retries} failed: {e} — retry in {wait}s"
            )
            if attempt < retries - 1:
                await asyncio.sleep(wait)
        except Exception as e:
            last_exc = e
            wait = 2**attempt
            logger.warning(
                f"Embed attempt {attempt + 1}/{retries} failed: {e} — retry in {wait}s"
            )
            if attempt < retries - 1:
                await asyncio.sleep(wait)

    if last_exc is not None:
        await _embed_cb.record_failure(last_exc)
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
    embedding_model: str = "mxbai-embed-large",
    content_hashes: list[str | None] | None = None,
) -> int:
    """Insert chunks with embeddings. Returns count of rows written.

    If *content_hashes* is provided, each chunk is checked against the existing
    row's content_hash. When the hash matches, the chunk is skipped (no re-embed
    needed). Pass None or omit for backward-compatible always-upsert behaviour.
    """
    if on_conflict == "update":
        conflict_clause = """
            ON CONFLICT (source, source_id, chunk_index)
            DO UPDATE SET
                content         = EXCLUDED.content,
                content_hash    = EXCLUDED.content_hash,
                embedding       = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model,
                metadata        = EXCLUDED.metadata,
                updated_at      = NOW()
        """
    else:
        conflict_clause = "ON CONFLICT (source, source_id, chunk_index) DO NOTHING"

    inserted = 0
    for idx, (text, emb) in enumerate(zip(chunks, embeddings)):
        ch = content_hashes[idx] if content_hashes and idx < len(content_hashes) else None

        # Skip if content unchanged (only for on_conflict="update")
        if ch is not None and on_conflict == "update":
            try:
                existing = await conn.execute(
                    "SELECT content_hash FROM knowledge_chunks "
                    "WHERE source = %s AND source_id = %s AND chunk_index = %s",
                    (source, source_id, idx),
                )
                row = await existing.fetchone()
                if row is not None and row[0] == ch:
                    # Content unchanged — skip re-embedding
                    continue
            except Exception:
                pass  # If SELECT fails, proceed with upsert

        try:
            async with conn.transaction():
                result = await conn.execute(
                    f"""
                    INSERT INTO knowledge_chunks
                        (source, source_id, chunk_index, content, content_hash, metadata, embedding, embedding_model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    {conflict_clause}
                    """,
                    (
                        source,
                        source_id,
                        idx,
                        text,
                        ch,
                        psycopg.types.json.Jsonb(metadata),
                        emb,
                        embedding_model,
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
    ],  # (source, source_id, chunk_index, content, content_hash|None, metadata_dict, embedding, embedding_model)
    on_conflict: str = "update",
) -> int:
    """Bulk-insert many chunks in a single transaction using executemany.

    Dramatically faster than upsert_chunks for large batches (Wikipedia-scale)
    — replaces N individual transactions with one transaction + N pipelined statements.

    Row tuples have 8 elements: source, source_id, chunk_index, content,
    content_hash (str|None), metadata (dict), embedding, embedding_model.
    """
    if not rows:
        return 0

    if on_conflict == "update":
        conflict_clause = """
            ON CONFLICT (source, source_id, chunk_index)
            DO UPDATE SET
                content         = EXCLUDED.content,
                content_hash    = EXCLUDED.content_hash,
                embedding       = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model,
                metadata        = EXCLUDED.metadata,
                updated_at      = NOW()
        """
    else:
        conflict_clause = "ON CONFLICT (source, source_id, chunk_index) DO NOTHING"

    sql = f"""
        INSERT INTO knowledge_chunks
            (source, source_id, chunk_index, content, content_hash, metadata, embedding, embedding_model)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        {conflict_clause}
    """

    params = [
        (src, sid, idx, content, ch, psycopg.types.json.Jsonb(meta), emb, model)
        for src, sid, idx, content, ch, meta, emb, model in rows
    ]

    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.executemany(sql, params)

    return len(rows)


async def cleanup_orphan_chunks(
    conn, source: str, source_id: str, new_chunk_count: int
) -> int:
    """Delete chunks with chunk_index >= new_chunk_count for a given source+source_id.

    When an article/document is updated and now has fewer chunks than before,
    the old excess chunks remain orphaned in the database. This function removes
    them to keep the knowledge base consistent.

    Returns the number of deleted rows.
    """
    result = await conn.execute(
        """
        DELETE FROM knowledge_chunks
        WHERE source = %s
          AND source_id = %s
          AND chunk_index >= %s
        """,
        (source, source_id, new_chunk_count),
    )
    deleted = result.rowcount
    if deleted:
        logger.info(
            f"Cleaned up {deleted} orphan chunk(s) for {source}/{source_id} "
            f"(new count: {new_chunk_count})"
        )
    return deleted


async def update_job_progress(conn, job_id: int, processed: int):
    await conn.execute(
        "UPDATE ingestion_jobs SET processed = %s WHERE id = %s",
        (processed, job_id),
    )


async def create_job(conn, source: str, total: int | None = None) -> int:
    row = await (
        await conn.execute(
            """
            INSERT INTO ingestion_jobs (source, status, total, retry_count, started_at)
            VALUES (%s, 'running', %s, 0, NOW())
            RETURNING id
            """,
            (source, total),
        )
    ).fetchone()
    return row[0]


async def finish_job(
    conn,
    job_id: int,
    status: str = "done",
    *,
    error_message: str | None = None,
    failed_count: int | None = None,
    retry_count: int | None = None,
):
    """Mark an ingestion job as done or failed.

    Columns written on every call:
      - status, finished_at
    When status='failed':
      - error_message (last error), failed_count, retry_count (incremented if not passed)
    duration_ms is always computed from started_at → finished_at.
    """
    sets = ["status = %s", "finished_at = NOW()"]
    params: list = [status]

    sets.append(
        "duration_ms = EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000"
    )

    if status == "failed" or error_message is not None:
        sets.append("error_message = %s")
        params.append(error_message)

    if failed_count is not None:
        sets.append("failed_count = %s")
        params.append(failed_count)

    if retry_count is not None:
        sets.append("retry_count = %s")
        params.append(retry_count)
    elif status == "failed":
        sets.append("retry_count = retry_count + 1")

    params.append(job_id)

    sql = f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = %s"
    await conn.execute(sql, params)


async def write_to_dlq(
    conn,
    source: str,
    source_id: str,
    content: str | None,
    metadata: dict,
    error: Exception,
    retry_count: int = 0,
):
    await conn.execute(
        """
        INSERT INTO failed_records (source, source_id, content, metadata, error_message, error_class, retry_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            source,
            source_id,
            content,
            psycopg.types.json.Jsonb(metadata),
            str(error)[:2000],
            type(error).__name__,
            retry_count,
        ),
    )


def classify_error(exc: Exception) -> str:
    """Classify an exception as 'transient' (retryable) or 'permanent'.

    Transient: network/timeout/rate-limit errors that may succeed on retry.
    Permanent: programming/logic/invalid-request errors that won't self-resolve.
    """
    # --- Transient: connection / timeout ---
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return "transient"

    try:
        if isinstance(exc, asyncio.TimeoutError) and not isinstance(exc, TimeoutError):
            return "transient"
    except TypeError:
        pass

    # --- Transient: psycopg operational errors (connection dropped, etc.) ---
    if isinstance(exc, psycopg.OperationalError):
        return "transient"

    # --- Transient: asyncpg connection errors ---
    if asyncpg is not None and isinstance(exc, asyncpg.PostgresError):
        # Classify connection-related asyncpg errors as transient
        sqlstate = getattr(exc, "sqlstate", "")
        # 08xxx = connection exception, 53xxx = insufficient resources
        if sqlstate and (sqlstate.startswith("08") or sqlstate.startswith("53")):
            return "transient"

    # --- Transient: HTTP status errors (429 rate-limit, 503 service unavailable) ---
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (429, 503):
            return "transient"
        if exc.response.status_code in (400, 404):
            return "permanent"

    # --- Permanent: programming / logic errors ---
    if isinstance(exc, (ValueError, KeyError, TypeError, AssertionError)):
        return "permanent"

    # --- Default: unknown errors treated as transient (safer to retry) ---
    return "transient"


async def update_job_retry_count(conn, job_id: int) -> None:
    """Increment retry_count for an ingestion job by 1."""
    await conn.execute(
        "UPDATE ingestion_jobs SET retry_count = retry_count + 1 WHERE id = %s",
        (job_id,),
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

    result = await conn.execute(
        f"""
        UPDATE ingestion_jobs
        SET status = 'failed',
            finished_at = NOW(),
            error_message = 'Job timed out after {timeout_hours} hours',
            duration_ms = EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000,
            retry_count = retry_count + 1
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
