#!/usr/bin/env bash
# Download and extract the English Wikipedia dump for ingestion.
#
# Requires: aria2c (fast parallel downloader), python3 + wikiextractor
# Disk space needed: ~25GB download + ~105GB extracted = ~130GB total
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[wiki]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

DUMP_DIR="$(dirname "$0")/../ingestion/data"
DUMP_FILE="enwiki-latest-pages-articles-multistream.xml.bz2"
DUMP_URL="https://dumps.wikimedia.org/enwiki/latest/${DUMP_FILE}"
EXTRACTED_DIR="${DUMP_DIR}/wiki_extracted"

mkdir -p "${DUMP_DIR}"
cd "${DUMP_DIR}"

# ── 1. Download ───────────────────────────────────────────────────────────────
if [ -f "${DUMP_FILE}" ] && [ ! -f "${DUMP_FILE}.aria2" ]; then
    info "Dump already downloaded: ${DUMP_FILE}"
else
    [ -f "${DUMP_FILE}.aria2" ] && info "Resuming incomplete download…"
    info "Downloading Wikipedia dump (~25GB)…"
    info "URL: ${DUMP_URL}"
    echo ""

    # Wikimedia rate-limits aggressive parallel downloads (429).
    # 3 connections is the safe maximum; single-stream wget also works.
    UA="Mozilla/5.0 (compatible; parsnip-kb-ingestion/1.0; +https://github.com/your-repo)"
    if command -v aria2c > /dev/null 2>&1; then
        aria2c \
            --continue=true \
            --max-connection-per-server=3 \
            --split=3 \
            --min-split-size=500M \
            --file-allocation=falloc \
            --user-agent="${UA}" \
            "${DUMP_URL}"
    else
        warn "aria2c not found — falling back to wget (slower, no parallel)"
        warn "Install for faster downloads: sudo pacman -S aria2"
        wget -c --user-agent="${UA}" "${DUMP_URL}"
    fi
    info "Download complete."
fi

# ── 2. Extract text ───────────────────────────────────────────────────────────
if [ -d "${EXTRACTED_DIR}" ] && [ "$(ls -A "${EXTRACTED_DIR}" 2>/dev/null)" ]; then
    info "Extraction directory already exists: ${EXTRACTED_DIR}"
    info "Delete it to re-extract: rm -rf ${EXTRACTED_DIR}"
else
    info "Extracting Wikipedia text with wikiextractor…"
    info "This takes ~30–60 minutes. Output: ${EXTRACTED_DIR}"
    echo ""

    # wikiextractor is broken on Python 3.11+ (inline (?i) regex flag); use Docker with 3.10
    # Runs as root inside container; chmod at end so host user can manage the files without sudo
    DUMP_ABS="$(cd "${DUMP_DIR}" && pwd)"
    NPROC="$(nproc)"
    DOCKER_INNER="docker run --rm \
        --network host \
        -v ${DUMP_ABS}:/data \
        python:3.10-slim \
        bash -c 'pip install --no-cache-dir wikiextractor -q \
            && python -m wikiextractor.WikiExtractor \
                --json \
                --output /data/wiki_extracted \
                --bytes 10M \
                --processes ${NPROC} \
                /data/${DUMP_FILE} \
            && chmod -R a+rwX /data/wiki_extracted'"
    if docker info >/dev/null 2>&1; then
        eval "${DOCKER_INNER}"
    else
        sg docker -c "${DOCKER_INNER}"
    fi

    info "Extraction complete: ${EXTRACTED_DIR}"
fi

# ── 3. Stats ──────────────────────────────────────────────────────────────────
FILE_COUNT=$(find "${EXTRACTED_DIR}" -name "wiki_*" 2>/dev/null | wc -l)
info "Extracted files: ${FILE_COUNT}"
info "Disk usage: $(du -sh "${DUMP_DIR}" 2>/dev/null | cut -f1)"
echo ""
info "Ready to ingest! Run:"
echo "  cd ingestion"
echo "  export DATABASE_URL=postgresql://agent:PASSWORD@localhost:5432/agent_kb"
echo "  export OLLAMA_BASE_URL=http://localhost:11434"
echo "  python ingest_wikipedia.py --wiki-dir ./data/wiki_extracted"
