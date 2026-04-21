#!/usr/bin/env python3
"""
Serial ingestion runner: GitHub → arXiv → bioRxiv → News → PubMed → RSS → SSRN → Wikipedia.

Runs each pipeline sequentially, logging progress to a file.
Designed to be left running for extended periods (days/week).

Usage:
    python run_serial_ingestion.py
    python run_serial_ingestion.py --skip github news    # skip GitHub and news
    python run_serial_ingestion.py --only wikipedia      # only run Wikipedia
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Configure logging to file + console
LOG_DIR = Path(__file__).parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = (
    LOG_DIR
    / f"serial_ingestion_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def run_command(cmd: list[str], description: str) -> bool:
    """Run a subprocess command and return success status."""
    logger.info(f"{'=' * 60}")
    logger.info(f"STARTING: {description}")
    logger.info(f"Command: {' '.join(cmd)}")
    logger.info(f"{'=' * 60}")

    import subprocess

    start = time.time()
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        elapsed = time.time() - start
        logger.info(f"COMPLETED: {description} in {elapsed / 60:.1f} minutes")
        return True
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start
        logger.error(
            f"FAILED: {description} after {elapsed / 60:.1f} minutes (exit code {e.returncode})"
        )
        return False


async def main():
    parser = argparse.ArgumentParser(description="Serial ingestion runner")
    parser.add_argument(
        "--skip",
        nargs="+",
        default=[],
        choices=["github", "arxiv", "biorxiv", "news", "pubmed", "rss", "ssrn", "wikipedia"],
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        choices=["github", "arxiv", "biorxiv", "news", "pubmed", "rss", "ssrn", "wikipedia"],
    )
    args = parser.parse_args()

    # Determine which pipelines to run
    all_pipelines = ["github", "arxiv", "biorxiv", "news", "pubmed", "rss", "ssrn", "wikipedia"]
    if args.only:
        pipelines = [p for p in all_pipelines if p in args.only]
    else:
        pipelines = [p for p in all_pipelines if p not in args.skip]

    logger.info(
        f"Serial ingestion starting at {datetime.now(timezone.utc).isoformat()}"
    )
    logger.info(f"Pipelines to run: {', '.join(pipelines)}")
    logger.info(f"Log file: {LOG_FILE}")

    start_time = time.time()
    results = {}

    for pipeline in pipelines:
        if pipeline == "github":
            success = run_command(
                [
                    "uv",
                    "run",
                    "python",
                    "ingest_github.py",
                    "--top-repos",
                    "100",
                    "--max-files",
                    "200",
                ],
                "GitHub ingestion (top 100 most-starred repos)",
            )
            results["github"] = success

        elif pipeline == "arxiv":
            success = run_command(
                ["uv", "run", "python", "ingest_arxiv.py"],
                "arXiv ingestion (default categories)",
            )
            results["arxiv"] = success

        elif pipeline == "biorxiv":
            success = run_command(
                ["uv", "run", "python", "ingest_biorxiv.py"],
                "bioRxiv/medRxiv ingestion",
            )
            results["biorxiv"] = success

        elif pipeline == "news":
            success = run_command(
                ["uv", "run", "python", "ingest_news_api.py"],
                "NewsAPI ingestion",
            )
            results["news"] = success

        elif pipeline == "pubmed":
            success = run_command(
                ["uv", "run", "python", "ingest_pubmed.py"],
                "PubMed ingestion (default MeSH terms)",
            )
            results["pubmed"] = success

        elif pipeline == "rss":
            success = run_command(
                ["uv", "run", "python", "ingest_rss.py"],
                "RSS feed ingestion (default feeds)",
            )
            results["rss"] = success

        elif pipeline == "ssrn":
            success = run_command(
                ["uv", "run", "python", "ingest_ssrn.py"],
                "SSRN/OpenAlex ingestion (social science preprints)",
            )
            results["ssrn"] = success

        elif pipeline == "wikipedia":
            wiki_dir = Path(__file__).parent / "data" / "wiki_extracted"
            if not wiki_dir.exists():
                logger.error(f"Wikipedia extracted directory not found: {wiki_dir}")
                logger.error("Run: bash ../scripts/download_wikipedia.sh first")
                results["wikipedia"] = False
                continue

            success = run_command(
                [
                    "uv",
                    "run",
                    "python",
                    "ingest_wikipedia.py",
                    "--wiki-dir",
                    str(wiki_dir),
                ],
                "Wikipedia ingestion (full dump)",
            )
            results["wikipedia"] = success

    # Summary
    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("SERIAL INGESTION COMPLETE")
    logger.info(f"{'=' * 60}")
    logger.info(f"Total time: {elapsed / 3600:.2f} hours ({elapsed / 60:.1f} minutes)")
    for pipeline, success in results.items():
        status = "OK" if success else "FAILED"
        logger.info(f"  {pipeline}: {status}")

    if all(results.values()):
        logger.info("\nAll pipelines completed successfully!")
    else:
        failed = [p for p, s in results.items() if not s]
        logger.warning(f"\nFailed pipelines: {', '.join(failed)}")


if __name__ == "__main__":
    asyncio.run(main())
