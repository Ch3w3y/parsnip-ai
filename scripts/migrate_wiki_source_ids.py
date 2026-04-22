#!/usr/bin/env python3
import argparse
import logging
import os
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _count_remaining(cur) -> int:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM knowledge_chunks
        WHERE source = 'wikipedia'
          AND source_id ~ '::[0-9]+$'
        """
    )
    return cur.fetchone()[0]


def migrate_in_batches(
    database_url: str,
    batch_size: int = 50000,
    sleep_seconds: float = 0.05,
    count_every: int = 5,
):
    logger.info("Starting Wikipedia source_id migration in batches...")
    conn = psycopg.connect(database_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('wiki_source_id_migration'))")
            if not cur.fetchone()[0]:
                raise RuntimeError("Another Wikipedia source_id migration is already running.")

            remaining = _count_remaining(cur)
            logger.info("Rows remaining at start: %s", remaining)

        total_updated = 0
        batches = 0
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH batch AS (
                        SELECT
                            id,
                            regexp_replace(source_id, '::[0-9]+$', '') AS new_source_id,
                            substring(source_id from '::([0-9]+)$')::int AS new_chunk_index
                        FROM knowledge_chunks
                        WHERE source = 'wikipedia'
                          AND source_id ~ '::[0-9]+$'
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE knowledge_chunks
                    SET source_id = batch.new_source_id,
                        chunk_index = batch.new_chunk_index
                    FROM batch
                    WHERE knowledge_chunks.id = batch.id;
                    """,
                    (batch_size,),
                )

                updated = cur.rowcount
                total_updated += updated
                batches += 1

                if updated == 0:
                    logger.info("Migration complete.")
                    break

                if batches % count_every == 0:
                    remaining = _count_remaining(cur)
                    logger.info(
                        "Updated %s rows. Total this run: %s. Remaining: %s",
                        updated,
                        total_updated,
                        remaining,
                    )
                else:
                    logger.info(
                        "Updated %s rows. Total this run: %s",
                        updated,
                        total_updated,
                    )

            time.sleep(sleep_seconds)
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext('wiki_source_id_migration'))")
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Normalize legacy Wikipedia source_id values.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--count-every", type=int, default=5)
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is not set. Configure .env or pass --database-url.")

    migrate_in_batches(
        args.database_url,
        batch_size=args.batch_size,
        sleep_seconds=args.sleep_seconds,
        count_every=args.count_every,
    )


if __name__ == "__main__":
    main()
