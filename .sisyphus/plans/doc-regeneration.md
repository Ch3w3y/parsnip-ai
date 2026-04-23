# Documentation Deep Regeneration Plan

## TL;DR

> **Objective**: Transform parsnip-ai's documentation from "surface-level accurate" to "deeply grounded and comprehensive". We will fix factual errors, expand skeletal docs, regenerate stale tool tables, and create missing guides — all verified against the live codebase.

> **Deliverables**: 12 regenerated docs + 4 new docs + 1 cross-link audit.
> **Estimated Effort**: Large (20+ tasks, 4 waves).
> **Parallel Execution**: YES — Wave 1 (fixes), Wave 2 (deep rewrites), Wave 3 (new docs), Wave 4 (verification).
> **Critical Path**: Wave 1 fixes → Wave 2 rewrites (depends on Wave 1 for port fix) → Wave 3 new docs (independent) → Wave 4 verification (depends on all).

---

## Context

### Original Request
The user requested a "proper regeneration" of all GitHub-facing documentation, noting that the previous pass was surface-level. The existing docs must serve as "inspiration/grounding," but the goal is to fundamentally bring them into deep alignment with the current architecture and fill all critical gaps.

### Research Findings (Synthesized from 3 parallel deep-dive agents)
**Good news**: `README.md`, `ARCHITECTURE.md`, `CONFIGURATION.md`, `DEPLOYMENT.md`, `ROUTING.md`, `STORAGE_AND_BACKUP.md`, and `HYBRID_RAG_SHOWCASE.md` are factually current.
**Bad news**: Several contain specific stale data (ports, source counts, tool tables). More critically, `EXTENDING.md` (37 lines), `CONTRIBUTING.md` (21 lines), and `SECURITY.md` (17 lines) are skeletal. `agent/README.md` references only 6 of ~40 tools.
**Missing entirely**: Troubleshooting guide, testing guide, frontend architecture doc, API reference, CI/CD docs, database schema docs.

### Metis Review
[Metis analysis incorporated: identified 3 critical factual errors, 4 missing cross-links, and the need for command verification.]

---

## Work Objectives

### Core Objective
Produce a documentation suite that accurately reflects every architectural component, provides clear operational guidance, enables safe extension, and is fully verified against the running system.

### Concrete Deliverables
- **12 Refreshed Docs**: Root README, ARCHITECTURE, agent/README, ingestion/README, docs/ARCHITECTURE_VISUALS, docs/CONFIGURATION, docs/DEPLOYMENT, docs/EXTENDING, docs/README (index), docs/ROUTING, CONTRIBUTING, SECURITY.
- **4 New Docs**: TROUBLESHOOTING, TESTING, FRONTEND, API_REFERENCE.
- **1 Verification Report**: Cross-link audit, command verification, port audit.

### Definition of Done
- [ ] Every shell command in every doc runs successfully in Docker.
- [ ] Every URL/port reference matches `docker-compose.yml`.
- [ ] Every tool/service mentioned exists in the codebase.
- [ ] No doc references stale architectural components (e.g., OpenWebUI as primary).
- [ ] Cross-links connect all related docs.

### Must Have
- Fix the `:3000` -> `:3001` port error in all diagrams and text.
- `agent/README.md` must list all ~40 tools with brief descriptions and links to source files.
- `ingestion/README.md` must list all 13 sources from `sources.yaml`.
- `EXTENDING.md` must be a true extension guide (not just a paragraph).
- `CONTRIBUTING.md` must cover testing, PR expectations, and code style.

### Must NOT Have (Guardrails)
- Do NOT duplicate large sections between docs; use cross-references.
- Do NOT add boilerplate that isn't specific to this codebase (e.g., generic "How to use Docker" tutorials).
- Do NOT create docs for internal `.handover.md` or transient planning files.

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (Docker Compose stack, `pytest` suite, `docker compose config`).
- **Automated tests**: Tests-after (each doc will have a verification task after writing).
- **Framework**: N/A for docs; verification uses `bash` (command execution + curl).
- **Agent-Executed QA**: EVERY task includes a verification scenario.

