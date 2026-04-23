# Port Reference Audit Findings

## Pattern Recognition
- `:3000` = OpenWebUI (legacy, backward compatibility)
- `:3001` = assistant-ui (primary Next.js frontend)
- `:8000` = Agent API (LangGraph orchestration)
- `:9099` = Pipelines adapter (legacy OpenWebUI compatibility)

## Issue Found
**docs/FRONTEND.md:250** incorrectly stated Next.js dev server runs on port `:3000` when it should be `:3001`.

This is a regression - the code comment contradicts the architecture where assistant-ui (Next.js) runs on `:3001` and OpenWebUI runs on `:3000`.

## Verification Results
- All OpenWebUI references to `:3000`: ✓ LEGIT (explicit legacy mention)
- All assistant-ui references to `:3001`: ✓ CORRECT
- Zero false positives where Next.js/assistant-ui uses `:3000`

## Action Taken
Fixed line 250 in docs/FRONTEND.md to correctly reference `http://localhost:3001` for Next.js dev server.

## Evidence
Saved to `.sisyphus/evidence/task-19-ports.txt`
# Link Verification Findings

## Date: Thu Apr 23 2026
## Task: Verify all [text](path) internal links

## Process
1. Extracted all markdown links from main files (README.md, docs/*.md, agent/README.md)
2. Filtered for relative paths only (excluded http:// and https:// URLs)
3. Resolved each link path against file's directory and repository root
4. Verified each target file exists

## Results
- All 70+ internal links verified and valid
- Zero 404s found
- Relative paths (../ARCHITECTURE.md) resolve correctly
- Fragment links (e.g., #legacy-openwebui) point to valid section headers

## Evidence
Saved to: .sisyphus/evidence/task-17-links.txt

# Doc Bash Command Verification

## Date: Thu Apr 23 2026
## Task: Extract, run, and verify all ```bash blocks from key docs

## Process
1. Extracted 73 bash code blocks from 20 .md files
2. Ran non-destructive commands, skipped destructive/state-altering ones
3. Recorded exit codes and outputs

## Results
- docker compose config: PASS (exit 0)
- ./pi-ctl.sh status: FAIL (exit 1, expected — Docker socket inaccessible in this environment)
- pytest --collect-only: PASS (365 tests discovered)
- ruff check .: FAIL (154 lint issues; command runs fine, code has lint debt)
- curl localhost:8000/health: PASS (agent healthy)
- curl localhost:8000/stats: PASS (returns valid JSON)
- curl localhost:8000/ingestion/status: PASS (returns valid JSON)
- curl localhost:8000/v1/chat/completions: PASS (SSE streaming OK)
- curl localhost:8000/chat/sync: PASS (returns correct response for 2+2)
- curl localhost:3000/api/config: FAIL (connection refused — OpenWebUI legacy not running, expected)
- curl localhost:8095/health: FAIL (returns {"detail":"Not Found"})
- ss -tlnp | grep -E ':3001|:8000|:5432': PASS (8000 & 5432 listening)
- uname -m: PASS (x86_64)
- cat /tmp/parsnip_circuit_breaker.json: PASS (file not found — circuit closed)
- diff .env.example .env: PASS (shows correct diff of env vars)
- ls frontend/src/components/: PASS (expected files present)
- docker network ls | grep pi_agent: FAIL (Docker socket inaccessible)
- python scripts/ingestion_status.py: PASS (detailed output)
- ./pi-ctl.sh wiki status: PASS (shows running PID and chunk counts)
- ./pi-ctl.sh stack status / ingest status: FAIL (Docker socket inaccessible)
- docker compose ps: FAIL (Docker socket inaccessible)
- docker compose logs --tail 50 agent: FAIL (Docker socket inaccessible)
- curl localhost:8000/v1/models: FAIL (returns {"detail":"Not Found"})
- curl localhost:8000/threads/test-thread: PASS (returns thread history)
- curl localhost:8080: PASS (SearXNG index page HTML returned)
- curl localhost:22300: PASS (redirects to /login)

## Doc Discrepancies Found
1. DEPLOYMENT.md claims analysis server has /health endpoint; curl returns Not Found.
2. API_REFERENCE.md documents GET /v1/models; endpoint does not exist in current agent.

## Evidence
Saved to: .sisyphus/evidence/task-18-commands.txt
