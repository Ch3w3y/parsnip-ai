#!/usr/bin/env python3
"""CLI for manual pipeline health checks.

Connects to DB, runs alerting threshold checks, and prints results.

Exit codes: 0 = healthy, 1 = CRITICAL alert(s), 2 = WARN alert(s) only

Usage:
    python scripts/check_pipeline_health.py
    python scripts/check_pipeline_health.py --json
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.alerting import run_all_checks, AlertLevel
from ingestion.utils import get_db_connection


def _colour(code: str, text: str) -> str:
    codes = {
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    if not sys.stdout.isatty():
        return text
    return f"{codes.get(code, '')}{text}{codes['reset']}"


def _level_icon(level: AlertLevel) -> str:
    if level == AlertLevel.CRITICAL:
        return _colour("red", "✗ CRIT")
    if level == AlertLevel.WARN:
        return _colour("yellow", "⚠ WARN")
    return _colour("green", "● INFO")


def render_table(alerts: list) -> None:
    if not alerts:
        print(_colour("green", "Pipeline health: OK — no alerts"))
        return

    print(_colour("bold", "Pipeline Health Alerts"))
    print("─" * 72)
    print(f"{'Level':<10} {'Source':<18} {'Metric':<20} {'Threshold':<12} {'Actual':<12}")
    print("─" * 72)
    for a in alerts:
        level_str = _level_icon(a.level)
        print(f"{level_str:<20} {a.source:<18} {a.metric:<20} {str(a.threshold):<12} {str(a.actual):<12}")
        print(f"           {a.message}")
    print("─" * 72)


def render_json(alerts: list) -> None:
    data = [
        {
            "level": a.level.value,
            "source": a.source,
            "metric": a.metric,
            "threshold": str(a.threshold),
            "actual": str(a.actual),
            "message": a.message,
        }
        for a in alerts
    ]
    print(json.dumps(data, indent=2))


async def main():
    parser = argparse.ArgumentParser(description="Check pipeline health via alerting thresholds")
    parser.add_argument("--json", action="store_true", help="Output as JSON for automation")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
                    break

    conn = await get_db_connection()
    alerts = await run_all_checks(conn)
    await conn.close()

    if args.json:
        render_json(alerts)
    else:
        render_table(alerts)

    has_critical = any(a.level == AlertLevel.CRITICAL for a in alerts)
    has_warn = any(a.level == AlertLevel.WARN for a in alerts)

    if has_critical:
        sys.exit(1)
    if has_warn:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())