### QA Policy
Every task MUST include agent-executed QA scenarios verifying the actual content.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Docs**: Use `Bash` (shell commands) and `Read` (file content) to verify commands execute and references are accurate.
- **Links**: Use `Bash` (curl) to verify URLs and ports.
- **Code examples**: Use `Bash` (syntax check, execution) to verify scripts run.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation Fixes — Start Immediately):
├── Task 1: Fix port :3000→:3001 in ARCHITECTURE.md + ARCHITECTURE_VISUALS.md
├── Task 2: Update ingestion/README.md source table (10→13 sources)
├── Task 3: Update docs/README.md index (add ROUTING.md)
├── Task 4: Verify docs/ROUTING.md intent layers against router.py
└── Task 5: Update docs/CONFIGURATION.md (deprecation notes, missing vars)

Wave 2 (Deep Rewrites — After Wave 1):
├── Task 6: Regenerate root README.md (fix port, add sections, verify commands)
├── Task 7: Rewrite agent/README.md (expand tool tables: 6→40 tools)
├── Task 8: Rewrite docs/EXTENDING.md ( SourceRegistry, routing, tool patterns)
├── Task 9: Update docs/DEPLOYMENT.md (scheduler ops, frontend env vars)
└── Task 10: Update docs/HYBRID_RAG_SHOWCASE.md (cross-refs to ROUTING.md)

Wave 3 (New Docs — After Wave 1, parallel with Wave 2):
├── Task 11: Create docs/TROUBLESHOOTING.md
├── Task 12: Create docs/TESTING.md
├── Task 13: Create docs/FRONTEND.md
├── Task 14: Create docs/API_REFERENCE.md
├── Task 15: Rewrite CONTRIBUTING.md
└── Task 16: Rewrite SECURITY.md

Wave 4 (Integration Verification — After ALL):
├── Task 17: Cross-link audit (ensure every doc links to relevant others)
├── Task 18: Command verification (execute every shell command in every doc)
├── Task 19: Port verification (ensure all :3000 references are legacy only)
└── Task 20: Final cleanup + commit

