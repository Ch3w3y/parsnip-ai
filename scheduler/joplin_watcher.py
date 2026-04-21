"""
Joplin Sync Watcher — triggers incremental ingestion only when a sync occurs.

Polls the Joplin Server API every 30s for the latest item updated_time.
When it detects a change, runs ingest_joplin.py (incremental mode).
Falls back to a safety check every 6h in case a sync was missed.

Usage:
    python joplin_watcher.py
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [watcher] %(message)s",
)
logger = logging.getLogger("joplin_watcher")

JOPLIN_URL = os.environ.get("JOPLIN_SERVER_URL", "http://localhost:22300")
JOPLIN_EMAIL = os.environ.get("JOPLIN_ADMIN_EMAIL", "")
JOPLIN_PASS = os.environ.get("JOPLIN_ADMIN_PASSWORD", "")

POLL_INTERVAL = 30  # seconds between sync checks
SAFETY_INTERVAL = 6 * 3600  # 6 hours — force check even if no change detected
INGESTION_TIMEOUT = 300  # max seconds for ingestion to complete

# Add ingestion directory to path so we can import ingest_joplin
INGESTION_DIR = Path(__file__).parent / "ingestion"
sys.path.insert(0, str(INGESTION_DIR))


async def get_latest_updated_time(http: httpx.AsyncClient, token: str) -> int:
    """Fetch the latest updated_time from root items."""
    latest = 0
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = await http.get(
            f"{JOPLIN_URL}/api/items/root/children",
            headers={"X-API-AUTH": token},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            updated = item.get("updated_time", 0)
            if updated > latest:
                latest = updated
        cursor = data.get("cursor")
        if not data.get("has_more"):
            break
    return latest


async def authenticate(http: httpx.AsyncClient) -> str:
    """Authenticate and return session token."""
    r = await http.post(
        f"{JOPLIN_URL}/api/sessions",
        json={"email": JOPLIN_EMAIL, "password": JOPLIN_PASS},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["id"]


async def run_ingestion():
    """Run the incremental Joplin ingestion."""
    try:
        import ingest_joplin

        await ingest_joplin.main_async(full=False)
    except SystemExit:
        logger.warning("Ingestion skipped — Joplin not running or token not set.")
    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)


async def main():
    if not JOPLIN_EMAIL or not JOPLIN_PASS:
        logger.error("JOPLIN_ADMIN_EMAIL and JOPLIN_ADMIN_PASSWORD must be set in .env")
        sys.exit(1)

    logger.info(
        f"Starting Joplin sync watcher (poll every {POLL_INTERVAL}s, safety check every {SAFETY_INTERVAL}s)"
    )

    async with httpx.AsyncClient() as http:
        try:
            token = await authenticate(http)
        except Exception as e:
            logger.error(f"Auth failed ({JOPLIN_URL}): {e}")
            logger.error("Is joplin-server running? Check: docker compose ps")
            sys.exit(1)

        logger.info(f"Authenticated to Joplin Server")

        # Get initial state
        last_updated = await get_latest_updated_time(http, token)
        logger.info(f"Initial sync state: latest updated_time={last_updated}")

        last_ingestion_time = time.time()

        while True:
            try:
                current_updated = await get_latest_updated_time(http, token)
                now = time.time()

                if current_updated > last_updated:
                    logger.info(f"Sync detected! ({last_updated} → {current_updated})")
                    await run_ingestion()
                    last_updated = current_updated
                    last_ingestion_time = now
                elif now - last_ingestion_time >= SAFETY_INTERVAL:
                    logger.info(f"Safety check ({SAFETY_INTERVAL // 3600}h elapsed)")
                    await run_ingestion()
                    last_ingestion_time = now

            except Exception as e:
                logger.warning(f"Poll failed (will retry): {e}")

            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
