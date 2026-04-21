#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INTERVAL="${1:-30}"

docker_cmd() {
    if docker ps >/dev/null 2>&1; then
        docker "$@"
    else
        sg docker -c "docker $*"
    fi
}

while true; do
    echo "== $(date -Is) =="
    echo ""

    echo "-- Stack status --"
    ./pi-ctl.sh status || true
    echo ""

    echo "-- Agent stats --"
    curl -sS http://localhost:8000/stats || true
    echo ""
    echo ""

    echo "-- Direct retrieval smoke test --"
    curl -sS -X POST http://localhost:8000/chat/sync \
      -H 'Content-Type: application/json' \
      --data '{"message":"Summarize the latest news in 2 short bullet points using your retrieval tools."}' || true
    echo ""
    echo ""

    echo "-- Pipeline retrieval smoke test --"
    curl -sS -X POST http://localhost:9099/v1/chat/completions \
      -H 'Authorization: Bearer owui-pipeline-key' \
      -H 'Content-Type: application/json' \
      --data '{"model":"research_agent","messages":[{"role":"user","content":"Summarize the latest news in 2 short bullet points using your retrieval tools."}],"stream":false}' || true
    echo ""
    echo ""

    echo "-- Scheduler logs (tail 80) --"
    docker_cmd logs --tail 80 pi_agent_scheduler 2>&1 || true
    echo ""
    echo "Sleeping ${INTERVAL}s..."
    echo ""

    sleep "$INTERVAL"
done
