#!/usr/bin/env python3
"""
Forex ingestion from Frankfurter API (free, no API key, 164 currencies).

Fetches daily FX rates, stores structured data in forex_rates table
(for analysis scripts), and creates text chunks in knowledge_chunks
(for semantic KB search).

Usage:
    python ingest_forex.py                          # latest rates + 90-day trend, all currencies
    python ingest_forex.py --pairs EUR/USD,GBP/USD  # specific pairs
    python ingest_forex.py --days 30                # 30-day lookback
    python ingest_forex.py --from-raw               # replay from latest raw file
    python ingest_forex.py --from-raw path          # replay from specific file
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from tqdm import tqdm

from utils import (
    embed_batch,
    bulk_upsert_chunks,
    cleanup_orphan_chunks,
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

FRANKFURTER_API = "https://api.frankfurter.dev/v2"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
BATCH_SIZE = 64
DATABASE_URL = os.environ.get("DATABASE_URL", "")
RATE_DELAY = 0.5  # seconds between API calls (Frankfurter is free, no key needed)

# Bases used when fetching all currencies — each base gets all other currencies as quotes
ALL_CURRENCY_BASES = ["USD", "EUR", "GBP", "JPY"]


async def fetch_currencies(client: httpx.AsyncClient) -> list[str]:
    """Return all currency codes supported by Frankfurter API."""
    r = await client.get(f"{FRANKFURTER_API}/currencies", timeout=30)
    r.raise_for_status()
    return [c["iso_code"] for c in r.json()]


async def fetch_timeseries(client: httpx.AsyncClient, base: str, quotes: list[str],
                            start: str, end: str) -> list[dict]:
    """Fetch a time series of rates. v2 returns a flat list of objects."""
    params = {"base": base, "quotes": ",".join(quotes), "from": start, "to": end}
    r = await client.get(f"{FRANKFURTER_API}/rates", params=params, timeout=300)
    r.raise_for_status()
    return r.json()


FRANKFURTER_ORIGIN = "1999-01-04"  # EUR launch date — earliest comprehensive data
CHUNK_DAYS = 90  # split large date ranges into N-day windows to stay under ~800KB response limit


def _date_windows(start: str, end: str, chunk_days: int) -> list[tuple[str, str]]:
    """Split [start, end] into windows of at most chunk_days days."""
    from datetime import date
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    windows = []
    cur = s
    while cur < e:
        nxt = cur + timedelta(days=chunk_days - 1)
        if nxt > e:
            nxt = e
        windows.append((cur.isoformat(), nxt.isoformat()))
        cur = nxt + timedelta(days=1)
    return windows


async def fetch_all(days: int = 30, pairs: list[tuple[str, str]] | None = None,
                    all_history: bool = False) -> list[dict]:
    """Phase 1: Fetch all forex data from Frankfurter API.

    When pairs is None, fetches all available currencies vs ALL_CURRENCY_BASES.
    When all_history=True, fetches from FRANKFURTER_ORIGIN (1999-01-04) regardless of days.
    Large date spans are automatically chunked into CHUNK_YEARS windows to stay under
    Frankfurter's ~2.8MB response limit.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if all_history:
        start = FRANKFURTER_ORIGIN
        label = "all history"
    else:
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        label = f"{days}d"

    async with httpx.AsyncClient() as client:
        if pairs is None:
            all_currencies = await fetch_currencies(client)
            logger.info(f"Discovered {len(all_currencies)} currencies from Frankfurter API")

            bases: dict[str, list[str]] = {
                base: [c for c in all_currencies if c != base]
                for base in ALL_CURRENCY_BASES
            }
        else:
            bases = {}
            for base, quote in pairs:
                bases.setdefault(base, []).append(quote)

        chunk = CHUNK_DAYS if (all_history or days > CHUNK_DAYS) else days
        windows = _date_windows(start, today, chunk)
        total_calls = len(bases) * len(windows)
        logger.info(f"Fetching {len(bases)} bases × {len(windows)} date windows = {total_calls} API calls ({label})")

        records = []

        for base, quotes in bases.items():
            base_count = 0
            for win_start, win_end in windows:
                try:
                    rows = await fetch_timeseries(client, base, quotes, win_start, win_end)
                    for row in rows:
                        records.append({
                            "date": row["date"],
                            "base": row["base"],
                            "quote": row["quote"],
                            "rate": float(row["rate"]),
                            "pair": f"{row['base']}/{row['quote']}",
                        })
                    base_count += len(rows)
                except Exception as e:
                    logger.error(f"Failed {base} {win_start}–{win_end}: {e}")
                    continue
                await asyncio.sleep(RATE_DELAY)
            logger.info(f"{base}: {base_count} observations fetched")

    logger.info(f"Fetched {len(records)} total rate observations")
    return records


async def upsert_forex_rates(conn, records: list[dict]) -> int:
    """Insert/update structured rate data in forex_rates table."""
    if not records:
        return 0

    sql = """
        INSERT INTO forex_rates (pair, base_ccy, quote_ccy, rate, rate_date)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (pair, rate_date)
        DO UPDATE SET rate = EXCLUDED.rate, fetched_at = NOW()
    """

    params = [
        (r["pair"], r["base"], r["quote"], r["rate"], r["date"])
        for r in records
    ]

    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.executemany(sql, params)

    return len(records)


