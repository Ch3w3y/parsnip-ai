"""
Ingestion scheduler — runs inside Docker as part of the pi-agent stack.

Schedule:
  - News:               daily  at 06:00 UTC
  - arXiv:              weekly on Monday at 03:00 UTC
  - Wikipedia updates:  weekly on Sunday at 02:00 UTC

All ingestion sources are resolved via the SourceRegistry plugin system
(instead of direct module imports). See registry_adapter.run_source().
"""

import asyncio
import logging
import random
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from ingestion.registry import SourceRegistry
from ingestion.alerting import run_all_checks
from ingestion.utils import get_db_connection, recover_stuck_jobs
from registry_adapter import run_source

load_dotenv(Path(__file__).parent / "ingestion" / ".." / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")

# --- Plugin registry (replaces direct sys.path hack + import of each ingest_*.py) ---
registry = SourceRegistry()

ARXIV_CATEGORIES = [
    "cs.AI",
    "cs.LG",
    "cs.CL",
    "cs.CV",
    "stat.ML",
    "q-bio.GN",
    "q-bio.NC",
    "econ.GN",
]

# --- Failed / stuck ingestion retry configuration ---
MAX_RETRIES = 3
RETRY_INTERVAL_MINUTES = 30       # scheduler job runs every 30 min
STUCK_RECOVERY_INTERVAL_HOURS = 6 # aggressive stuck-job recovery every 6 hours
FAILED_RETRY_AGE_HOURS = 1       # only retry failed jobs older than 1 hour
STUCK_JOB_AGE_HOURS = 2          # jobs running longer than this are considered stuck

# Mutex: prevent concurrent Wikipedia writes (dump seed vs incremental update)
_wikipedia_lock = asyncio.Lock()


def _is_transient_error(error_message: str | None) -> bool:
    """Heuristic: classify an error_message string as transient or permanent.

    Mirrors the logic in ingestion.utils.classify_error() but works on stored
    text rather than a live Exception object. Unknown patterns default to
    transient (safer to retry).
    """
    if not error_message:
        return True  # no error info — assume transient

    msg_lower = error_message.lower()

    # Permanent signals
    permanent_patterns = [
        "valueerror",
        "keyerror",
        "typeerror",
        "assertionerror",
        "400",       # bad request
        "404",       # not found
    ]
    for p in permanent_patterns:
        if p in msg_lower:
            return False

    # Transient signals (everything else is treated as transient by default,
    # but be explicit about the common ones)
    transient_patterns = [
        "connectionerror",
        "connecterror",
        "timeout",
        "timed out",
        "429",         # rate limit
        "503",         # service unavailable
        "operationalerror",
        "connection",
        "refused",
        "reset",
        "unreachable",
    ]
    for p in transient_patterns:
        if p in msg_lower:
            return True

    # Default: unknown errors treated as transient (safer to retry)
    return True


async def wikipedia_seed_complete() -> bool:
    """Return True only if the one-time dump seed has finished successfully."""
    try:
        conn = await get_db_connection()
        row = await (
            await conn.execute(
                "SELECT status FROM ingestion_jobs WHERE source = 'wikipedia' AND status = 'done' LIMIT 1"
            )
        ).fetchone()
        await conn.close()
        return row is not None
    except Exception as e:
        logger.warning(f"Could not check Wikipedia seed status: {e}")
        return False


async def wikipedia_seed_running() -> bool:
    """Return True if a dump seed job is currently in progress."""
    try:
        conn = await get_db_connection()
        row = await (
            await conn.execute(
                "SELECT id FROM ingestion_jobs WHERE source = 'wikipedia' AND status = 'running' LIMIT 1"
            )
        ).fetchone()
        await conn.close()
        return row is not None
    except Exception as e:
        logger.warning(f"Could not check Wikipedia seed status: {e}")
        return True  # assume running on error — safer to skip


async def run_news():
    logger.info("=== Starting daily news ingestion ===")
    try:
        await run_source(registry, "news_api", days=1, from_raw=None)
    except Exception as e:
        logger.error(f"News ingestion failed: {e}", exc_info=True)


async def run_arxiv():
    logger.info("=== Starting weekly arXiv ingestion ===")
    try:
        await run_source(registry, "arxiv", categories=ARXIV_CATEGORIES, max_per_cat=500)
    except Exception as e:
        logger.error(f"arXiv ingestion failed: {e}", exc_info=True)


async def run_biorxiv():
    """bioRxiv/medRxiv — two separate calls with different server params."""
    logger.info("=== Starting weekly bioRxiv/medRxiv ingestion ===")
    entry = registry.get_source("biorxiv")
    func = entry.get_entry_point()
    try:
        import ingest_biorxiv as _biorxiv_mod  # needed for DEFAULT_CATEGORIES
        await func(
            server="biorxiv",
            days=7,
            categories=_biorxiv_mod.DEFAULT_CATEGORIES,
            limit=None,
        )
        await func(
            server="medrxiv",
            days=7,
            categories=[],  # medRxiv uses different category names — fetch all, filter later
            limit=500,
        )
    except Exception as e:
        logger.error(f"bioRxiv ingestion failed: {e}", exc_info=True)


async def run_joplin():
    logger.info("=== Starting Joplin incremental sync ===")
    try:
        await run_source(registry, "joplin", full=False)
    except SystemExit:
        logger.warning("Joplin sync skipped — Joplin not running or token not set.")
    except Exception as e:
        logger.error(f"Joplin sync failed: {e}", exc_info=True)


async def run_forex():
    logger.info("=== Starting daily forex ingestion ===")
    try:
        await run_source(registry, "forex", days=30)
    except Exception as e:
        logger.error(f"Forex ingestion failed: {e}", exc_info=True)


async def run_world_bank():
    logger.info("=== Starting monthly World Bank ingestion ===")
    try:
        await run_source(registry, "worldbank", years=20, all_countries=True)
    except Exception as e:
        logger.error(f"World Bank ingestion failed: {e}", exc_info=True)


async def run_wikipedia_updates():
    # Block if dump seed hasn't completed
    if not await wikipedia_seed_complete():
        logger.warning(
            "Wikipedia incremental update skipped — initial dump seed not yet complete. "
            "Will retry next scheduled run."
        )
        return

    # Block if dump seed is somehow running concurrently
    if await wikipedia_seed_running():
        logger.warning(
            "Wikipedia incremental update skipped — dump seed is currently running."
        )
        return

    if _wikipedia_lock.locked():
        logger.warning("Wikipedia update already in progress, skipping this run.")
        return

    async with _wikipedia_lock:
        logger.info("=== Starting weekly Wikipedia incremental update ===")
        try:
            await run_source(registry, "wikipedia_updates", days=7, limit=None)
        except Exception as e:
            logger.error(f"Wikipedia update failed: {e}", exc_info=True)


async def _run_joplin_watcher():
    """Run the Joplin sync watcher as a background task."""
    try:
        from joplin_watcher import main as watcher_main

        await watcher_main()
    except SystemExit:
        logger.warning("Joplin watcher exited — Joplin credentials or server unavailable. Continuing without Joplin sync.")
    except Exception as e:
        logger.error(f"Joplin watcher crashed: {e}", exc_info=True)


def _run_script(name: str, script: str, *args: str, timeout: int = 3600) -> bool:
    """Run a backup script as a subprocess. Returns True on success."""
    import subprocess
    cmd = [sys.executable, str(Path(__file__).parent / "scripts" / script), *args]
    logger.info(f"Running {name}: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            logger.info(f"{name} completed successfully.")
            return True
        logger.error(f"{name} failed (exit {result.returncode}): {result.stderr[-800:]}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"{name} timed out after {timeout}s.")
        return False
    except Exception as e:
        logger.error(f"{name} error: {e}")
        return False


async def run_backup_incremental():
    """Hourly incremental Parquet backup of KB tables to GCS."""
    _run_script("KB incremental backup", "backup_kb.py", "--mode", "incremental",
                timeout=1800)


async def run_backup_full():
    """Weekly full Parquet snapshot. Restore-canonical reference."""
    _run_script("KB full snapshot", "backup_kb.py", "--mode", "full",
                timeout=14400)


async def run_config_backup():
    """Project config + age-encrypted secrets bundle to GCS."""
    _run_script("Config backup", "backup_config.py", timeout=600)


async def run_volume_sync():
    """Sync analysis_output / owui_data / pipelines_data to GCS."""
    _run_script("Volume sync", "sync_volumes.py", timeout=3600)


async def recover_stuck_jobs_wrapper():
    """Periodic stuck-job recovery (wrapper for recover_stuck_jobs that manages the DB connection)."""
    try:
        conn = await get_db_connection()
        recovered = await recover_stuck_jobs(conn, timeout_hours=STUCK_JOB_AGE_HOURS)
        if recovered > 0:
            logger.warning(f"Periodic recovery: {recovered} stuck ingestion job(s)")
        else:
            logger.info("Periodic recovery: no stuck ingestion jobs found")
        await conn.close()
    except Exception as e:
        logger.warning(f"Periodic stuck-job recovery failed: {e}")


async def run_failed_retries():
    """Retry failed ingestion jobs (transient errors, retry_count < 3) and stuck running jobs.

    Two categories are picked up:
      1. Failed jobs where error is transient and retry_count < MAX_RETRIES,
         and finished_at is older than FAILED_RETRY_AGE_HOURS.
      2. Running jobs where started_at is older than STUCK_JOB_AGE_HOURS
         (treated as stuck — first recovered, then re-dispatched).
    """
    conn = None
    try:
        conn = await get_db_connection()

        failed_rows = await (
            await conn.execute(
                """
                SELECT id, source, retry_count, error_message
                FROM ingestion_jobs
                WHERE status = 'failed'
                  AND retry_count < %s
                  AND finished_at < NOW() - INTERVAL '1 hour'
                ORDER BY finished_at ASC
                """,
                (MAX_RETRIES,),
            )
        ).fetchall()

        transient_failed = [
            row for row in failed_rows if _is_transient_error(row[3])
        ]

        stuck_rows = await (
            await conn.execute(
                """
                SELECT id, source, started_at
                FROM ingestion_jobs
                WHERE status = 'running'
                  AND started_at < NOW() - INTERVAL '2 hours'
                ORDER BY started_at ASC
                """
            )
        ).fetchall()

        if not transient_failed and not stuck_rows:
            logger.info("No failed/stuck ingestion jobs to retry")
            await conn.close()
            return

        logger.info(
            f"Retry scan: {len(transient_failed)} transient-failed, "
            f"{len(stuck_rows)} stuck running jobs"
        )

        for job_id, source, retry_count, error_message in transient_failed:
            await conn.execute(
                "UPDATE ingestion_jobs SET retry_count = retry_count + 1 WHERE id = %s",
                (job_id,),
            )
            await conn.close()
            conn = None

            jitter = random.uniform(0, 5)
            logger.info(
                f"Retrying failed job id={job_id} source={source} "
                f"(retry {retry_count + 1}/{MAX_RETRIES}, "
                f"error={error_message!r:.80}, jitter={jitter:.1f}s)"
            )
            await asyncio.sleep(jitter)
            try:
                await run_source(registry, source)
                logger.info(f"Retry succeeded: job id={job_id} source={source}")
            except Exception as e:
                logger.error(
                    f"Retry failed: job id={job_id} source={source}: {e}",
                    exc_info=True,
                )

            conn = await get_db_connection()

        for job_id, source, started_at in stuck_rows:
            await recover_stuck_jobs(conn, timeout_hours=STUCK_JOB_AGE_HOURS)
            await conn.execute(
                "UPDATE ingestion_jobs SET retry_count = retry_count + 1 WHERE id = %s",
                (job_id,),
            )
            await conn.close()
            conn = None

            jitter = random.uniform(0, 5)
            logger.warning(
                f"Retrying stuck job id={job_id} source={source} "
                f"(started {started_at}, jitter={jitter:.1f}s)"
            )
            await asyncio.sleep(jitter)
            try:
                await run_source(registry, source)
                logger.info(f"Stuck-job retry succeeded: job id={job_id} source={source}")
            except Exception as e:
                logger.error(
                    f"Stuck-job retry failed: job id={job_id} source={source}: {e}",
                    exc_info=True,
                )

            conn = await get_db_connection()

        await conn.close()
    except Exception as e:
        logger.error(f"run_failed_retries error: {e}", exc_info=True)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


async def pipeline_health_check():
    """Run alerting threshold checks every 5 minutes and dispatch alerts."""
    try:
        conn = await get_db_connection()
        alerts = await run_all_checks(conn)
        critical = sum(1 for a in alerts if a.level.value == "critical")
        warn = sum(1 for a in alerts if a.level.value == "warn")
        if critical or warn:
            logger.warning(
                f"Pipeline health check: {len(alerts)} alert(s) "
                f"({critical} critical, {warn} warn)"
            )
        else:
            logger.info("Pipeline health check: all clear")
        await conn.close()
    except Exception as e:
        logger.warning(f"Pipeline health check failed: {e}")


async def main():
    scheduler = AsyncIOScheduler()

    # Run initial backups on startup — config first so a fresh deploy lands
    # the docker-compose + secrets bundle before any KB rows are produced.
    logger.info("Running initial backups…")
    await run_config_backup()
    await run_backup_incremental()

    # Recover stuck jobs before starting the scheduler
    try:
        conn = await get_db_connection()
        recovered = await recover_stuck_jobs(conn)
        if recovered > 0:
            logger.warning(f"Recovered {recovered} stuck ingestion job(s) on startup")
        else:
            logger.info("No stuck ingestion jobs found on startup")
        await conn.close()
    except Exception as e:
        logger.warning(f"Could not recover stuck jobs on startup: {e}")

    scheduler.add_job(run_news, CronTrigger(hour=6, minute=0), id="news")
    scheduler.add_job(run_arxiv, CronTrigger(day_of_week="mon", hour=3), id="arxiv")
    scheduler.add_job(run_biorxiv, CronTrigger(day_of_week="tue", hour=3), id="biorxiv")
    scheduler.add_job(run_forex, CronTrigger(hour=7, minute=0), id="forex")
    scheduler.add_job(run_world_bank, CronTrigger(day_of_week="sun", hour=4), id="world_bank")
    scheduler.add_job(
        run_wikipedia_updates,
        CronTrigger(day_of_week="sun", hour=2),
        id="wikipedia_updates",
    )
    # Joplin sync is now handled by joplin_watcher.py (polls every 30s, triggers on change)
    # Safety fallback: run every 6h in case watcher misses something
    scheduler.add_job(run_joplin, CronTrigger(hour="*/6"), id="joplin_safety")

    # KB backups: hourly incremental + weekly full Sunday 02:30 UTC
    # (Sunday 02:30 lands BEFORE pgbackrest's Sunday 03:00 full so both reference
    # the same approximate state.)
    scheduler.add_job(run_backup_incremental, CronTrigger(minute=15), id="kb_incremental")
    scheduler.add_job(run_backup_full, CronTrigger(day_of_week="sun", hour=2, minute=30),
                      id="kb_full")

    # Volume sync: daily at 04:30 UTC (after pgbackrest expire window)
    scheduler.add_job(run_volume_sync, CronTrigger(hour=4, minute=30), id="volume_sync")

    # Config backup: daily at 01:00 UTC + weekly Sunday with full snapshot
    scheduler.add_job(run_config_backup, CronTrigger(hour=1, minute=0), id="config_backup")

    scheduler.add_job(
        run_failed_retries,
        IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES),
        id="retry_failed_ingestion",
    )
    scheduler.add_job(
        recover_stuck_jobs_wrapper,
        IntervalTrigger(hours=STUCK_RECOVERY_INTERVAL_HOURS),
        id="stuck_job_recovery",
    )
    scheduler.add_job(
        pipeline_health_check,
        IntervalTrigger(minutes=5),
        id="pipeline_health",
    )

    scheduler.start()
    logger.info(
        "Scheduler started. Jobs: news=daily@06:00 | arxiv=Mon@03:00 | "
        "biorxiv=Tue@03:00 | wikipedia=Sun@02:00 | forex=daily@07:00 | "
        "world_bank=Sun@04:00 | joplin_safety=every 6h | "
        "kb_incremental=hourly@:15 | kb_full=Sun@02:30 | "
        "volume_sync=daily@04:30 | config_backup=daily@01:00 | "
        f"retry_failed=every {RETRY_INTERVAL_MINUTES}min | "
        f"stuck_recovery=every {STUCK_RECOVERY_INTERVAL_HOURS}h | "
        f"pipeline_health=every 5min"
    )
    logger.info(
        "Wikipedia updates are gated — will not run until dump seed is marked done in ingestion_jobs."
    )

    # Seed current events immediately on startup
    logger.info("Running initial news ingestion on startup…")
    await run_news()

    # Launch Joplin sync watcher as a concurrent background task
    watcher_task = asyncio.create_task(_run_joplin_watcher())

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        watcher_task.cancel()
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())