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
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from ingestion.registry import SourceRegistry
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

# Mutex: prevent concurrent Wikipedia writes (dump seed vs incremental update)
_wikipedia_lock = asyncio.Lock()


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


async def run_backup():
    """Run knowledge base and Joplin DB backup to GCS."""
    import subprocess
    logger.info("Running KB backup to GCS…")
    try:
        # Increased timeout to 1h for large KB
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "scripts" / "backup_kb.py")],
            capture_output=True, text=True, timeout=3600,
        )
        if result.returncode == 0:
            logger.info("KB backup completed successfully.")
        else:
            logger.error(f"KB backup failed: {result.stderr[-500:]}")
    except Exception as e:
        logger.error(f"KB backup error: {e}")


async def run_config_backup():
    """Run project configuration and code backup to GCS."""
    import subprocess
    logger.info("Running config backup to GCS…")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "scripts" / "backup_config.py")],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            logger.info("Config backup completed successfully.")
        else:
            logger.error(f"Config backup failed: {result.stderr[-500:]}")
    except Exception as e:
        logger.error(f"Config backup error: {e}")


async def main():
    scheduler = AsyncIOScheduler()

    # Run initial backups on startup
    logger.info("Running initial backups…")
    await run_config_backup()
    await run_backup()

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

    # KB backups 4x daily: 02:00, 08:00, 14:00, 20:00 UTC
    scheduler.add_job(run_backup, CronTrigger(hour="2,8,14,20", minute=0), id="kb_backup")
    
    # Config backup: daily at 01:00 UTC
    scheduler.add_job(run_config_backup, CronTrigger(hour=1, minute=0), id="config_backup")

    scheduler.start()
    logger.info(
        "Scheduler started. Jobs: news=daily@06:00 | arxiv=Mon@03:00 | "
        "biorxiv=Tue@03:00 | wikipedia=Sun@02:00 | forex=daily@07:00 | "
        "world_bank=Sun@04:00 | joplin_safety=every 6h | backup_kb=4x daily | backup_config=daily@01:00"
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