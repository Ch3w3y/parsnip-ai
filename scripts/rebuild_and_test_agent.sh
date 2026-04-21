#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Rebuilding agent =="
docker compose up -d --build agent

echo ""
echo "== Waiting for health endpoint =="
for _ in $(seq 1 60); do
    if curl -fsS http://localhost:8000/health >/dev/null; then
        break
    fi
    sleep 2
done

echo ""
echo "== Health =="
curl -sS http://localhost:8000/health
echo ""
echo ""

echo "== Direct agent retrieval test =="
curl -sS -X POST http://localhost:8000/chat/sync \
  -H 'Content-Type: application/json' \
  --data '{"message":"Summarize the latest news in 3 bullet points. Use search_with_filters with source=news and a recent time filter if needed."}'
echo ""
echo ""

echo "== Pipeline retrieval test =="
curl -sS -X POST http://localhost:9099/v1/chat/completions \
  -H 'Authorization: Bearer owui-pipeline-key' \
  -H 'Content-Type: application/json' \
  --data '{"model":"research_agent","messages":[{"role":"user","content":"Summarize the latest news in 2 short bullet points using your retrieval tools."}],"stream":false}'
echo ""
echo ""

echo "== KB stats =="
curl -sS http://localhost:8000/stats
echo ""
