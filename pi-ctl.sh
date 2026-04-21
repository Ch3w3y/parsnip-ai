#!/usr/bin/env bash
# pi-ctl — control script for pi-agent stack and ingestion
#
# Usage:
#   ./pi-ctl.sh stack   start|stop|restart|status
#   ./pi-ctl.sh ingest  start|stop|status          (scheduler container)
#   ./pi-ctl.sh wiki    start|stop|status           (Wikipedia background ingest)
#   ./pi-ctl.sh status                              (overview of everything)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIKI_PID_FILE="/tmp/pi-agent-wiki.pid"
WIKI_LOG="/tmp/wiki_ingest.log"

# Colours
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
header() { echo -e "\n${BOLD}$*${NC}"; }

env_load() {
    if [[ -f "$SCRIPT_DIR/.env" ]]; then
        # shellcheck disable=SC1091
        set -a && source "$SCRIPT_DIR/.env" && set +a
    fi
}

extract_host() {
    printf '%s' "$1" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##; s#:[0-9]+$##'
}

is_local_host() {
    case "${1:-}" in
        ""|localhost|127.0.0.1|0.0.0.0|host.docker.internal) return 0 ;;
        *) return 1 ;;
    esac
}

# ── Stack (docker compose services, excluding scheduler) ─────────────────────

STACK_SERVICES="postgres joplin-server joplin-mcp searxng agent pipelines openwebui analysis"

stack_start() {
    info "Starting stack services..."
    cd "$SCRIPT_DIR"
    docker compose up -d $STACK_SERVICES
    ok "Stack started"
}

stack_stop() {
    info "Stopping stack services..."
    cd "$SCRIPT_DIR"
    docker compose stop $STACK_SERVICES
    ok "Stack stopped"
}

stack_restart() {
    stack_stop
    stack_start
}

stack_status() {
    cd "$SCRIPT_DIR"
    docker compose ps $STACK_SERVICES --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null
}

# ── Ingest (scheduler container — handles arXiv, bioRxiv, news, Joplin) ──────

ingest_start() {
    info "Starting scheduler (arXiv / bioRxiv / news / Joplin watcher)..."
    cd "$SCRIPT_DIR"
    docker compose up -d scheduler
    ok "Scheduler started"
}

ingest_stop() {
    info "Stopping scheduler..."
    cd "$SCRIPT_DIR"
    docker compose stop scheduler
    ok "Scheduler stopped"
}

ingest_status() {
    cd "$SCRIPT_DIR"
    docker compose ps scheduler --format "table {{.Name}}\t{{.Status}}" 2>/dev/null
}

# ── Wiki (long-running host process — Wikipedia bulk ingest) ──────────────────

wiki_running() {
    if [[ -f "$WIKI_PID_FILE" ]]; then
        local pid
        pid=$(cat "$WIKI_PID_FILE")
        kill -0 "$pid" 2>/dev/null
    else
        return 1
    fi
}

wiki_start() {
    if wiki_running; then
        warn "Wikipedia ingest already running (PID $(cat "$WIKI_PID_FILE"))"
        return
    fi

    # Warn if scheduler is also running — competing embed calls risk VRAM OOM
    if docker compose ps scheduler --format "{{.Status}}" 2>/dev/null | grep -q "Up"; then
        warn "Scheduler is running — stopping it to avoid VRAM conflicts..."
        ingest_stop
    fi

    info "Starting Wikipedia ingest (BATCH_SIZE=64, auto-resume from checkpoint)..."
    cd "$SCRIPT_DIR/ingestion"
    # shellcheck disable=SC1091
    set -a && source "$SCRIPT_DIR/.env" && set +a
    nohup uv run python ingest_wikipedia.py > "$WIKI_LOG" 2>&1 &
    echo $! > "$WIKI_PID_FILE"
    ok "Wikipedia ingest started (PID $!) — log: $WIKI_LOG"
    info "Monitor: tail -f $WIKI_LOG"
    info "VRAM note: ~6GB occupied while running. Stop before gaming: ./pi-ctl.sh wiki stop"
}

wiki_stop() {
    if ! wiki_running; then
        warn "Wikipedia ingest is not running"
        return
    fi
    local pid
    pid=$(cat "$WIKI_PID_FILE")
    info "Stopping Wikipedia ingest (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    # Wait up to 10s for clean exit
    for i in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then break; fi
        sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$WIKI_PID_FILE"
    ok "Wikipedia ingest stopped. Resume any time — progress is checkpointed every 500 articles."
}