Critical Path: Task 1→Task 6→Task 17→Task 18→Task 20
Parallel Speedup: ~65% faster than sequential
```

### Dependency Matrix
- **1-5**: Wave 1 (fixes) — Independent of each other.
- **6**: Depends on 1 (port fix in ARCHITECTURE.md).
- **7**: Independent of Wave 1, but should align with Task 6.
- **8**: Depends on 2 (ingestion source list).
- **9**: Depends on 5 (env var updates).
- **11-16**: Independent of Wave 2, but can parallelize with it.
- **17-19**: Depends on ALL (6-16 complete).

---

## TODOs

### Wave 1: Foundation Fixes (Parallel)

- [x] **1. Fix port :3000→:3001 in ARCHITECTURE.md and ARCHITECTURE_VISUALS.md**

  **What to do**: Search all docs for `assistant-ui :3000`, `Frontend :3000`, or `Next.js :3000` and update to `:3001`. Check mermaid diagrams, markdown tables, and text. Do not change references to OpenWebUI (`:3000`).
  **Must NOT do**: Do not change OpenWebUI port references.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Can run with Tasks 2–5.
  **QA Scenarios**:
  ```
  Scenario: Verify port fixes
    Tool: Bash (grep)
    Steps:
      1. grep -r 'assistant-ui.*:3000\|Next.js.*:3000\|Frontend.*:3000' README.md docs/ ARCHITECTURE.md
         → Expected: 0 matches
      2. grep -r 'assistant-ui.*:3001\|Next.js.*:3001\|Frontend.*:3001' README.md docs/ ARCHITECTURE.md
         → Expected: ≥3 matches
    Evidence: .sisyphus/evidence/task-1-port-fixes.txt
  ```

- [x] **2. Update ingestion/README.md source table (10→13 sources)**

  **What to do**: Read `ingestion/sources.yaml`. Add `hackernews`, `pubmed`, `rss`, and `ssrn` to the Pipelines table. Ensure descriptions, conflict strategies, and schedules match `sources.yaml`.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Can run with Tasks 1, 3–5.
  **QA Scenarios**:
  ```
  Scenario: Verify source count
    Tool: Read (ingestion/README.md)
    Steps:
      1. Count rows in the Pipelines table → Expected: 13 rows
      2. Verify hackernews, pubmed, rss, ssrn are listed
    Evidence: .sisyphus/evidence/task-2-source-count.md
  ```

- [x] **3. Update docs/README.md index (add ROUTING.md)**

  **What to do**: Add `[Routing Configuration](ROUTING.md)` to the Documentation Index under its logical place (e.g., between Configuration and Deployment).
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Can run with Tasks 1–2, 4–5.
  **QA Scenarios**:
  ```
  Scenario: Verify index completeness
    Tool: Read (docs/README.md)
    Steps:
      1. Confirm ROUTING.md is listed
      2. Confirm the list matches all .md files in docs/ (except docs/README.md itself)
    Evidence: .sisyphus/evidence/task-3-index.md
  ```

- [x] **4. Verify docs/ROUTING.md intent layers against router.py**

  **What to do**: Read `agent/tools/router.py` and compare `ROUTING_CONFIG["intent_layers"]` and `layer_budgets` against `docs/ROUTING.md`. Update ROUTING.md if any source names or budgets differ.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Can run with Tasks 1–3, 5.
  **QA Scenarios**:
  ```
  Scenario: Verify routing config alignment
    Tool: Read (agent/tools/router.py) + diff against docs/ROUTING.md
    Steps:
      1. Read ROUTING_CONFIG in router.py
      2. Confirm intent_layers and layer_budgets in ROUTING.md match exactly
    Evidence: .sisyphus/evidence/task-4-routing-match.md
  ```

- [x] **5. Update docs/CONFIGURATION.md (deprecation notes, missing env vars)**

  **What to do**: 
  - Mark `JOPLIN_MCP_URL` as deprecated (legacy, backward compatibility only).
  - Add `DATABASE_URL`, `JOPLIN_DATABASE_URL`, `PARSNIP_CIRCUIT_BREAKER_PATH`.
  - Add section explaining `GUARDRAIL_MODE` values (`strict`, `balanced`, `lenient`) with their runtime effects.
  - Cross-reference ROUTING.md for embedding config.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Can run with Tasks 1–4.
  **QA Scenarios**:
  ```
  Scenario: Verify env var completeness
    Tool: Bash (diff .env.example vs CONFIGURATION.md)
    Steps:
      1. List all vars in .env.example
      2. Confirm every var is either documented in CONFIGURATION.md or explicitly noted as deprecated
    Evidence: .sisyphus/evidence/task-5-env-vars.md
  ```

### Wave 2: Deep Rewrites (Mostly Parallel)

- [x] **6. Regenerate root README.md**

  **What to do**: Using the current README as inspiration/grounding, produce a proper regeneration:
  - Fix `Architecture` diagram port to `:3001`.
  - Add `joplin-mcp` to Core Services table (port `:8090`, deprecated).
  - Add ROUTING.md and INSTALL.md links to Documentation section.
  - Expand Quick Start with `pi-ctl.sh status` as verification.
  - Expand Operations with `pi-ctl.sh ingest start|stop|status`.
  - Mention pre-built image option (`IMAGE_TAG=0.1.0 docker compose up -d --no-build`).
  - Add section for tests (`pytest -q`) and scripts directory.
  - Add note on analysis container `linux/amd64` limitation.
  - Verify all curl commands and `./pi-ctl.sh` invocations actually exist in the codebase.
  **Must NOT do**: Do not remove existing sections wholesale; evolve them.
  **Recommended Agent Profile**: `writing` + `devops` skills
  **Parallelization**: Depends on Task 1 (port fix). Can run in parallel with Tasks 7–10.
  **QA Scenarios**:
  ```
  Scenario: Verify curl commands and paths
    Tool: Bash
    Steps:
      1. Run ./pi-ctl.sh status → verify script exists and is executable
      2. Verify all curl URLs in README match docker-compose.yml service ports
    Evidence: .sisyphus/evidence/task-6-readme-commands.txt
  ```

- [x] **7. Rewrite agent/README.md (expand tool tables: 6→40 tools)**

  **What to do**: 
  - Read `agent/tools/__init__.py` and all `agent/tools/*.py` files.
  - Categorize all ~40 tools into logical groups: Retrieval, Memory, Analysis, Joplin, Workspace, GitHub, System, PDF, Knowledge Graph, Filtered Search.
  - For each tool, provide: name, purpose (1 sentence), and source file link.
  - Add tables for `holistic_search`, `adaptive_search`, `kb_search`, `research`, `timeline`, `get_document`, etc.
  - Mention `workspace.py` tools, `github.py` tools, `joplin_hitl.py` HITL workflow.
  - Update "Adding a New Tool" section to match current graph wiring (graph_tools.py + graph_prompts.py).
  - Verify against `agent/graph.py` TOOLS list.
  **Must NOT do**: Do not copy full function docstrings; keep descriptions to one sentence.
  **Recommended Agent Profile**: `deep` (needs to read all tool files)
  **Parallelization**: Can run in parallel with Task 6, 8–10.
  **QA Scenarios**:
  ```
  Scenario: Verify tool count and categories
    Tool: Bash (find)
    Steps:
      1. find agent/tools -name '*.py' | grep -v __pycache__ | wc -l → Expected: ~15 files
      2. Count tool entries in agent/README.md → Expected: ~40 entries matching __init__.py exports
    Evidence: .sisyphus/evidence/task-7-tool-table.md
  ```

- [x] **8. Rewrite docs/EXTENDING.md**

  **What to do**: Expand from 37 lines to a full extension guide (inspired by old content but fundamentally rewritten):
  - **Ingestion Extension**: Fetch → save_raw → process → upsert pattern. Link to sources.yaml schema reference (which lives in ingestion/README.md). Explain `--from-raw` replay.
  - **SourceRegistry Pattern**: How `ingestion/registry.py` auto-discovers modules. How to add a new source with just `ingest_<source>.py` + `sources.yaml` entry.
  - **Routing System Integration**: Explain updating `ROUTING_CONFIG` in `router.py`, `SOURCE_MODEL_MAP`, `intent_layers`, `layer_budgets`. Link to ROUTING.md.
  - **Tool Extension**: How to add a tool to `agent/tools/`, register in `__init__.py`, wire into `graph_tools.py`, and update `graph_prompts.py`.
  - **Frontend Extension**: How custom tool UIs work in `frontend/` (brief, link to FRONTEND.md).
  - **Structured Data Extension**: How to add a new structured table (e.g., `forex_rates` pattern).
  - **Connection Pool Extension**: How to add a new named pool to `db_pool.py`.
  - **Concrete Example**: Walk through adding a hypothetical new source end-to-end.
  **Must NOT do**: Do not copy-paste from ingestion/README.md; use cross-references and expand the narrative.
  **Recommended Agent Profile**: `deep` (needs to understand full extension lifecycle)
  **Parallelization**: Depends on Task 2 (source list). Can run in parallel with Tasks 6–7, 9–10.
  **QA Scenarios**:
  ```
  Scenario: Verify EXTENDING.md completeness
    Tool: Read
    Steps:
      1. Confirm each heading maps to a real file/command in the repo
      2. Verify no section is <2 lines (a guard against AI slop placeholders)
    Evidence: .sisyphus/evidence/task-8-extending.md
  ```

- [x] **9. Update docs/DEPLOYMENT.md**

  **What to do**:
  - Add `NEXT_PUBLIC_AGENT_URL` and `AGENT_INTERNAL_URL` documentation.
  - Document `pi-ctl.sh ingest start|stop|status` for scheduler operations.
  - Fix "restore script" reference: either create `scripts/restore_kb.py` or remove the reference.
  - Add scheduler health check guidance.
  - Mention NEWS_API_KEY and other ingestion prerequisite keys.
  - Ensure Kubernetes or non-Docker deployment paths are NOT added (out of scope per guardrails).
  **Recommended Agent Profile**: `devops`
  **Parallelization**: Depends on Task 5 (env vars). Can run in parallel with Tasks 6–8, 10.
  **QA Scenarios**:
  ```
  Scenario: Verify scheduler commands
    Tool: Bash
    Steps:
      1. ./pi-ctl.sh ingest status → verify script accepts this argument
    Evidence: .sisyphus/evidence/task-9-scheduler-ops.txt
  ```

- [x] **10. Update docs/HYBRID_RAG_SHOWCASE.md**

  **What to do**: Add cross-references to `docs/ROUTING.md` in sections discussing intent classification and complexity scoring. Ensure all referenced files exist.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Independent; can run with Tasks 6–9.
  **QA Scenarios**:
  ```
  Scenario: Verify cross-references
    Tool: Read
    Steps:
      1. Confirm all [text](path) links in HYBRID_RAG_SHOWCASE.md resolve to existing files
    Evidence: .sisyphus/evidence/task-10-hybrid-links.md
  ```

### Wave 3: New Documentation (Parallel with Wave 2)

- [x] **11. Create docs/TROUBLESHOOTING.md**

  **What to do**: Draw from `docker-compose.yml`, scripts, and common deployment issues:
  - **Joplin Admin Issues**: `fix-joplin-admin.sh`.
  - **Database Persistence**: PGDATA path warnings.
  - **Port Conflicts**: If 3001, 8000, or 5432 are already in use.
  - **Analysis Container (amd64-only)**: Explain how to check and what to do on ARM.
  - **Stuck Ingestion Jobs**: How to check and reset via `pi-ctl.sh` or API.
  - **Out of Memory**: VRAM usage during Wikipedia ingestion.
  - **Network/DNS**: Docker bridge network issues (referencing `pi_agent_net`).
  - **Circuit Breaker Tripped**: How to check `/tmp/parsnip_circuit_breaker.json` and reset.
  - **Missing Env Vars**: Common errors if `.env` is out of date.
  **Must NOT do**: Do not invent error messages; verify from logs/code.
  **Recommended Agent Profile**: `unspecified-high` + `devops`
  **Parallelization**: Independent; can run with all Wave 2 and Wave 3 tasks.
  **QA Scenarios**:
  ```
  Scenario: Verify troubleshooting commands exist
    Tool: Bash
    Steps:
      1. Check every shell command in the doc exists (grep for script/CLI names in repo)
    Evidence: .sisyphus/evidence/task-11-troubleshoot-cmds.txt
  ```

- [x] **12. Create docs/TESTING.md**

  **What to do**: Based on `tests/` directory and `pytest` config:
  - How to run tests (`pytest -q`, `pytest tests/`).
  - Test categories: unit tests (`tests/test_*.py`), integration tests.
  - How to run with coverage.
  - Docker-based integration testing strategy.
  - Adding new tests: patterns for mocking DB connections, LLM calls.
  - CI: `build-and-publish.yml` overview.
  **Must NOT do**: Do not duplicate pytest official docs; focus on this repo's specific patterns.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Independent.
  **QA Scenarios**:
  ```
  Scenario: Verify test commands work
    Tool: Bash
    Steps:
      1. pytest --collect-only → verify test collection works and count matches known test suite
    Evidence: .sisyphus/evidence/task-12-testing.txt
  ```

- [x] **13. Create docs/FRONTEND.md**

  **What to do**: Document the Next.js + assistant-ui frontend:
  - Project structure (`frontend/src/app/`, `frontend/src/components/`).
  - Key components: `ToolUIs.tsx`, `ApprovalUI.tsx`, `WelcomeScreen.tsx`, `Header.tsx`.
  - How the frontend connects to the agent API (`NEXT_PUBLIC_AGENT_URL`, `AGENT_INTERNAL_URL`).
  - Environment variables specific to the frontend.
  - Custom tool UI architecture (`makeAssistantToolUI`).
  - How to run the frontend in dev mode (`npm run dev`) vs. Docker.
  - OpenWebUI as a legacy option (how to switch).
  **Recommended Agent Profile**: `frontend-category-pointer` skill + `deep`
  **Parallelization**: Independent.
  **QA Scenarios**:
  ```
  Scenario: Verify frontend docs match codebase
    Tool: Read (frontend/src/components/*.tsx)
    Steps:
      1. List all .tsx files in frontend/src/components/
      2. Confirm each major component is mentioned in FRONTEND.md
    Evidence: .sisyphus/evidence/task-13-frontend.md
  ```

- [x] **14. Create docs/API_REFERENCE.md**

  **What to do**: Document the Agent API FastAPI endpoints:
  - `/v1/chat/completions` (OpenAI-compatible) — request/response format.
  - `/health`, `/stats` — health and metrics.
  - `/ingestion/status` — ingestion job status.
  - Streaming response format.
  - Authentication: None (local-first) or API key if configured.
  - How to test with `curl` (include 3+ working examples).
  - Link to `http://localhost:8000/docs` (FastAPI auto-docs).
  **Must NOT do**: Do not duplicate FastAPI auto-generated docs wholesale; provide the operator's quick reference.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Independent.
  **QA Scenarios**:
  ```
  Scenario: Verify API examples
    Tool: Bash (curl)
    Steps:
      1. curl -sS http://localhost:8000/health → Expected: {"status":"ok"} or similar
      2. Verify curl commands in the doc run against the actual API
    Evidence: .sisyphus/evidence/task-14-api-curl.json
  ```

- [x] **15. Rewrite CONTRIBUTING.md**

  **What to do**: Expand from 21 lines to a proper contributor guide:
  - Branch naming conventions.
  - Commit message format (conventional commits).
  - PR template: what to include (behavior, tests, evidence).
  - Code style: ruff/black configuration.
  - Testing requirements: `pytest -q` must pass.
  - Docker compose validation: `docker compose config`.
  - How to run linting: `ruff check .`
  - Documentation updates: update docs when changing public API or architecture.
  **Must NOT do**: Do not add generic "be nice" content; keep it specific and actionable.
  **Recommended Agent Profile**: `writing` + `quick`
  **Parallelization**: Independent.
  **QA Scenarios**:
  ```
  Scenario: Verify contributing commands
    Tool: Bash
    Steps:
      1. docker compose config → verify it exits 0
      2. ruff check . → verify it runs (even if it finds issues)
    Evidence: .sisyphus/evidence/task-15-contributing.txt
  ```

- [x] **16. Rewrite SECURITY.md**

  **What to do**: Expand from 17 lines to a proper security policy:
  - Reporting procedures: where to send vulnerability reports (private email or security issue).
  - Supported versions (e.g., latest release + last minor).
  - Secret handling: `.env`, API keys, cloud credentials.
  - Dependency scanning: how to run `pip check` or equivalent.
  - Docker security: non-root containers, read-only mounts where possible.
  - Data privacy notes: self-hosted, no external data sharing by default.
  **Recommended Agent Profile**: `writing` + `security-category-pointer` skill
  **Parallelization**: Independent.
  **QA Scenarios**:
  ```
  Scenario: Verify security references
    Tool: Read
    Steps:
      1. Confirm no hardcoded secrets are referenced in the doc
      2. Confirm all linked contacts/channels exist
    Evidence: .sisyphus/evidence/task-16-security.md
  ```

### Wave 4: Integration Verification (After all previous waves)

- [x] **17. Cross-link audit**

  **What to do**: For every doc, verify all `[text](path)` links resolve to existing files. Fix broken links. Ensure every major doc has a "Related Docs" or "See Also" section linking to 2–4 related guides.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Depends on ALL (6–16 complete).
  **QA Scenarios**:
  ```
  Scenario: Verify all internal links
    Tool: Bash (find + grep)
    Steps:
      1. Extract all [text](path) links from all .md files
      2. Verify each path exists in the repo
      3. List any 404s (missing files)
    Evidence: .sisyphus/evidence/task-17-links.txt
  ```

- [x] **18. Command verification**

  **What to do**: Execute every shell command block (```bash) from every doc against the running Docker stack (or `docker compose config` if the stack isn't running). Verify exit code 0. Capture stdout for evidence.
  **Must NOT do**: Skip destructive commands (e.g., `docker compose down`, backup overwrites); note them as "requires confirmation".
  **Recommended Agent Profile**: `unspecified-high` + `devops`
  **Parallelization**: Depends on ALL.
  **QA Scenarios**:
  ```
  Scenario: Verify non-destructive commands
    Tool: Bash
    Steps:
      1. For each doc, extract bash blocks into a temp script
      2. Run script, verify exit 0
      3. Flag any failures
    Evidence: .sisyphus/evidence/task-18-commands.txt
  ```

- [x] **19. Port verification**

  **What to do**: Ensure all `:3000` references in docs are explicitly about OpenWebUI (legacy). Any `:3000` referring to assistant-ui is a bug.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Depends on ALL.
  **QA Scenarios**:
  ```
  Scenario: Verify port references
    Tool: Bash (grep)
    Steps:
      1. grep -r ':[[:space:]]*3000' docs/ README.md ARCHITECTURE.md
      2. Confirm every match is explicitly about OpenWebUI
    Evidence: .sisyphus/evidence/task-19-ports.txt
  ```

- [x] **20. Final commit and cleanup**

  **What to do**: Stage all changed/new docs. Write a single comprehensive commit message:
  ```
  docs: deep regeneration of documentation suite
  
  - Fix stale ports (assistant-ui :3000 → :3001)
  - Update ingestion source table (13 sources)
  - Expand agent/README.md tool catalog (~40 tools)
  - Rewrite EXTENDING.md as comprehensive extension guide
  - Update DEPLOYMENT.md with scheduler ops and env vars
  - Regenerate root README.md with expanded quickstart and operations
  - Add new docs: TROUBLESHOOTING, TESTING, FRONTEND, API_REFERENCE
  - Rewrite CONTRIBUTING.md and SECURITY.md
  - Cross-link all docs, verify every command and port reference
  
  Verification: all curl commands tested, all internal links verified,
  all port references confirmed correct.
  ```
  Push to the `architecture-improvements` branch.
  **Must NOT do**: Do not push to `main` without explicit user approval.
  **Recommended Agent Profile**: `git-master` skill
  **Parallelization**: Final task; depends on ALL.
  **QA Scenarios**:
  ```
  Scenario: Verify commit
    Tool: Bash (git)
    Steps:
      1. git diff --stat HEAD~1 → confirm docs changed as expected
      2. git log -1 --oneline → confirm commit message matches template
    Evidence: .sisyphus/evidence/task-20-commit.txt
  ```

---

## Final Verification Wave

### Plan Compliance Audit — `oracle`
... (to be run after all tasks)

### Code Quality Review — `unspecified-high`
... (to be run after all tasks)

### Real Manual QA — `unspecified-high`
... (to be run after all tasks)

### Scope Fidelity Check — `deep`
... (to be run after all tasks)

---

## Commit Strategy

- **Wave 1**: `fix(docs): correct stale ports and source counts`
- **Wave 2**: `refactor(docs): regenerate README, agent guide, and extension docs`
- **Wave 3**: `feat(docs): add troubleshooting, testing, frontend, and API reference guides`
- **Wave 4**: `chore(docs): cross-link audit and command verification`
- **Final Squash**: `docs: deep regeneration of documentation suite` (only if user approves squash)

## Success Criteria

### Verification Commands
```bash
# All internal links resolve
grep -rP '\[.*?\]\(.*?\)' docs/ README.md ARCHITECTURE.md | while read match; do file=$(echo "$match" | grep -oP '(?<=\()[^)]+'); test -f "$file" || echo "MISSING: $file"; done

# No stale assistant-ui on :3000
grep -r 'assistant-ui\|Next\.js\|Frontend.*:3000' docs/ README.md ARCHITECTURE.md | grep -v 'OpenWebUI'
# Expected: 0 matches

# All ingestion sources present
python -c "import yaml; print(len(yaml.safe_load(open('ingestion/sources.yaml'))['sources']))"
grep -c '| `ingest_' ingestion/README.md
# Expected: both return 13
```

### Final Checklist
- [ ] All 20 tasks complete with evidence files.
- [ ] All internal links verified.
- [ ] All shell commands tested.
- [ ] All port references verified.
- [ ] Commit pushed to `architecture-improvements` branch.
- [ ] User explicit "okay" before any merge to `main`.

### Plan Compliance Audit
... (to be filled)

### Code Quality Review
... (to be filled)

### Real Manual QA
... (to be filled)

### Scope Fidelity Check
... (to be filled)

---

## Commit Strategy

- **Wave 1**: `fix(docs): correct stale ports and source counts`
- **Wave 2**: `refactor(docs): regenerate README, agent guide, and extension docs`
- **Wave 3**: `feat(docs): add troubleshooting, testing, frontend, and API reference guides`
- **Wave 4**: `chore(docs): cross-link audit and command verification`

## Success Criteria

- Every command in `docs/` runs without error in Docker.
- `grep -r ':3000' docs/ --include='*.md'` only returns references to OpenWebUI (legacy).
- `grep -r 'joplin-mcp' README.md docs/` shows the service is mentioned in Core Services.
- `grep -r 'ROUTING.md' docs/README.md` shows a link.
- `agent/README.md` contains references to all files in `agent/tools/*.py`.