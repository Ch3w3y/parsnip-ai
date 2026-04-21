#!/usr/bin/env python3
"""
World Bank data ingestion — macro indicators for economic analysis.

Fetches key indicators (GDP, inflation, trade, debt, employment) for
configurable countries. Stores structured data in world_bank_data table
(for analysis queries) and creates text chunks in knowledge_chunks
(for semantic KB search).

Usage:
    python ingest_worldbank.py                              # default: 20 countries, 20 years
    python ingest_worldbank.py --all-countries              # all ~200 countries (14 API calls)
    python ingest_worldbank.py --countries BRA,GBR,USA      # specific countries
    python ingest_worldbank.py --years 30                   # 30-year lookback
    python ingest_worldbank.py --from-raw                   # replay from latest raw file
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from tqdm import tqdm

from utils import (
    embed_batch,
    bulk_upsert_chunks,
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

WB_API = "https://api.worldbank.org/v2"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_SIZE = 64

# Key macro indicators — the ones most useful for cross-referencing with forex
INDICATORS = {
    "NY.GDP.MKTP.CD":       "GDP (current US$)",
    "NY.GDP.MKTP.KD.ZG":    "GDP growth (annual %)",
    "NY.GDP.PCAP.CD":       "GDP per capita (current US$)",
    "FP.CPI.TOTL.ZG":       "Inflation, consumer prices (annual %)",
    "NE.EXP.GNFS.CD":       "Exports of goods and services (current US$)",
    "NE.IMP.GNFS.CD":       "Imports of goods and services (current US$)",
    "BN.CAB.XOKA.CD":       "Current account balance (BoP, current US$)",
    "PA.NUS.FCRF":          "Official exchange rate (LCU per US$)",
    "FR.INR.RINR":          "Real interest rate (%)",
    "SL.UEM.TOTL.ZS":       "Unemployment (% of labor force)",
    "GC.DOD.TOTL.GD.ZS":    "Central government debt (% of GDP)",
    "NY.GNS.ICTR.ZS":       "Gross savings (% of GDP)",
    "DT.DOD.DECT.CD":       "External debt stocks (current US$)",
    "FM.LBL.BMNY.GD.ZS":    "Broad money (% of GDP)",
}

# Default countries — major economies + forex-relevant
DEFAULT_COUNTRIES = [
    "USA", "GBR", "EUR",  # majors
    "BRA", "CHN", "IND", "JPN", "KOR",  # BRICS + Asia
    "AUS", "NZL", "CAN", "MEX",  # commodity / US neighbors
    "ZAF", "TUR", "IDR", "THA", "MYS", "SGD",  # EM
    "NOR", "SWE", "DNK", "CHE",  # Europe
]


async def fetch_indicator_all_countries(client: httpx.AsyncClient,
                                        indicator: str, years: int) -> list[dict]:
    """Fetch one indicator for ALL countries in a single paginated API call."""
    end_year = datetime.now().year
    start_year = end_year - years

    url = (f"{WB_API}/country/all/indicator/{indicator}"
           f"?date={start_year}:{end_year}&format=json&per_page=20000")

    try:
        r = await client.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()

        if not isinstance(data, list) or len(data) < 2:
            return []

        records = data[1] if isinstance(data[1], list) else []
        results = []

        for rec in records:
            if rec.get("value") is not None:
                results.append({
                    "country_code": rec.get("countryiso3code", ""),
                    "country_name": rec.get("country", {}).get("value", ""),
                    "indicator_code": indicator,
                    "indicator_name": INDICATORS.get(indicator, indicator),
                    "year": int(rec.get("date", 0)),
                    "value": float(rec["value"]),
                    "unit": "",
                })

        return results

    except Exception as e:
        logger.warning(f"Failed all-countries/{indicator}: {e}")
        return []


async def fetch_indicator(client: httpx.AsyncClient, country: str,
                           indicator: str, years: int) -> list[dict]:
    """Fetch one indicator for one country from World Bank API."""
    end_year = datetime.now().year
    start_year = end_year - years

    url = (f"{WB_API}/country/{country}/indicator/{indicator}"
           f"?date={start_year}:{end_year}&format=json&per_page=1000")

    try:
        r = await client.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()

        # API returns [metadata, data] where data is a list of records
        if not isinstance(data, list) or len(data) < 2:
            return []

        records = data[1] if isinstance(data[1], list) else []
        results = []

        for rec in records:
            if rec.get("value") is not None:
                results.append({
                    "country_code": rec.get("countryiso3code", country),
                    "country_name": rec.get("country", {}).get("value", country),
                    "indicator_code": indicator,
                    "indicator_name": INDICATORS.get(indicator, indicator),
                    "year": int(rec.get("date", 0)),
                    "value": float(rec["value"]),
                    "unit": "",
                })

        return results

    except Exception as e:
        logger.warning(f"Failed {country}/{indicator}: {e}")
        return []


async def fetch_all(countries: list[str] | None = None,
                    years: int = 20,
                    all_countries: bool = False) -> list[dict]:
    """Phase 1: Fetch all indicators.

    When all_countries=True, uses the WB 'all' endpoint — one call per indicator
    (~14 total) instead of one per (country, indicator) pair.
    """
    records = []

    async with httpx.AsyncClient() as client:
        if all_countries:
            with tqdm(total=len(INDICATORS), desc="WB API calls (all countries)", unit="call") as pbar:
                for indicator in INDICATORS:
                    rows = await fetch_indicator_all_countries(client, indicator, years)
                    records.extend(rows)
                    pbar.update(1)
        else:
            target_countries = countries if countries is not None else DEFAULT_COUNTRIES
            total_calls = len(target_countries) * len(INDICATORS)

            with tqdm(total=total_calls, desc="WB API calls", unit="call") as pbar:
                for country in target_countries:
                    for indicator in INDICATORS:
                        rows = await fetch_indicator(client, country, indicator, years)
                        records.extend(rows)
                        pbar.update(1)

    # Filter out aggregate regions (no ISO 3-letter country code)
    if all_countries:
        before = len(records)
        records = [r for r in records if len(r["country_code"]) == 3 and r["country_code"]]
        logger.info(f"Filtered {before - len(records)} regional aggregates; {len(records)} country data points remain")

    logger.info(f"Fetched {len(records)} data points")
    return records


async def upsert_wb_data(conn, records: list[dict]) -> int:
    """Insert/update structured data in world_bank_data table."""
    if not records:
        return 0

    sql = """
        INSERT INTO world_bank_data
            (country_code, country_name, indicator_code, indicator_name, year, value, unit)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (country_code, indicator_code, year)
        DO UPDATE SET
            value = EXCLUDED.value,
            country_name = EXCLUDED.country_name,
            indicator_name = EXCLUDED.indicator_name,
            fetched_at = NOW()
    """

    params = [
        (r["country_code"], r["country_name"], r["indicator_code"],
         r["indicator_name"], r["year"], r["value"], r["unit"])
        for r in records
    ]

    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.executemany(sql, params)

    return len(records)


def records_to_chunks(records: list[dict]) -> list[dict]:
    """Convert WB data to searchable text chunks grouped by country."""
    # Group by country
    country_data: dict[str, list[dict]] = {}
    for r in records:
        country_data.setdefault(r["country_code"], []).append(r)

    chunks = []

    for code, observations in country_data.items():
        # Group by indicator for this country
        indicators: dict[str, list[dict]] = {}
        for obs in observations:
            indicators.setdefault(obs["indicator_code"], []).append(obs)

        country_name = observations[0]["country_name"]
        latest_year = max(o["year"] for o in observations)

        # Build a summary text chunk per country
        lines = [f"World Bank macro indicators for {country_name} ({code}):"]

        for ind_code, obs_list in sorted(indicators.items()):
            obs_list.sort(key=lambda x: x["year"])
            latest = obs_list[-1]
            ind_name = latest["indicator_name"]

            # Format value nicely
            val = latest["value"]
            if abs(val) >= 1e12:
                val_str = f"${val/1e12:.2f} trillion"
            elif abs(val) >= 1e9:
                val_str = f"${val/1e9:.2f} billion"
            elif abs(val) >= 1e6:
                val_str = f"${val/1e6:.2f} million"
            else:
                val_str = f"{val:.2f}"

            trend = ""
            if len(obs_list) > 1:
                prev = obs_list[-2]
                if prev["value"] and prev["value"] != 0:
                    pct = ((val - prev["value"]) / abs(prev["value"])) * 100
                    direction = "up" if pct > 0 else "down"
                    trend = f" ({direction} {abs(pct):.1f}% YoY)"

            lines.append(f"- {ind_name}: {val_str} ({latest['year']}){trend}")

        text = "\n".join(lines)
        source_id = f"{code}::{latest_year}"

        chunks.append({
            "source_id": source_id,
            "text": text,
            "metadata": {
                "country_code": code,
                "country_name": country_name,
                "latest_year": latest_year,
                "indicators": list(indicators.keys()),
                "url": f"https://data.worldbank.org/country/{code.lower()}",
            },
        })

    return chunks


async def process_records(records: list[dict]):
    """Phase 2: Write to world_bank_data table + chunk + embed + upsert to KB."""
    conn = await get_db_connection()
    job_id = await create_job(conn, "world_bank")
    await conn.commit()

    # 1. Structured data
    count = await upsert_wb_data(conn, records)
    logger.info(f"Upserted {count} rows into world_bank_data")

    # 2. KB chunks
    chunks = records_to_chunks(records)
    logger.info(f"Created {len(chunks)} World Bank KB chunks")

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
                "world_bank",
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
        pending_texts.clear()
        pending_chunks.clear()

    with tqdm(total=len(chunks), desc="WB KB chunks", unit="chunk") as pbar:
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
    await conn.close()

    logger.info(f"Done! {count} world_bank_data rows | {total_kb} KB chunks")


def main():
    parser = argparse.ArgumentParser(description="Ingest World Bank macro indicators")
    parser.add_argument("--countries", type=str, default=None,
                        help="Comma-separated ISO 3-letter codes (e.g. BRA,GBR,USA)")
    parser.add_argument("--all-countries", action="store_true",
                        help="Fetch all ~200 countries (14 API calls via WB 'all' endpoint)")
    parser.add_argument("--years", type=int, default=20, help="Years of history (default: 20)")
    parser.add_argument("--from-raw", type=str, nargs="?", const="LATEST", default=None,
                        help="Replay from saved raw file")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        return

    countries = args.countries.split(",") if args.countries else None

    if args.from_raw:
        if args.from_raw == "LATEST":
            raw_path = latest_raw("world_bank")
            if not raw_path:
                logger.error("No saved raw World Bank data found")
                return
        else:
            raw_path = Path(args.from_raw)
        logger.info(f"Replaying from {raw_path}")
        records = list(iter_raw(raw_path))
        asyncio.run(process_records(records))
    else:
        async def run():
            records = await fetch_all(countries=countries, years=args.years,
                                      all_countries=args.all_countries)
            if not records:
                logger.error("No data fetched")
                return
            label = "all" if args.all_countries else f"{args.years}y"
            save_raw(records, "world_bank", label=label)
            await process_records(records)

        asyncio.run(run())


if __name__ == "__main__":
    main()


async def main_async(countries: list[str] | None = None, years: int = 20,
                     all_countries: bool = False):
    """Async entrypoint for scheduler."""
    records = await fetch_all(countries=countries, years=years, all_countries=all_countries)
    if not records:
        logger.error("No World Bank data fetched")
        return
    label = "all" if all_countries else f"{years}y"
    save_raw(records, "world_bank", label=label)
    await process_records(records)
