#!/usr/bin/env python3
"""
ingestion_status.py — command-line ingestion / migration status reporter.

Usage:
    python scripts/ingestion_status.py
    python scripts/ingestion_status.py --json
    python scripts/ingestion_status.py --watch   # poll every 10s until ^C
    python scripts/ingestion_status.py --wiki    # only wikipedia detail
    python scripts/ingestion_status.py --migration # only migration detail

Environment:
    DATABASE_URL (required)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root in path so we can import agent/ingestion_status
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from ingestion_status import (
    get_ingestion_overview,
    get_migration_status,
    get_wikipedia_bulk_status,
    to_dict,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _colour(code: str, text: str) -> str:
    codes = {
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "cyan": "\033[96m",
        "reset": "\033[0m",
        "bold": "\033[1m",
    }
    if not sys.stdout.isatty():
        return text
    return f"{codes.get(code, '')}{text}{codes['reset']}"


def _fmt_bool(v: bool) -> str:
    return _colour("green", "✓ yes") if v else _colour("yellow", "✗ no")


def _fmt_int(n: int | None) -> str:
    if n is None:
        return "N/A"
    return f"{n:,}"


def _print_section(title: str):
    print(f"\n{_colour('bold', title)}")
    print("─" * 50)


# ── renderers ────────────────────────────────────────────────────────────────

def render_overview(overview):
    _print_section("Wikipedia source_id migration")
    m = overview.migration
    print(f"  Running          {_fmt_bool(m.running)}")
    print(f"  PID              {m.pid or 'N/A'}")
    print(f"  Rows remaining   {_colour('red', _fmt_int(m.rows_remaining)) if m.rows_remaining else _colour('green', '0')}")
    print(f"  Anomalous rows   {_fmt_int(m.anomalous_rows)}")
    print(f"  Ready for ingest {_fmt_bool(m.ready_for_ingestion)}")
    if m.last_log_tail:
        print(f"  Last log tail    {m.last_log_tail[:120]}")

    _print_section("Wikipedia bulk ingest")
    b = overview.wikipedia_bulk
    print(f"  Running          {_fmt_bool(b.running)}")
    print(f"  PID              {b.pid or 'N/A'}")
    print(f"  Chunks in KB     {_fmt_int(b.chunks_in_kb)}")
    print(f"  Articles in KB   {_fmt_int(b.articles_in_kb)}")
    print(f"  Last job status  {b.last_job_status or 'N/A'}")
    print(f"  Last processed   {_fmt_int(b.last_job_processed)}")

    _print_section("Recent ingestion jobs")
    if overview.recent_jobs:
        for j in overview.recent_jobs[:6]:
            status_icon = _colour("green", "●") if j["status"] == "done" else _colour("yellow", "⏳") if j["status"] == "running" else _colour("red", "✗")
            total = j["total"] or "?"
            proc = j["processed"] or 0
            print(f"  {status_icon} {j['source']:<16} {j['status']:<10}  {proc}/{total}  started {j['started_at'][:19]}")
    else:
        print("  No jobs found.")

    _print_section("Scheduled sources")
    for source, meta in overview.scheduled_next.items():
        print(f"  {source:<20} next: {meta['next']}")
        print(f"    last job status: {meta['last_job_status']}  processed: {_fmt_int(meta['last_processed'])}")

    print()


def render_compact(overview):
    """One-line summary suitable for piping or cron."""
    m = overview.migration
    b = overview.wikipedia_bulk
    print(
        f"migration={_yes_no(m.running)}"
        f" migration_remaining={m.rows_remaining}"
        f" bulk_running={_yes_no(b.running)}"
        f" wiki_kb_chunks={b.chunks_in_kb}"
        f" wiki_kb_articles={b.articles_in_kb}"
    )


def _yes_no(v: bool) -> str:
    return "yes" if v else "no"


# ── main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Ingestion and migration status reporter")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--watch", action="store_true", help="Poll every 10 seconds until Ctrl-C")
    parser.add_argument("--wiki", action="store_true", help="Show only Wikipedia bulk detail")
    parser.add_argument("--migration", action="store_true", help="Show only migration detail")
    args = parser.parse_args()

    if os.environ.get("DATABASE_URL") is None:
        # Try to source .env manually
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
                    break

    if args.wiki:
        if args.json:
            data = to_dict(await get_ingestion_overview())
            print(json.dumps(data["wikipedia_bulk"], indent=2))
        else:
            b = await get_wikipedia_bulk_status()
            print(f"bulk_running={_yes_no(b.running)}")
            print(f"bulk_pid={b.pid or 'none'}")
            print(f"wiki_kb_chunks={_fmt_int(b.chunks_in_kb)}")
            print(f"wiki_kb_articles={_fmt_int(b.articles_in_kb)}")
            print(f"last_job_status={b.last_job_status or 'none'}")
            print(f"last_job_processed={_fmt_int(b.last_job_processed)}")
        return

    if args.migration:
        if args.json:
            data = to_dict(await get_ingestion_overview())
            print(json.dumps(data["migration"], indent=2))
        else:
            m = await get_migration_status()
            print(f"running={_yes_no(m.running)}")
            print(f"pid={m.pid or 'none'}")
            print(f"rows_remaining={_fmt_int(m.rows_remaining)}")
            print(f"anomalous_rows={_fmt_int(m.anomalous_rows)}")
            print(f"ready_for_ingestion={_yes_no(m.ready_for_ingestion)}")
            if m.last_log_tail:
                print(f"last_log_tail={m.last_log_tail[:200]}")
        return

    # Default: full overview
    if args.watch:
        try:
            while True:
                overview = await get_ingestion_overview()
                # clear screen
                print("\033[H\033[J", end="")
                render_overview(overview)
                time.sleep(10)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    overview = await get_ingestion_overview()
    if args.json:
        print(json.dumps(to_dict(overview), indent=2))
    else:
        render_overview(overview)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