wiki_status() {
    if wiki_running; then
        local pid
        pid=$(cat "$WIKI_PID_FILE")
        ok "Wikipedia ingest RUNNING (PID $pid)"
        # Show last progress line from log
        if [[ -f "$WIKI_LOG" ]]; then
            local last_progress
            last_progress=$(grep -E "Progress:|articles\]" "$WIKI_LOG" 2>/dev/null | tail -1 || true)
            [[ -n "$last_progress" ]] && info "Last progress: $last_progress"
        fi
        # Show VRAM
        local vram_used vram_total
        vram_used=$(rocm-smi --showmeminfo vram 2>/dev/null | grep "Total Used" | awk '{print $NF}' || true)
        vram_total=$(rocm-smi --showmeminfo vram 2>/dev/null | grep "Total Memory" | awk '{print $NF}' || true)
        if [[ -n "$vram_used" ]]; then
            local used_gb total_gb
            used_gb=$(python3 -c "print(f'{$vram_used/1073741824:.1f}')")
            total_gb=$(python3 -c "print(f'{$vram_total/1073741824:.1f}')")
            info "VRAM: ${used_gb}GB / ${total_gb}GB used"
        fi
        # Show DB chunk count
        local wiki_chunks
        wiki_chunks=$(docker exec pi_agent_postgres psql -U agent -d agent_kb -tAc \
            "SELECT COUNT(*) FROM knowledge_chunks WHERE source='wikipedia'" 2>/dev/null || echo "?")
        wiki_chunks="${wiki_chunks// /}"  # trim whitespace
        info "Chunks in DB: $wiki_chunks"
    else
        fail "Wikipedia ingest NOT running"
        rm -f "$WIKI_PID_FILE"
    fi
}

# ── Full status overview ──────────────────────────────────────────────────────

show_status() {
    env_load
    header "── Stack ────────────────────────────────────────"
    stack_status

    header "── Scheduler (ingest) ───────────────────────────"
    ingest_status

    header "── Wikipedia ingest ─────────────────────────────"
    wiki_status

    header "── Knowledge Base ───────────────────────────────"
    docker exec pi_agent_postgres psql -U agent -d agent_kb -c \
        "SELECT source, COUNT(*) as chunks FROM knowledge_chunks GROUP BY source ORDER BY chunks DESC;" \
        2>/dev/null || warn "Could not reach database"

    header "── VRAM ─────────────────────────────────────────"
    local ollama_host ssh_host
    ollama_host=$(extract_host "${OLLAMA_BASE_URL:-http://localhost:11434}")
    ssh_host="${OLLAMA_SSH_HOST:-}"
    if is_local_host "$ollama_host"; then
        rocm-smi --showmeminfo vram 2>/dev/null | grep -E "Total (Memory|Used)" | \
            awk '{printf "  %s\n", $0}' || warn "rocm-smi not available"
    else
        info "Remote Ollama configured at ${OLLAMA_BASE_URL:-}"
        if [[ -n "$ssh_host" ]] && command -v ssh >/dev/null 2>&1; then
            ssh -o BatchMode=yes -o ConnectTimeout=5 "$ssh_host" \
                "nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader" 2>/dev/null | \
                awk '{printf "  %s\n", $0}' || warn "Remote CUDA check failed via SSH"
        else
            info "Local VRAM status skipped (GPU is remote)"
            [[ -z "$ssh_host" ]] && warn "Set OLLAMA_SSH_HOST in .env to enable remote CUDA status via SSH"
        fi
    fi

    echo ""
}

# ── Entrypoint ────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: $0 <command> [action]"
    echo ""
    echo "  $0 stack   start|stop|restart|status"
    echo "  $0 ingest  start|stop|status     (scheduler: arXiv/bioRxiv/news/Joplin)"
    echo "  $0 wiki    start|stop|status     (Wikipedia bulk ingest)"
    echo "  $0 status                        (full overview)"
}

case "${1:-}" in
    stack)
        case "${2:-}" in
            start)   stack_start ;;
            stop)    stack_stop ;;
            restart) stack_restart ;;
            status)  stack_status ;;
            *)       usage; exit 1 ;;
        esac ;;
    ingest)
        case "${2:-}" in
            start)  ingest_start ;;
            stop)   ingest_stop ;;
            status) ingest_status ;;
            *)      usage; exit 1 ;;
        esac ;;
    wiki)
        case "${2:-}" in
            start)  wiki_start ;;
            stop)   wiki_stop ;;
            status) wiki_status ;;
            *)      usage; exit 1 ;;
        esac ;;
    status) show_status ;;
    *)      usage; exit 1 ;;
esac
