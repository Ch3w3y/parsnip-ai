#!/bin/bash
# Master ingestion runner — called by systemd timers.
# Usage: ingest_all.sh [news|arxiv|wikipedia_updates|all]

set -euo pipefail

INGEST_DIR="$(cd "$(dirname "$0")/../ingestion" && pwd)"
LOG_DIR="/var/log/pi-agent"
mkdir -p "$LOG_DIR"

run() {
    local name="$1"; shift
    local log="$LOG_DIR/${name}_$(date +%Y%m%d_%H%M%S).log"
    echo "[$(date)] Starting $name ingestion…"
    cd "$INGEST_DIR"
    uv run "$@" 2>&1 | tee "$log"
    echo "[$(date)] $name done. Log: $log"
}

TARGET="${1:-all}"

case "$TARGET" in
    news)
        run news ingest_news.py --days 1
        ;;
    arxiv)
        run arxiv ingest_arxiv.py --categories cs.AI cs.LG cs.CL cs.CV stat.ML q-bio.GN q-bio.NC econ.GN --max-per-cat 500
        ;;
    wikipedia_updates)
        run wikipedia_updates ingest_wikipedia_updates.py --days 7
        ;;
    all)
        run news ingest_news.py --days 1
        run arxiv ingest_arxiv.py --categories cs.AI cs.LG cs.CL cs.CV stat.ML q-bio.GN q-bio.NC econ.GN --max-per-cat 500
        run wikipedia_updates ingest_wikipedia_updates.py --days 7
        ;;
    *)
        echo "Usage: $0 [news|arxiv|wikipedia_updates|all]"
        exit 1
        ;;
esac
