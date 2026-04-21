#!/usr/bin/env bash
# First-run setup script for pi-agent.
# Run this once after cloning the repo.
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*"; exit 1; }

cd "$(dirname "$0")/.."

# ── 1. Copy .env ──────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    info "Created .env from .env.example"
    echo ""
    echo -e "${BOLD}ACTION REQUIRED: Edit .env and fill in:${NC}"
    echo "  POSTGRES_PASSWORD   — pick a strong password"
    echo "  LLM_PROVIDER        — openrouter or openai_compat"
    echo "  OPENROUTER_API_KEY  — required when LLM_PROVIDER=openrouter"
    echo "  OPENAI_COMPAT_*     — required when LLM_PROVIDER=openai_compat"
    echo "  WEBUI_SECRET_KEY    — run: openssl rand -hex 32"
    echo "  TAVILY_API_KEY      — optional, from https://app.tavily.com (recommended)"
    echo ""
    read -p "Press Enter after editing .env to continue... " _
fi

# ── 2. Validate .env ──────────────────────────────────────────────────────────
source .env
LLM_PROVIDER="${LLM_PROVIDER:-openrouter}"
if [ "${LLM_PROVIDER}" = "openrouter" ]; then
    [ -z "${OPENROUTER_API_KEY:-}" ] && error "OPENROUTER_API_KEY not set in .env"
elif [ "${LLM_PROVIDER}" = "openai_compat" ]; then
    [ -z "${OPENAI_COMPAT_BASE_URL:-}" ] && error "OPENAI_COMPAT_BASE_URL not set in .env"
    [ -z "${OPENAI_COMPAT_API_KEY:-}" ] && error "OPENAI_COMPAT_API_KEY not set in .env"
else
    error "LLM_PROVIDER must be openrouter or openai_compat"
fi
[ -z "${POSTGRES_PASSWORD:-}" ]  && error "POSTGRES_PASSWORD not set in .env"
[ -z "${WEBUI_SECRET_KEY:-}" ]   && error "WEBUI_SECRET_KEY not set in .env"

# ── 3. Check Ollama ───────────────────────────────────────────────────────────
info "Checking Ollama…"
OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null; then
    info "Ollama is running at ${OLLAMA_URL}"
else
    warn "Ollama not detected at ${OLLAMA_URL}"
    warn "Install from https://ollama.com and start with: ollama serve"
    warn "Continuing anyway — embedding will fail until Ollama is up."
fi

# ── 4. Pull embedding model ───────────────────────────────────────────────────
EMBED_MODEL="${EMBED_MODEL:-mxbai-embed-large}"
info "Pulling embedding model: ${EMBED_MODEL}"
ollama pull "${EMBED_MODEL}" || warn "Could not pull ${EMBED_MODEL} — do it manually: ollama pull ${EMBED_MODEL}"

# ── 5. GPU / Ollama check ────────────────────────────────────────────────────
info "Running GPU / Ollama check…"
bash "$(dirname "$0")/rocm_check.sh" || warn "ROCm check failed — see above for workarounds."

# ── 6. Start Docker stack ────────────────────────────────────────────────────
info "Starting Docker Compose stack…"
docker compose pull
docker compose up -d --build

# ── 7. Wait for postgres ──────────────────────────────────────────────────────
info "Waiting for PostgreSQL to be ready…"
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U agent -d agent_kb > /dev/null 2>&1; then
        info "PostgreSQL ready."
        break
    fi
    sleep 2
    [ $i -eq 30 ] && error "PostgreSQL did not become ready in time."
done

# ── 8. Bootstrap Joplin admin credentials ────────────────────────────────────
info "Bootstrapping Joplin admin credentials…"
bash "$(dirname "$0")/bootstrap_joplin_admin.sh" || warn "Could not bootstrap Joplin admin credentials automatically."

# ── 9. Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}✓ pi-agent is running!${NC}"
echo ""
echo "  OpenWebUI:  http://localhost:3000"
echo "  Agent API:  http://localhost:8000"
echo "  Agent docs: http://localhost:8000/docs"
echo ""
echo -e "${BOLD}Next: ingest Wikipedia${NC}"
echo "  bash scripts/download_wikipedia.sh   # ~25GB download"
echo "  cd ingestion && python ingest_wikipedia.py --wiki-dir ./data/wiki_extracted"
echo ""
echo -e "${BOLD}Or test the agent now (no Wikipedia yet):${NC}"
echo "  curl -N -X POST http://localhost:8000/chat/sync \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"message\": \"What is quantum entanglement?\"}'"