def records_to_chunks(records: list[dict]) -> list[dict]:
    """Convert rate records to searchable text chunks with metadata.

    Each chunk is a natural-language description of a currency pair's rate
    and recent trend, suitable for embedding and KB search.
    """
    # Group by pair for trend analysis
    pair_data: dict[str, list[dict]] = {}
    for r in records:
        pair_data.setdefault(r["pair"], []).append(r)

    chunks = []

    for pair, observations in pair_data.items():
        base, quote = pair.split("/")
        observations.sort(key=lambda x: x["date"])

        latest = observations[-1]
        latest_rate = latest["rate"]
        latest_date = latest["date"]

        # Calculate trend if we have multiple days
        if len(observations) > 1:
            first_rate = observations[0]["rate"]
            change_pct = ((latest_rate - first_rate) / first_rate) * 100
            direction = "strengthened" if change_pct > 0 else "weakened" if change_pct < 0 else "unchanged"
            trend_text = f"Over the past {len(observations)} days, the {base} has {direction} {abs(change_pct):.3f}% against the {quote}."
            min_rate = min(o["rate"] for o in observations)
            max_rate = max(o["rate"] for o in observations)
            range_text = f"Range: {min_rate:.6f} – {max_rate:.6f}."
        else:
            trend_text = ""
            range_text = ""

        # Build natural-language chunk
        text = (
            f"Foreign exchange rate for {pair} on {latest_date}: "
            f"1 {base} = {latest_rate:.6f} {quote}. "
            f"{base} is the base currency, {quote} is the quote currency. "
            f"{trend_text} {range_text}".strip()
        )

        source_id = f"{pair}::{latest_date}"

        chunks.append({
            "source_id": source_id,
            "text": text,
            "metadata": {
                "pair": pair,
                "base": base,
                "quote": quote,
                "rate": latest_rate,
                "date": latest_date,
                "days": len(observations),
                "url": f"https://api.frankfurter.dev/v2/rate/{base}/{quote}",
            },
        })

    return chunks


async def process_records(records: list[dict]):
    """Phase 2: Write to forex_rates table + chunk + embed + upsert to KB."""
    conn = None
    job_id = None
    try:
        conn = await get_db_connection()
        job_id = await create_job(conn, "forex")
        await conn.commit()

        # ── 1. Write structured data to forex_rates table ────────────────────
        rate_count = await upsert_forex_rates(conn, records)
        logger.info(f"Upserted {rate_count} rows into forex_rates table")

        # ── 2. Create KB text chunks ─────────────────────────────────────────
        chunks = records_to_chunks(records)
        logger.info(f"Created {len(chunks)} forex KB chunks")

        total_kb = 0
        pending_texts: list[str] = []
        pending_chunks: list[dict] = []

        async def flush_batch():
            nonlocal total_kb
            if not pending_texts:
                return

            embeddings = await embed_batch(pending_texts)
            if embeddings is None:
                logger.error(f"Embedding failed for batch of {len(pending_texts)}")
                pending_texts.clear()
                pending_chunks.clear()
                return

            bulk_rows = [
                (
                    "forex",
                    chunk["source_id"],
                    0,
                    text,
                    chunk["metadata"],
                    emb,
                    EMBED_MODEL,
                )
                for chunk, text, emb in zip(pending_chunks, pending_texts, embeddings)
                if emb is not None
            ]

            inserted = await bulk_upsert_chunks(conn, bulk_rows, on_conflict="update")
            total_kb += inserted

            for chunk in pending_chunks:
                await cleanup_orphan_chunks(conn, "forex", chunk["source_id"], 1)

            pending_texts.clear()
            pending_chunks.clear()

        with tqdm(total=len(chunks), desc="Forex KB chunks", unit="chunk") as pbar:
            for chunk in chunks:
                pending_texts.append(chunk["text"])
                pending_chunks.append(chunk)

                if len(pending_texts) >= BATCH_SIZE:
                    await flush_batch()
                    pbar.update(BATCH_SIZE)

            await flush_batch()
            pbar.update(len(pending_texts))

        await update_job_progress(conn, job_id, total_kb)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again

        logger.info(f"Done! {rate_count} forex_rates rows | {total_kb} KB chunks")
    except Exception as exc:
        logger.error(f"forex ingestion failed: {exc}", exc_info=True)
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
    parser = argparse.ArgumentParser(description="Ingest forex rates from Frankfurter API")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch (default: 30)")
    parser.add_argument("--all-history", action="store_true",
                        help=f"Fetch all available history from {FRANKFURTER_ORIGIN} (overrides --days)")
    parser.add_argument("--pairs", type=str, default=None,
                        help="Comma-separated pairs (e.g. EUR/USD,GBP/USD). Default: all currencies")
    parser.add_argument("--from-raw", type=str, nargs="?", const="LATEST", default=None,
                        help="Replay from saved raw file (omit for latest)")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Copy .env.example to .env and configure it.")
        return

    pairs = None
    if args.pairs:
        pairs = [tuple(p.strip().split("/")) for p in args.pairs.split(",")]

    if args.from_raw:
        if args.from_raw == "LATEST":
            raw_path = latest_raw("forex")
            if not raw_path:
                logger.error("No saved raw forex data found")
                return
        else:
            raw_path = Path(args.from_raw)
        logger.info(f"Replaying from {raw_path}")
        records = list(iter_raw(raw_path))
        asyncio.run(process_records(records))
    else:
        async def run():
            records = await fetch_all(days=args.days, pairs=pairs, all_history=args.all_history)
            if not records:
                logger.error("No forex data fetched")
                return
            label = "all" if args.all_history else f"{args.days}d"
            save_raw(records, "forex", label=label)
            await process_records(records)

        asyncio.run(run())


if __name__ == "__main__":
    main()


async def main_async(days: int = 30, pairs: list[tuple[str, str]] | None = None,
                     all_history: bool = False):
    """Async entrypoint for scheduler."""
    records = await fetch_all(days=days, pairs=pairs, all_history=all_history)
    if not records:
        logger.error("No forex data fetched")
        return
    label = "all" if all_history else f"{days}d"
    save_raw(records, "forex", label=label)
    await process_records(records)
