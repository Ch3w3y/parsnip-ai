"""Helpers for the ingestion / migration status endpoint."""

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Allow callers that pass in a DB URL directly to not need .env loaded
try:
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass


@dataclass
class MigrationStatus:
    running: bool
    pid: int | None
    rows_remaining: int
    anomalous_rows: int
    ready_for_ingestion: bool
    last_log_tail: str | None


@dataclass
class BulkIngestStatus:
    running: bool
    pid: int | None
    chunks_in_kb: int
    articles_in_kb: int
    last_job_status: str | None
    last_job_processed: int | None


@dataclass
class IngestionOverview:
    migration: MigrationStatus
    wikipedia_bulk: BulkIngestStatus
    recent_jobs: list[dict]
    scheduled_next: dict


async def _get_db_conn():
    """Return a psycopg async connection using the configured DATABASE_URL."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    return await psycopg.AsyncConnection.connect(dsn)


def _find_migration_pid() -> int | None:
    """Search for a running migrate_wiki_source_ids.py process."""
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-f", "scripts/migrate_wiki_source_ids.py"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def _find_bulk_ingest_pid() -> int | None:
    """Search for a running ingest_wikipedia.py process."""
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-f", "ingest_wikipedia.py"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def _migration_log_tail(lines: int = 5) -> str | None:
    """Return the last N lines of the migration log, if available."""
    log_paths = ["/tmp/post_migration_monitor.log", "/tmp/migrate_wiki.log"]
    for path in log_paths:
        try:
            if Path(path).exists():
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                return "".join(all_lines[-lines:]).strip()
        except Exception:
            continue
    return None


async def get_migration_status() -> MigrationStatus:
    """Determine whether the Wikipedia source_id migration is complete."""
    pid = _find_migration_pid()
    rows_remaining = 0
    anomalous_rows = 0

    try:
        async with await _get_db_conn() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_chunks
                    WHERE source = 'wikipedia' AND source_id ~ '::[0-9]+$'
                    """
                )
            ).fetchone()
            if row:
                rows_remaining = row[0]

            row2 = await (
                await conn.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_chunks
                    WHERE source = 'wikipedia' AND source_id !~ '^[A-Z]'
                    """
                )
            ).fetchone()
            if row2:
                anomalous_rows = row2[0]
    except Exception as e:
        logger.warning(f"DB error querying migration status: {e}")

    return MigrationStatus(
        running=pid is not None,
        pid=pid,
        rows_remaining=rows_remaining,
        anomalous_rows=anomalous_rows,
        ready_for_ingestion=(rows_remaining == 0 and pid is None),
        last_log_tail=_migration_log_tail(),
    )


async def get_wikipedia_bulk_status() -> BulkIngestStatus:
    """Return the current state of the Wikipedia dump seed ingestion."""
    pid = _find_bulk_ingest_pid()
    chunks = 0
    articles = 0
    last_status: str | None = None
    last_processed: int | None = None

    try:
        async with await _get_db_conn() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT source_id)
                    FROM knowledge_chunks
                    WHERE source = 'wikipedia'
                    """
                )
            ).fetchone()
            if row:
                chunks, articles = row[0], row[1]

            job_row = await (
                await conn.execute(
                    """
                    SELECT status, processed
                    FROM ingestion_jobs
                    WHERE source = 'wikipedia'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            ).fetchone()
            if job_row:
                last_status, last_processed = job_row[0], job_row[1]
    except Exception as e:
        logger.warning(f"DB error querying bulk ingest status: {e}")

    return BulkIngestStatus(
        running=pid is not None,
        pid=pid,
        chunks_in_kb=chunks,
        articles_in_kb=articles,
        last_job_status=last_status,
        last_job_processed=last_processed,
    )


async def get_recent_jobs(limit: int = 10) -> list[dict]:
    """Return the most recent ingestion job rows."""
    try:
        async with await _get_db_conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, source, status, total, processed,
                           started_at, finished_at, metadata
                    FROM ingestion_jobs
                    ORDER BY started_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            ).fetchall()

            return [
                {
                    "id": r[0],
                    "source": r[1],
                    "status": r[2],
                    "total": r[3],
                    "processed": r[4],
                    "started_at": str(r[5]),
                    "finished_at": str(r[6]),
                    "metadata": r[7] or {},
                }
                for r in rows
            ]
    except Exception as e:
        logger.warning(f"DB error querying recent jobs: {e}")
        return []


def _next_scheduled(source: str, source_id: str) -> str:
    """Human-readable next-run hint for major scheduled jobs."""
    schedules = {
        "news": "Daily 06:00 UTC",
        "arxiv": "Monday 03:00 UTC",
        "biorxiv": "Tuesday 03:00 UTC",
        "wikipedia_update": "Sunday 02:00 UTC (gated until dump seed completes)",
        "forex": "Daily 07:00 UTC",
        "world_bank": "Sunday 04:00 UTC",
        "joplin": "Continuous (watcher 30s; safety every 6h)",
    }
    return schedules.get(source, "N/A")


async def get_scheduled_next() -> dict:
    """Build a map of scheduled source → next expected run."""
    try:
        async with await _get_db_conn() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT DISTINCT ON (source) source, status, total, processed
                    FROM ingestion_jobs
                    ORDER BY source, started_at DESC
                    """
                )
            ).fetchall()
    except Exception:
        rows = []

    sources = {r[0]: {"status": r[1], "total": r[2], "processed": r[3]} for r in rows}
    scheduled = {
        "news": _next_scheduled("news", ""),
        "arxiv": _next_scheduled("arxiv", ""),
        "biorxiv": _next_scheduled("biorxiv", ""),
        "wikipedia_update": _next_scheduled("wikipedia_update", ""),
        "forex": _next_scheduled("forex", ""),
        "world_bank": _next_scheduled("world_bank", ""),
        "joplin": _next_scheduled("joplin", ""),
    }

    for key in scheduled:
        scheduled[key] = {
            "next": scheduled[key],
            "last_job_status": sources.get(key, {}).get("status", "unknown"),
            "last_processed": sources.get(key, {}).get("processed"),
            "last_total": sources.get(key, {}).get("total"),
        }
    return scheduled


async def get_ingestion_overview() -> IngestionOverview:
    """Convenience wrapper that gathers all sections."""
    return IngestionOverview(
        migration=await get_migration_status(),
        wikipedia_bulk=await get_wikipedia_bulk_status(),
        recent_jobs=await get_recent_jobs(),
        scheduled_next=await get_scheduled_next(),
    )


def to_dict(overview: IngestionOverview) -> dict:
    """Serialize an IngestionOverview to a plain dict for JSON responses."""
    return {
        "migration": {
            "running": overview.migration.running,
            "pid": overview.migration.pid,
            "rows_remaining": overview.migration.rows_remaining,
            "anomalous_rows": overview.migration.anomalous_rows,
            "ready_for_ingestion": overview.migration.ready_for_ingestion,
            "last_log_tail": overview.migration.last_log_tail,
        },
        "wikipedia_bulk": {
            "running": overview.wikipedia_bulk.running,
            "pid": overview.wikipedia_bulk.pid,
            "chunks_in_kb": overview.wikipedia_bulk.chunks_in_kb,
            "articles_in_kb": overview.wikipedia_bulk.articles_in_kb,
            "last_job_status": overview.wikipedia_bulk.last_job_status,
            "last_job_processed": overview.wikipedia_bulk.last_job_processed,
        },
        "recent_jobs": overview.recent_jobs,
        "scheduled_next": overview.scheduled_next,
    }
