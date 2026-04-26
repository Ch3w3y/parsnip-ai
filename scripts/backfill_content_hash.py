#!/usr/bin/env python3
"""
backfill_content_hash.py — Populate content_hash for existing knowledge_chunks rows.

Iterates over rows WHERE content_hash IS NULL in batches, computes SHA-256
via ingestion.utils.compute_content_hash(), and writes the hash back.

Idempotent: only touches rows with NULL content_hash. Re-running picks up
where the last run left off.

Usage:
    python scripts/backfill_content_hash.py
    python scripts/backfill_content_hash.py --batch-size 500
    python scripts/backfill_content_hash.py --dry-run
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Import compute_content_hash from ingestion ────────────────────────────────
# Add ingestion/ to sys.path so we can import utils without a package install.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "ingestion"))

from utils import compute_content_hash  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_remaining(cur) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE content_hash IS NULL"
    )
    return cur.fetchone()[0]


def backfill(
    database_url: str,
    batch_size: int = 1000,
    sleep_seconds: float = 0.02,
    dry_run: bool = False,
):
    logger.info("Starting content_hash backfill (batch_size=%s, dry_run=%s)", batch_size, dry_run)

    conn = psycopg.connect(database_url)
    conn.autocommit = True

    try:
        # Advisory lock so only one backfill runs at a time
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('backfill_content_hash'))")
            if not cur.fetchone()[0]:
                raise RuntimeError("Another content_hash backfill is already running.")

            remaining = _count_remaining(cur)
            logger.info("Rows with NULL content_hash at start: %s", remaining)

        if remaining == 0:
            logger.info("Nothing to backfill.")
            return

        total_updated = 0
        total_errors = 0
        batches = 0

        while True:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content
                    FROM knowledge_chunks
                    WHERE content_hash IS NULL
                    ORDER BY id
                    LIMIT %s
                    """,
                    (batch_size,),
                )
                rows = cur.fetchall()

            if not rows:
                logger.info("Backfill complete.")
                break

            updated_in_batch = 0
            errors_in_batch = 0

            for row_id, content in rows:
                try:
                    if content is None:
                        logger.warning("Row id=%s has NULL content — skipping", row_id)
                        errors_in_batch += 1
                        continue

                    content_hash = compute_content_hash(content)

                    if not dry_run:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE knowledge_chunks
                                SET content_hash = %s
                                WHERE id = %s AND content_hash IS NULL
                                """,
                                (content_hash, row_id),
                            )
                            # rowcount 0 means another process set the hash first
                            if cur.rowcount > 0:
                                updated_in_batch += 1
                    else:
                        updated_in_batch += 1
                except Exception as exc:
                    logger.error("Failed to update row id=%s: %s", row_id, exc)
                    errors_in_batch += 1

            total_updated += updated_in_batch
            total_errors += errors_in_batch
            batches += 1

            if dry_run:
                logger.info(
                    "[DRY-RUN] Batch %s: %s rows would be updated, %s errors",
                    batches, updated_in_batch, errors_in_batch,
                )
            else:
                logger.info(
                    "Batch %s: updated %s rows, %s errors (total updated: %s)",
                    batches, updated_in_batch, errors_in_batch, total_updated,
                )

            time.sleep(sleep_seconds)

        logger.info(
            "Backfill finished. Total updated: %s, errors: %s across %s batches.",
            total_updated, total_errors, batches,
        )

    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext('backfill_content_hash'))")
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill content_hash for knowledge_chunks rows with NULL hashes."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DSN (default: $DATABASE_URL from .env)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows to fetch per batch (default: 1000)",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.02,
        help="Pause between batches in seconds (default: 0.02)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute hashes but do not write to the database",
    )
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit(
            "DATABASE_URL is not set. Configure .env or pass --database-url."
        )

    backfill(
        args.database_url,
        batch_size=args.batch_size,
        sleep_seconds=args.sleep_seconds,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()