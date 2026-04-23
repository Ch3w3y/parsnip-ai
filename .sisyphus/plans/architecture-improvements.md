# Architecture Improvements: Extensibility + Integration Tightening

## TL;DR

> **Quick Summary**: Refactor Parsnip's architecture to create a plugin-ready ingestion framework, unify Joplin access paths via direct PG pipes, integrate OpenWebUI with agent memories/KB directly through PG, and clean up key technical debt (connection pooling, circuit breaker process-safety, embed model routing).
> 
> **Deliverables**:
> - Ingestion plugin registry with auto-discovery and config-driven source management
> - Unified Joplin access layer replacing 3 convoluted paths with 1 direct PG pipe
> - OpenWebUI direct PG integration for memories and KB search (via pipeline middleware)
> - Connection pooling across all services (agent tools, memory, ingestion)
> - Circuit breaker upgraded to process-safe (Redis/file-based shared state)
> - Embed model routing fix for cross-model queries in holistic_search
> - Stuck ingestion job recovery mechanism
> - Comprehensive test suite (TDD) for all new patterns
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 (plugin registry) → Task 6 (Joplin unified layer) → Task 9 (OpenWebUI memories pipe) → Task 12 (integration tests) → F1-F4

---

## Context

### Original Request
Review the architecture and kick off integration agents to review for integration too. User wants extensibility patterns for easy integration with further ingestion pipelines, tighter OpenWebUI + Joplin integration leveraging their own feature sets with direct pipes to PG (memories especially), and a highly functional extensible core app.

### Interview Summary
**Key Discussions**:
- Hardware corrected: 96GB DDR5 9800x3D desktop (not Pi 5); RAM pressure concerns invalidated
- Priority: extensibility > integration > tech debt; security deferred to future phase
- Joplin has 3 access paths (MCP HTTP, Joplin Server API, ingestion sync) — should unify
- Joplin HITL note workflow: LLM generates note → user edits in Joplin → LLM reads edited note → generates reviewed version → user edits → sync triggers another review cycle. Human-in-the-loop for reports, analysis, information correction. Joplin is the editing surface, LLM is the reviewer.
- OpenWebUI may be replaced — user wants to evaluate slim TS/React chat frontends (assistant-ui is top candidate). Pipeline middleware approach is contingent on staying with OpenWebUI.
- TDD chosen as test strategy
- Embed model mismatch (bge-m3 for GitHub, mxbai-embed-large default) causes garbage results in cross-model holistic_search queries

**Research Findings**:
- Full architecture map via direct codebase reads (all major modules read)
- Circuit breaker uses module-level globals — not process-safe for multi-worker
- memory.py opens fresh connections per call — no pooling
- ingestion/utils.py get_db_connection() — no pooling
- Pipeline Joplin enrichment duplicates agent's own Joplin tools
- Scheduler imports ingestion modules directly (tight coupling)

**Research: PG KB Plugin Patterns** (from web+GitHub search):
- Airweave (`@source` decorator + SourceRegistry + BaseSource lifecycle) — closest architectural match for plugin registry
- pgai (Timescale) — composable pipeline DDL (`loading → chunking → embedding → destination`) — best model for sources.yaml format
- RAGPipe — YAML `source { type } → transforms → sinks` pattern — closest existing YAML schema to sources.yaml
- DataHub — `source.type` → registry lookup + entry_points + `@config_class` proven at 100+ connector scale
- Ragbits — `Source` base class with `from_uri()` + `class_identifier()` for typed source resolution
- Synthesis: `type` field for class resolution + `config` dict for type-specific params + composable `pipeline` components + `schedule` per source

**Research: OpenWebUI Alternatives** (from web+GitHub search):
- **assistant-ui** (9.6k★, TS/React, MIT) — Ultra-light React component library, `makeAssistantToolUI` for per-tool custom UI, `useChat` hooks to any OpenAI-compatible endpoint. Best fit: thin chat layer over Parsnip backend with rich tool rendering. NOT a standalone app — embed in existing Next.js/Vite project.
- **LibreChat** (35.9k★, TS/React, MIT) — Full chat platform with Custom Endpoints + MCP support. Heavier (Express+MongoDB), better out-of-box, but fighting its architecture for custom tool UIs.
- **Big-AGI** (6.9k★, TS/Next.js, MIT) — Frontend-only, up to 5 custom endpoints, best UX, but ReAct mode is prompt-based not native tool_call rendering.
- **Vercel AI SDK + custom Next.js** (19k+★) — Maximum control, `UIToolInvocation` per-tool rendering, but build everything from scratch.
- **Recommendation**: If replacing OpenWebUI → assistant-ui. If keeping OpenWebUI → pipeline middleware approach. Decision needed before Wave 2.

### Metis Review
**Identified Gaps** (addressed):
- Need to verify OpenWebUI's own DB model for integration constraints → will explore during Task 9
- "Tighter integration" scope needs explicit boundaries → bounded to PG-pipe read integration only
- New ingestion sources must follow existing pattern → plugin registry enforces this
- Stuck ingestion_jobs need timeout recovery → Task 11
- Embed model mismatch in holistic_search → Task 8
- **NEW**: Joplin HITL note workflow — LLM generates, user edits in Joplin, LLM reviews edits, cycle continues → needs explicit architecture in Task 6/8
- **NEW**: OpenWebUI replacement evaluation — assistant-ui is top candidate → decision gates Tasks 9/10
- **NEW**: sources.yaml should follow established PG KB plugin patterns (Airweave, pgai, RAGPipe) → reflected in Task 1

---

## Work Objectives

### Core Objective
Transform Parsnip from a hardcoded multi-service stack into an extensible platform where new knowledge sources plug in via a declarative registry (following Airweave/pgai/RAGPipe patterns), Joplin becomes a human-in-the-loop editing surface for LLM-generated content (note→edit→review→sync cycles), OpenWebUI or its replacement integrates through direct PG pipes, and key tech debt is resolved.

### Concrete Deliverables
- `ingestion/registry.py` — Plugin registry with auto-discovery (inspired by Airweave's `@source` + SourceRegistry), schema validation, declarative config
- `ingestion/sources.yaml` — Config-driven source definitions with composable pipeline components (inspired by pgai's `loading→chunking→embedding→destination` and RAGPipe's `source→transforms→sinks`)
- `agent/tools/joplin_pg.py` — Unified Joplin access layer going direct to PG (replaces joplin_mcp.py HTTP chain), includes HITL note workflow primitives
- `agent/tools/joplin_hitl.py` — Human-in-the-loop note workflow: generate→sync→detect edits→review→repeat cycle
- Frontend path (decision-gated):
  - **If keeping OpenWebUI**: `pipelines/memories_pipeline.py` + `pipelines/kb_pipeline.py` — Pipeline model choices querying PG directly
  - **If replacing OpenWebUI**: `frontend/` — assistant-ui React component library with `makeAssistantToolUI` for each Parsnip tool, `useChat` pointing at agent API
- Connection pool module (named, multi-DSN) shared across agent tools
- File-based circuit breaker state
- Embed model routing regression tests
- Stuck-job recovery in scheduler
- TDD test suite covering all new patterns

### Definition of Done
- [ ] `uv run pytest tests/` passes with ≥90% coverage on new modules
- [ ] Adding a new ingestion source requires only: 1) create script, 2) add to `sources.yaml`, 3) optionally update `ROUTING_CONFIG`
- [ ] Joplin tool calls go through 1 network hop (direct PG) instead of 2 (HTTP→MCP→PG)
- [ ] Joplin HITL note workflow: LLM can generate a note, detect user edits, review the edited version, and repeat the cycle
- [ ] Frontend integration: EITHER OpenWebUI pipelines show memories/KB as model choices querying PG, OR assistant-ui chat UI with custom tool rendering
- [ ] `holistic_search` embed model routing verified correct by regression tests (already fixed in codebase)
- [ ] Stuck ingestion jobs auto-recover after configurable timeout
- [ ] Circuit breaker state survives agent restarts

### Must Have
- Ingestion plugin registry with declarative config (following Airweave/pgai/RAGPipe patterns)
- Unified Joplin access via direct PG (replace double-hop)
- Joplin HITL note workflow (generate→edit→review→sync cycle)
- Frontend decision: keep OpenWebUI (pipeline middleware) OR replace with assistant-ui (TS/React)
- PG-direct read integration for memories and KB (path depends on frontend choice)
- Embed model routing regression tests
- Connection pooling for memory tools + ingestion
- TDD test coverage for all new modules

### Must NOT Have (Guardrails)
- No security hardening (deferred to separate plan)
- No changes to existing embedding models or stored vectors (only regression tests for routing)
- No changes to LangGraph graph structure or checkpoint system
- No OpenWebUI codebase modifications (only pipeline middleware)
- No breaking changes to existing tool contracts (joplin_mcp.py remains for backward compat during transition)
- No external dependencies beyond what's already in requirements (no Redis — use file-based shared state)
- No rewriting of ingestion scripts — only registry scaffolding around them
- No Pi 5 assumptions (this is a 96GB DDR5 desktop)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest-based)
- **Automated tests**: TDD (RED-GREEN-REFACTOR)
- **Framework**: pytest (existing in project)
- **Each task follows**: RED (failing test) → GREEN (minimal impl) → REFACTOR

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Frontend/UI**: Use Playwright — navigate OpenWebUI, assert memories panel, screenshot
- **CLI/TUI**: Use interactive_bash (tmux) — run pytest, validate output
- **API/Backend**: Use Bash (curl) — hit endpoints, assert status + response fields
- **Library/Module**: Use Bash (python -c) — import, call functions, compare output

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Decision Spike — before any implementation):
├── Task 17: Frontend decision spike — OpenWebUI vs assistant-ui evaluation [quick]

Wave 1 (Start Immediately after Wave 0 — foundation + scaffolding):
├── Task 1: Ingestion plugin registry + sources.yaml [deep]
├── Task 2: Multi-DSN connection pool module [unspecified-high]
├── Task 3: Embed model routing verification tests [quick]
├── Task 4: Circuit breaker process-safe state (file-based) [unspecified-high]
└── Task 5: Stuck ingestion job recovery [quick]

Wave 2 (After Wave 1 — integrations + migrations):
├── Task 6: Unified Joplin PG access layer + HITL workflow (depends: 2) [deep]
├── Task 7: Scheduler decouple from ingestion imports (depends: 1) [deep]
├── Task 8: Migrate agent Joplin tools to unified layer (depends: 6) [unspecified-high]
├── Task 11: Pipeline Joplin enrichment dedup (depends: 8) [quick]
├── Task 12: Memory tools use shared pool (depends: 2) [quick]
│
│   ┌─── FRONTEND DECISION GATE (Task 17 result) ───┐
│   │                                                 │
│   IF keeping OpenWebUI:                            IF replacing with assistant-ui:
│   ├── Task 9:  OWUI memories pipeline [deep]       ├── Task 18: assistant-ui setup [deep]
│   ├── Task 10: OWUI KB search pipeline [u-h]       ├── Task 19: assistant-ui tool UIs [u-h]
│   └── (continue to Wave 3)                         └── (continue to Wave 3)

Wave 3 (After Wave 2 — validation + docs):
├── Task 13: Integration test suite for plugin registry (depends: 1, 7) [unspecified-high]
├── Task 14: Integration test suite for Joplin + frontend pipes (depends: 8, 9or18, 10or19) [unspecified-high]
├── Task 15: Update ARCHITECTURE.md + ingestion README (depends: 1, 6, 8) [writing]
└── Task 16: Update ROUTING_CONFIG docs + pattern checklist (depends: 1, 3) [writing]

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: Task 17 → Task 1 → Task 7 → Task 13 → F1-F4
Secondary Path: Task 2 → Task 6 → Task 8 → Task 14 → F1-F4
Frontend Path: Task 17 → Task 9/10 OR Task 18/19 → Task 14 → F1-F4
Parallel Speedup: ~55% faster than sequential
Max Concurrent: 6 (Wave 1)
```

### Dependency Matrix

| Task | Blocked By | Blocks | Wave |
|------|-----------|--------|------|
| 17 | - | 9,10,18,19 | 0 |
| 1 | - | 7, 13, 15, 16 | 1 |
| 2 | - | 6, 9, 10, 12 | 1 |
| 3 | - | 16 | 1 |
| 4 | - | - | 1 |
| 5 | - | - | 1 |
| 6 | 2 | 8, 11, 14, 15 | 2 |
| 7 | 1 | 13 | 2 |
| 8 | 6 | 11, 14 | 2 |
| 9 | 2, 17 (OWUI) | 14 | 2 |
| 10 | 2, 3, 17 (OWUI) | 14 | 2 |
| 11 | 8 | - | 2 |
| 12 | 2 | - | 2 |
| 18 | 17 (replace) | 14 | 2 |
| 19 | 17, 18 (replace) | 14 | 2 |
| 13 | 1, 7 | F1-F4 | 3 |
| 14 | 8, (9 or 18), (10 or 19) | F1-F4 | 3 |
| 15 | 1, 6, 8, 9 | F1-F4 | 3 |
| 16 | 1, 3 | F1-F4 | 3 |

### Agent Dispatch Summary

- **Wave 1**: 6 — T1 → `deep`, T2 → `quick`, T3 → `quick`, T4 → `unspecified-high`, T5 → `quick`, T6 → `deep`
- **Wave 2**: 6 — T7 → `deep`, T8 → `unspecified-high`, T9 → `deep`, T10 → `unspecified-high`, T11 → `quick`, T12 → `quick`
- **Wave 3**: 4 — T13 → `unspecified-high`, T14 → `unspecified-high`, T15 → `writing`, T16 → `writing`
- **FINAL**: 4 — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [ ] 1. Ingestion Plugin Registry + sources.yaml

  **What to do**:
  - Create `ingestion/registry.py` with a `SourceRegistry` class (inspired by Airweave's SourceRegistry + DataHub's entry_point registry):
    - Reads `ingestion/sources.yaml` on startup
    - Validates each source entry has required fields (name, type, schedule, conflict_strategy)
    - Provides `get_source(name)`, `list_sources()`, `register_source()` methods
    - `type` field maps to a resolved class/function (like DataHub's `source.type` → registry lookup, Airweave's `@source` decorator)
    - Auto-discovers ingestion scripts matching `ingest_*.py` pattern in `ingestion/` (14 files currently exist)
    - Validates that declared modules exist and have `main_async()` or `main()` function (ingest_wikipedia.py uses `main()`, not `main_async()`)
  - Create `ingestion/sources.yaml` with composable pipeline components (inspired by pgai's `loading→chunking→embedding→destination` and RAGPipe's `source→transforms→sinks`):
    - name, module/type path, schedule (cron expression), conflict_strategy, embed_model, enabled flag
    - Pipeline components: `chunking: {type: recursive_character, chunk_size: 700}`, `embedding: {provider: ollama, model: mxbai-embed-large}` — making the ingestion pipeline per-source explicit
    - Example: 
      ```yaml
      wikipedia:
        module: ingest_wikipedia
        schedule: "0 2 * * 0"
        conflict: update
        pipeline:
          embedding: {provider: ollama, model: mxbai-embed-large}
        enabled: true
      github:
        module: ingest_github
        schedule: "0 3 * * 1"
        conflict: skip
        pipeline:
          embedding: {provider: ollama, model: bge-m3}
        enabled: true
      ```
  - **ENTRY POINT NOTE**: All 14 ingestion scripts use `main_async()` as entry point EXCEPT `ingest_wikipedia.py` which uses `main()` (it's a one-time CLI seed script with argparse, not a scheduled job). The registry must handle both `main_async` and `main` entry points — use `getattr(source, 'main_async', None) or getattr(source, 'main', None)`.
  - RED: Write `tests/test_registry.py` first — test YAML parsing, validation, auto-discovery, missing module detection
  - GREEN: Implement registry to pass all tests
  - REFACTOR: Clean up module loading, add helpful error messages

  **Must NOT do**:
  - Do not rewrite existing ingestion scripts
  - Do not change how ingestion scripts are called (they still have `main_async()`)
  - Do not add scheduling logic to the registry (scheduler remains separate)
  - Do not use Pydantic for YAML schema — keep it lightweight with stdlib dataclasses

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Designing a plugin registry requires careful interface design + TDD discipline
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `architecture-category-pointer`: Overkill — this is a specific module design, not system architecture

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4, 5, 6)
  - **Blocks**: Tasks 7, 13, 15, 16
  - **Blocked By**: None

  **References**:

  **Pattern References** (existing code to follow):
  - `ingestion/utils.py:163-167` — `get_db_connection()` pattern that all ingestion scripts use
  - `ingestion/utils.py:266-290` — `create_job()`, `update_job_progress()`, `finish_job()` — the ingestion lifecycle the registry must understand
  - `scheduler/scheduler.py:25-31` — How scheduler currently imports ingestion modules directly (this is what the registry replaces)

  **API/Type References** (contracts to implement against):
  - Any `ingest_*.py` file in `ingestion/` (14 total) — 13 have `main_async()` as entry point; `ingest_wikipedia.py` uses `main()` (one-time CLI seed script)
  - `ingestion/README.md` — Source list, conflict strategies, and scheduling table

  **External References**:
  - Python `importlib` docs — For dynamic module loading from string paths
  - PyYAML docs — For YAML parsing (`yaml.safe_load`)
  - Airweave (`github.com/airweave-ai/airweave`) — `@source` decorator + `SourceRegistry` + `BaseSource` lifecycle. Our registry follows this pattern but with YAML config instead of decorators.
  - pgai (`github.com/timescale/pgai`) — Composable pipeline DDL: `loading → parsing → chunking → embedding → destination`. Our `sources.yaml` `pipeline:` section follows this composable component model.
  - RAGPipe (`github.com/avasis-ai/ragpipe`) — YAML `source { type } → transforms → sinks` pattern. Closest existing YAML schema to our format.
  - DataHub (`github.com/datahub-project/datahub`) — `source.type` → registry lookup + `@config_class`. Proven at 100+ connector scale.

  **WHY Each Reference Matters**:
  - `utils.py:163-167`: The registry must ensure registered modules can call `get_db_connection()` successfully
  - `scheduler.py:25-31`: The current direct-import pattern — the registry should provide a `get_main_async(source_name)` interface that the scheduler can call instead
  - `README.md`: Authoritative list of all sources, their conflict strategies, and schedules — the YAML config must match this

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_registry.py
  - [ ] `uv run pytest tests/test_registry.py` → PASS (≥8 tests covering: YAML load, validation, auto-discovery, missing module, duplicate name, enabled/disabled)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Registry discovers all 14 existing sources
    Tool: Bash
    Preconditions: ingestion/ directory exists with 14 ingest_*.py files
    Steps:
      1. Run: python -c "from ingestion.registry import SourceRegistry; r = SourceRegistry(); print(len(r.list_sources()))"
      2. Assert output is "14"
    Expected Result: Registry auto-discovers all 14 ingestion scripts
    Failure Indicators: Count ≠ 14, ImportError, ModuleNotFoundError
    Evidence: .sisyphus/evidence/task-1-registry-discovery.txt

  Scenario: Invalid YAML source entry fails validation
    Tool: Bash
    Preconditions: sources.yaml has one entry with missing 'module' field
    Steps:
      1. Run: python -c "from ingestion.registry import SourceRegistry; r = SourceRegistry(); print('ok')"
      2. Assert ValueError or ValidationError is raised
    Expected Result: Registry raises clear error about missing 'module' field
    Failure Indicators: Registry initializes without error, or cryptic error
    Evidence: .sisyphus/evidence/task-1-registry-validation.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `refactor(ingestion): add plugin registry and sources.yaml config`
  - Files: `ingestion/registry.py`, `ingestion/sources.yaml`, `tests/test_registry.py`

- [ ] 2. Multi-DSN Connection Pool for Agent Tools

  **What to do**:
  - Create `agent/tools/db_pool.py` with a **named pool registry** pattern:
    - `init_pool(name, dsn, max_size=10)` — creates a named pool (e.g., "agent_kb", "joplin")
    - `get_pool(name="agent_kb")` — returns the pool for the given name
    - Context manager: `async with get_pool("agent_kb").connection() as conn:`
    - Support for multiple databases: `init_pool("joplin", joplin_dsn, max_size=5)` for the Joplin DB
  - RED: Write `tests/test_db_pool.py` first — test pool init, named pools, multi-DSN, connection acquisition, pool exhaustion, error recovery
  - GREEN: Implement pool module
  - REFACTOR: Adjust `min_size`/`max_size` defaults for the 96GB machine (can afford more connections)

  **Must NOT do**:
  - Do not change the graph.py pool (that's for LangGraph checkpoints at `graph.py:69,77-80` using psycopg_pool directly — separate concern)
  - Do not force all tools to use the pool immediately (gradual migration)
  - Do not add middleware or logging to the pool (keep it thin)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multi-DSN pool registry needs careful interface design, not just a singleton
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4, 5, 6)
  - **Blocks**: Tasks 6, 9, 10, 12
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `agent/tools/memory.py:34` — Current pattern: `async with await psycopg.AsyncConnection.connect(db_url) as conn:` — this is what the pool replaces
  - `agent/main.py:44-61` — Agent lifespan where pool init should be added
  - `agent/graph.py:69,77-80` — Existing psycopg_pool for LangGraph (separate pool, don't modify)
  - `joplin-mcp/server.py:42-46` — Joplin DB connection config (separate DSN needed for "joplin" pool)

  **API/Type References**:
  - `psycopg_pool.AsyncConnectionPool` — The built-in psycopg pool class
  - `agent/config.py:Settings.database_url` — DSN source for agent_kb pool
  - Joplin DB env vars: `JOPLIN_DB_HOST`, `JOPLIN_DB_PORT`, `JOPLIN_DB_NAME`, `JOPLIN_DB_USER`, `JOPLIN_DB_PASSWORD` — DSN for joplin pool

  **WHY Each Reference Matters**:
  - `memory.py:34`: This is the primary consumer pattern — every `psycopg.AsyncConnection.connect(db_url)` call in tools/ should eventually become `pool.connection()`
  - `main.py:44-61`: The lifespan context is where `init_pool("agent_kb", ...)` and `init_pool("joplin", ...)` should be called
  - `joplin-mcp/server.py:42-46`: The joplin pool needs the same DSN components the MCP server uses
  - `graph.py:69,77-80`: The separate LangGraph pool remains untouched — TWO pool modules in codebase (graph.py for checkpoints, db_pool.py for tools)

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_db_pool.py
  - [ ] `uv run pytest tests/test_db_pool.py` → PASS (≥7 tests: init, named pools, multi-DSN, acquire, release, exhaustion, error recovery)

  **QA Scenarios:**

  ```
  Scenario: Multi-DSN pool provides connections for both databases
    Tool: Bash
    Preconditions: PostgreSQL accessible at DATABASE_URL and Joplin DB accessible
    Steps:
      1. Run: python -c "
         import asyncio
         from agent.tools.db_pool import init_pool, get_pool
         async def test():
             await init_pool('agent_kb', max_size=5)
             await init_pool('joplin', joplin_dsn, max_size=3)
             agent_pool = get_pool('agent_kb')
             joplin_pool = get_pool('joplin')
             print(f'agent_kb pool: {type(agent_pool).__name__}')
             print(f'joplin pool: {type(joplin_pool).__name__}')"
    Expected Result: Both named pools initialized and accessible
    Failure Indicators: KeyError for unknown pool name, pool confusion
    Evidence: .sisyphus/evidence/task-2-pool-multi-dsn.txt

  Scenario: Pool handles connection errors gracefully
    Tool: Bash
    Preconditions: Temporarily set DATABASE_URL to invalid value
    Steps:
      1. Run with invalid DSN and assert clear error raised
    Expected Result: Pool raises clear error on invalid DSN
    Failure Indicators: Silent failure, hang, or cryptic error
    Evidence: .sisyphus/evidence/task-2-pool-error.txt
  ```

  **Commit**: YES
  - Message: `refactor(agent): multi-DSN connection pool for tool DB access`
  - Files: `agent/tools/db_pool.py`, `tests/test_db_pool.py`

- [ ] 3. Embed Model Routing Verification Tests

  **What to do**:
  - **The embed model routing bug is ALREADY FIXED** in the current codebase:
    - `holistic_search.py:149-169` generates both `mxbai_embs` and `bge_embs`, then selects per-layer using `SOURCE_MODEL_MAP.get(sources[0], DEFAULT_MODEL)`
    - `kb_search.py:35-37` uses `embed_model = SOURCE_MODEL_MAP.get(source, DEFAULT_MODEL)` then `await get_embedding(query, model=embed_model)`
  - This task is to **add regression tests** confirming the fix stays correct:
    - Test that `holistic_search` generates both embedding sets when GitHub is in the source list
    - Test that `kb_search` passes the correct model to `get_embedding` for each source
    - Test that `_rrf_search` in `research.py` receives pre-computed embeddings from its caller (it doesn't call `get_embedding` itself)
  - RED: Write `tests/test_embed_routing.py` with tests that verify the current behavior
  - GREEN: All tests should pass immediately (behavior is correct, just untested)
  - REFACTOR: N/A — no code changes needed

  **Must NOT do**:
  - Do NOT change any search/embed code (it's already correct)
  - Do not re-embed existing GitHub chunks
  - Do not change SOURCE_MODEL_MAP keys or values

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Just writing verification tests for existing correct behavior
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4, 5, 6)
  - **Blocks**: Task 16
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `agent/tools/holistic_search.py:149-169` — Correct embed routing already implemented (generates both embed sets, selects per-layer)
  - `agent/tools/kb_search.py:35-37` — Correct model selection per source
  - `agent/tools/router.py:24-26` — `SOURCE_MODEL_MAP` — the routing table
  - `agent/tools/research.py:34` — `_rrf_search()` receives pre-computed embeddings; does NOT call `get_embedding` itself (caller's responsibility)

  **External References**:
  - None needed (testing existing code, no new APIs)

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_embed_routing.py
  - [ ] `uv run pytest tests/test_embed_routing.py` → PASS (≥3 tests: holistic dual-embed generation, kb model routing, research receives pre-computed embeddings)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: holistic_search generates both mxbai and bge-m3 embeddings
    Tool: Bash
    Preconditions: Ollama running with both models available
    Steps:
      1. Run: uv run pytest tests/test_embed_routing.py::test_holistic_dual_embedding -v
      2. Assert: PASS (both embedding sets generated, correct model selected per layer)
    Expected Result: Code correctly generates both embedding sets and routes per layer
    Failure Indicators: Only mxbai generated, or wrong model for GitHub layer
    Evidence: .sisyphus/evidence/task-3-embed-verify.txt

  Scenario: kb_search uses correct model per source
    Tool: Bash
    Preconditions: Same as above
    Steps:
      1. Run: uv run pytest tests/test_embed_routing.py::test_kb_model_routing -v
      2. Assert: PASS (bge-m3 for github, mxbai for others)
    Expected Result: Each source maps to its configured embed model
    Failure Indicators: Default model used for all sources
    Evidence: .sisyphus/evidence/task-3-kb-routing.txt
  ```

  **Commit**: YES
  - Message: `test(search): add embed model routing regression tests`
  - Files: `tests/test_embed_routing.py`

- [ ] 4. Process-Safe Circuit Breaker (File-Based Shared State)

  **What to do**:
  - Replace module-level globals in `graph_guardrails.py` with file-based state:
    - State file: `/tmp/parsnip_circuit_breaker.json` containing `{"tripped": bool, "tripped_at": float}`
    - `_trip_circuit()` writes JSON atomically (write-to-tmp, rename)
    - `_circuit_is_open()` reads the file on every check
    - File-based approach works across uvicorn workers and survives restarts
  - Add auto-reset: if `tripped_at` is > 5 minutes old AND file exists, clear it on startup
  - RED: Write `tests/test_circuit_breaker.py` — test trip, reset, auto-expiry, multi-process safety
  - GREEN: Implement file-based state replacement
  - REFACTOR: Remove `threading.Lock` (no longer needed with file-based state), add `pathlib.Path` for state directory

  **Must NOT do**:
  - Do not add Redis dependency (file-based is sufficient for single-machine)
  - Do not change the fallback cascade logic — only the state storage mechanism
  - Do not change cooldown duration (5 min is deliberate)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Requires careful atomic file operations + multi-process safety reasoning
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 5, 6)
  - **Blocks**: None directly
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `agent/graph_guardrails.py:21-24` — Current globals: `_OPENROUTER_TRIPPED`, `_OPENROUTER_TRIPPED_AT`, `_OPENROUTER_LOCK`
  - `agent/graph_guardrails.py:27-58` — `_trip_circuit()`, `_reset_circuit()`, `_circuit_is_open()` — the functions to modify

  **API/Type References**:
  - Python `json` + `tempfile` + `os.rename` — Atomic file write pattern (write to temp, atomic rename)

  **WHY Each Reference Matters**:
  - `guardrails.py:21-24`: These globals must become file reads/writes. The threading.Lock becomes unnecessary.
  - The atomic rename pattern (`os.rename(tmp, target)`) prevents corrupted reads from partial writes

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_circuit_breaker.py
  - [ ] `uv run pytest tests/test_circuit_breaker.py` → PASS (≥6 tests: trip, is_open, auto_reset, concurrent_access, stale_file_cleanup, fresh_start)

  **QA Scenarios:**

  ```
  Scenario: Circuit breaker state survives process restart
    Tool: Bash
    Preconditions: Clean state (no /tmp/parsnip_circuit_breaker.json)
    Steps:
      1. Run: python -c "
         from graph_guardrails import _trip_circuit
         _trip_circuit()
         print('Tripped')"
      2. Run: python -c "
         from graph_guardrails import _circuit_is_open
         print(f'Circuit open: {_circuit_is_open()}')"
      3. Assert output contains "Circuit open: True"
    Expected Result: Second process reads state written by first process
    Failure Indicators: Circuit open: False (state lost between processes)
    Evidence: .sisyphus/evidence/task-4-circuit-cross-process.txt

  Scenario: Circuit auto-resets after cooldown
    Tool: Bash
    Preconditions: Circuit is tripped with tripped_at > 5 minutes ago
    Steps:
      1. Write a stale circuit breaker file: echo '{"tripped": true, "tripped_at": 0}' > /tmp/parsnip_circuit_breaker.json
      2. Run: python -c "from graph_guardrails import _circuit_is_open; print(_circuit_is_open())"
      3. Assert output is "False"
    Expected Result: Stale circuit breaker self-heals on check
    Failure Indicators: Still returns True (stale state not cleaned up)
    Evidence: .sisyphus/evidence/task-4-circuit-autoreset.txt
  ```

  **Commit**: YES
  - Message: `fix(guardrails): process-safe circuit breaker with file-based state`
  - Files: `agent/graph_guardrails.py`, `tests/test_circuit_breaker.py`

- [ ] 5. Stuck Ingestion Job Recovery

  **What to do**:
  - Add a `recover_stuck_jobs()` function to `ingestion/utils.py`:
    - Mark jobs as 'failed' if `status='running'` AND `started_at < NOW() - interval '2 hours'` (configurable via env)
    - Log which jobs were recovered with source name and stuck duration
  - Add a recovery step to `scheduler/scheduler.py` startup:
    - Call `recover_stuck_jobs()` before scheduling begins
    - Also call it before each scheduled job run (as a guard)
  - RED: Write `tests/test_stuck_jobs.py` — test recovery of old running jobs, no-op for recent running jobs, no-op for done/failed jobs
  - GREEN: Implement recovery function
  - REFACTOR: Add `INGESTION_JOB_TIMEOUT_HOURS` env var (default: 2)

  **Must NOT do**:
  - Do not change the ingestion_jobs table schema
  - Do not retry failed jobs automatically (only unstick 'running' → 'failed')
  - Do not add job queuing or dependency management

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small targeted fix, clear scope
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 6)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `ingestion/utils.py:273-290` — `create_job()`, `finish_job()` — the lifecycle this recovery function integrates with
  - `scheduler/scheduler.py:226-232` — `main()` startup sequence — where recovery call should be added
  - `scheduler/scheduler.py:55-67` — `wikipedia_seed_complete()` — example of a job status check

  **API/Type References**:
  - `ingestion_jobs` table schema: `id, source, status, total, processed, metadata, started_at, finished_at`

  **WHY Each Reference Matters**:
  - `utils.py:273-290`: Recovery function needs to `UPDATE ingestion_jobs SET status='failed' ... WHERE status='running' AND started_at < NOW() - INTERVAL '2 hours'`
  - `scheduler.py:226-232`: Before `scheduler.start()`, call `recover_stuck_jobs()` to clean up any jobs left in 'running' from a crashed scheduler

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_stuck_jobs.py
  - [ ] `uv run pytest tests/test_stuck_jobs.py` → PASS (≥4 tests: recovery of stale running, no-op for recent, no-op for done, recovery count)

  **QA Scenarios:**

  ```
  Scenario: Stuck Wikipedia job is auto-recovered
    Tool: Bash
    Preconditions: ingestion_jobs has a 'running' Wikipedia entry from >2h ago
    Steps:
      1. Insert a stale job: psql "$DATABASE_URL" -c "INSERT INTO ingestion_jobs (source, status, started_at) VALUES ('wikipedia_test', 'running', NOW() - INTERVAL '3 hours')"
      2. Run: python -c "
         import asyncio
         from ingestion.utils import recover_stuck_jobs, get_db_connection
         async def test():
             conn = await get_db_connection()
             count = await recover_stuck_jobs(conn)
             print(f'Recovered {count} stuck jobs')
             await conn.close()
         asyncio.run(test())"
      3. Assert output contains "Recovered 1 stuck jobs"
      4. Verify: psql "$DATABASE_URL" -c "SELECT status FROM ingestion_jobs WHERE source='wikipedia_test'" → "failed"
    Expected Result: Stuck job marked as failed, recovery count returned
    Failure Indicators: Count=0, or job still 'running'
    Evidence: .sisyphus/evidence/task-5-stuck-recovery.txt

  Scenario: Recent running jobs are NOT recovered
    Tool: Bash
    Preconditions: ingestion_jobs has a 'running' entry started 5 minutes ago
    Steps:
      1. Insert a fresh job: psql "$DATABASE_URL" -c "INSERT INTO ingestion_jobs (source, status, started_at) VALUES ('fresh_test', 'running', NOW() - INTERVAL '5 minutes')"
      2. Run recovery function
      3. Verify job is still 'running'
    Expected Result: Fresh jobs left alone
    Failure Indicators: Fresh job marked as failed
    Evidence: .sisyphus/evidence/task-5-fresh-not-recovered.txt
  ```

  **Commit**: YES
  - Message: `fix(scheduler): auto-recover stuck ingestion jobs on timeout`
  - Files: `ingestion/utils.py`, `scheduler/scheduler.py`, `tests/test_stuck_jobs.py`

- [ ] 6. Unified Joplin PG Access Layer + HITL Workflow Primitives

  **What to do**:
  - Create `agent/tools/joplin_pg.py` — A unified direct-to-PG access layer for Joplin data:
    - Uses the named pool `"joplin"` from Task 2's `db_pool.py` (DSN constructed from `JOPLIN_DB_*` env vars)
    - Provides the same 12 tool functions as `joplin_mcp.py` but via direct DB queries
    - Functions: `create_notebook()`, `create_note()`, `update_note()`, `edit_note()`, `delete_note()`, `get_note()`, `search_notes()`, `list_notebooks()`, `list_tags()`, `get_tags_for_note()`, `upload_resource()`, `ping()`
    - Uses the named pool `"joplin"` from Task 2's `db_pool.py` with a separate DSN for the Joplin DB
    - Handles `DEFAULT_OWNER_ID` via env var (same as MCP server)
  - Create `agent/tools/joplin_hitl.py` — HITL note workflow primitives:
    - `generate_note(title, content, notebook_id)` — LLM creates a note, returns note_id + joplin:// deep-link
    - `detect_edits(note_id, since_version)` — compares current note content against a stored "last LLM version" to detect human edits. Uses Joplin's `updated_time` timestamp + content hash comparison
    - `review_edited_note(note_id, original_content, edited_content)` — returns a structured diff + the user's edits for the LLM to review and incorporate
    - `publish_review(note_id, reviewed_content)` — updates the note with the LLM's reviewed version, stores this as the new "last LLM version"
    - Storage: uses `agent_memories` or a new `joplin_hitl_sessions` table to track: note_id, last_llm_version (text), last_llm_version_hash, cycle_count, status (active/completed)
    - The cycle: generate_note → user edits in Joplin → detect_edits (triggered by sync/watcher) → review_edited_note → publish_review → user edits again → repeat
  - RED: Write `tests/test_joplin_pg.py` — test each CRUD operation; write `tests/test_joplin_hitl.py` — test the note cycle (generate→detect→review→publish)
  - GREEN: Implement all 12 CRUD functions + 4 HITL workflow functions
  - REFACTOR: Extract common query patterns into helpers (e.g., `_get_note_by_id`, `_ts_to_iso`, `_content_hash`)

  **Must NOT do**:
  - Do not delete `joplin_mcp.py` yet (backward compat — migration in Task 8)
  - Do not modify `joplin-mcp/server.py` (it continues to serve non-agent MCP clients)
  - Do not add features beyond what the current MCP tools provide (for CRUD) + HITL workflow (new)
  - Do not change Joplin DB schema
  - Do not implement the sync watcher/trigger in this task (that's a scheduler concern)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 12 CRUD functions + 4 HITL workflow functions, careful SQL, and TDD
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 11, 12)
  - **Blocks**: Tasks 8, 11, 14, 15
  - **Blocked By**: Task 2 (needs named pool "joplin" from multi-DSN pool module)

  **References**:

  **Pattern References**:
  - `joplin-mcp/server.py:42-46` — Joplin DB connection config (host, port, db name, user, password) — reuse these exact env vars for the `"joplin"` pool init
  - `joplin-mcp/server.py:100-900` (scattered) — SQL query patterns for CRUD operations — copy the SQL, not the HTTP
  - `agent/tools/joplin_mcp.py:19-30` — Current HTTP proxy pattern — this is what we're replacing with direct PG
  - `agent/tools/db_pool.py` (from Task 2) — Named connection pool — use `get_pool("joplin")` for Joplin DB pool
  - `agent/tools/notes.py:10-60` — `save_note` tool — this stores in knowledge_chunks (not Joplin); the HITL workflow stores in Joplin. These serve different purposes (save_note = KB persistence, HITL = human-editable review cycle)

  **API/Type References**:
  - Joplin DB tables: `notes`, `notebooks`, `tags`, `note_tags`, `resources` — as used by the MCP server
  - Joplin notes columns: `id`, `title`, `body`, `parent_id` (notebook), `updated_time`, `user_updated_time`, `is_conflict`, `markup` (always Markdown)
  - Env vars: `JOPLIN_DB_HOST`, `JOPLIN_DB_PORT`, `JOPLIN_DB_NAME`, `JOPLIN_DB_USER`, `JOPLIN_DB_PASSWORD`, `JOPLIN_OWNER_ID`

  **WHY Each Reference Matters**:
  - `server.py:42-46`: The joplin_pg module must use the EXACT same connection parameters — this ensures we're hitting the same Joplin DB
  - `server.py:100-900`: The SQL queries are already battle-tested — copying them avoids re-discovering Joplin's schema quirks
  - `joplin_mcp.py:19-30`: Current double-hop (HTTP→MCP→PG) — measuring latency before and after will show the improvement
  - `notes.py:10-60`: The HITL workflow is distinct from save_note — save_note persists to KB, HITL persists to Joplin for human editing. The HITL reviewed+approved output can later be saved_note'd to KB if desired.

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_joplin_pg.py
  - [ ] `uv run pytest tests/test_joplin_pg.py` → PASS (≥12 tests covering all CRUD + search + ping)
  - [ ] Test file created: tests/test_joplin_hitl.py
  - [ ] `uv run pytest tests/test_joplin_hitl.py` → PASS (≥4 tests: generate, detect_edits, review, publish)

  **QA Scenarios:**

  ```
  Scenario: Create and retrieve a Joplin note via direct PG
    Tool: Bash
    Preconditions: Joplin DB accessible, JOPLIN_DB_PASSWORD set
    Steps:
      1. Run: python -c "
         import asyncio
         from tools.joplin_pg import create_note, get_note
         async def test():
             result = await create_note.ainvoke({'title': 'PG Direct Test', 'content': 'Created via direct PG', 'notebook_id': ''})
             print(f'Create result: {result[:50]}')
         asyncio.run(test())"
      2. Assert result contains note ID or success message
    Expected Result: Note created directly in Joplin DB without HTTP hop
    Failure Indicators: ConnectionError, asyncpg error, permission denied
    Evidence: .sisyphus/evidence/task-6-joplin-pg-create.txt

  Scenario: HITL note workflow: generate→detect→review→publish
    Tool: Bash
    Preconditions: Joplin DB accessible
    Steps:
      1. Generate: python -c "
         import asyncio
         from tools.joplin_hitl import generate_note
         async def test():
             result = await generate_note.ainvoke({'title': 'HITL Test', 'content': 'LLM draft', 'notebook_id': ''})
             print(result)
         asyncio.run(test())"
      2. Simulate user edit: UPDATE notes SET body = 'LLM draft\n\nUSER EDIT: fixed section 3' WHERE title = 'HITL Test'
      3. Detect edits: python -c "
         from tools.joplin_hitl import detect_edits
         result = await detect_edits.ainvoke({'note_id': '<id>'})"
      4. Assert: detect_edits returns diff showing user's addition
    Expected Result: HITL cycle detects human edits and returns structured diff for LLM review
    Failure Indicators: No edits detected, or diff is unstructured
    Evidence: .sisyphus/evidence/task-6-hitl-workflow.txt
    Failure Indicators: PG path slower or equal (would indicate query inefficiency)
    Evidence: .sisyphus/evidence/task-6-joplin-latency-comparison.txt
  ```

  **Commit**: YES
  - Message: `refactor(joplin): unified PG-direct access layer`
  - Files: `agent/tools/joplin_pg.py`, `tests/test_joplin_pg.py`

- [ ] 7. Scheduler Decouple from Ingestion via Registry

  **What to do**:
  - Refactor `scheduler/scheduler.py` to use `SourceRegistry` instead of direct imports:
    - Replace `sys.path.insert(0, ...)` + `from ingest_X import main_async` pattern
    - Use `registry.get_source(name).main_async` to get the entry point dynamically
    - Each APScheduler job calls `registry.get_source(source_name).main_async()` instead of importing directly
  - Add `scheduler/registry_adapter.py` — thin adapter that passes scheduler context (job config, env vars) to the registry-resolved `main_async()`
  - RED: Write `tests/test_scheduler_registry.py` — test that scheduler loads sources from registry, test missing source gives clean error
  - GREEN: Implement adapter and refactor scheduler
  - REFACTOR: Remove all `sys.path` hacks from scheduler

  **Must NOT do**:
  - Do not change any ingestion script's function signature
  - Do not add new scheduling features (queuing, dependencies, retries)
  - Do not remove the backup/newsStartupScript functionality

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Refactoring scheduler with care to preserve all existing cron behavior
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 8, 9, 10, 11, 12)
  - **Blocks**: Task 13
  - **Blocked By**: Task 1 (registry must exist first)

  **References**:

  **Pattern References**:
  - `scheduler/scheduler.py:15` — `sys.path.insert(0, str(Path(__file__).parent / "ingestion"))` — this hack is what we're removing
  - `scheduler/scheduler.py:41-90` — `_run_wikipedia()`, `_run_arxiv()`, etc. — each currently imports its target module directly
  - `ingestion/registry.py` (from Task 1) — The `SourceRegistry` class that replaces direct imports

  **WHY Each Reference Matters**:
  - `scheduler.py:15`: The core problem — `sys.path` manipulation is fragile and makes scheduler coupled to ingestion directory structure
  - `scheduler.py:41-90`: Each `_run_X()` function needs to become a generic `_run_source(source_name)` that resolves via registry

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_scheduler_registry.py
  - [ ] `uv run pytest tests/test_scheduler_registry.py` → PASS (≥4 tests: source resolution, missing source, all 14 sources loadable)

  **QA Scenarios:**

  ```
  Scenario: Scheduler resolves all scheduled sources via registry
    Tool: Bash
    Preconditions: Registry initialized with sources.yaml
    Steps:
      1. Run: python -c "
         from ingestion.registry import SourceRegistry
         r = SourceRegistry()
         scheduled = ['news_api', 'arxiv', 'biorxiv', 'joplin', 'wikipedia_updates', 'forex', 'worldbank']
         for name in scheduled:
             source = r.get_source(name)
             entry = getattr(source, 'main_async', None) or getattr(source, 'main', None)
             assert entry is not None, f'{name} missing entry point'
         print('All scheduled sources resolved')"
      2. Assert output contains "All scheduled sources resolved"
    Expected Result: Registry can resolve every source the scheduler currently uses
    Failure Indicators: AssertionError for any source
    Evidence: .sisyphus/evidence/task-7-scheduler-registry.txt

  Scenario: Missing source gives clean error, not ImportError
    Tool: Bash
    Preconditions: sources.yaml with a source pointing to nonexistent module
    Steps:
      1. Add a bad entry: echo 'nonexistent: {module: fake_module, schedule: "0 0 * * *", conflict: skip, enabled: true}' >> ingestion/sources.yaml
      2. Run: python -c "from ingestion.registry import SourceRegistry; r = SourceRegistry(); print(r.get_source('nonexistent'))"
      3. Assert clear ValueError, not raw ImportError
    Expected Result: Clean validation error with source name in message
    Failure Indicators: Raw ImportError/ModuleNotFoundError
    Evidence: .sisyphus/evidence/task-7-missing-source.txt
  ```

  **Commit**: YES (groups with scheduler changes)
  - Message: `refactor(scheduler): decouple from ingestion via registry`
  - Files: `scheduler/scheduler.py`, `scheduler/registry_adapter.py`, `tests/test_scheduler_registry.py`

- [ ] 8. Migrate Agent Joplin Tools to Unified PG Layer

  **What to do**:
  - Replace `agent/tools/joplin_mcp.py` HTTP calls with direct calls to `joplin_pg.py` functions:
    - Each tool function (`create_note`, `search_notes`, etc.) now calls the equivalent `joplin_pg` function
    - Keep the same LangChain `@tool` decorator signatures so the agent's tool pack definitions don't change
    - Remove `httpx` dependency from Joplin tools (no more HTTP to MCP server)
  - Update `agent/graph_tools.py` `NOTE_TOOLS` (line 138, variable is `NOTE_TOOLS` not "NOTES_PACK") to import from `joplin_pg` instead of `joplin_mcp`
  - Add a deprecation warning to `joplin_mcp.py` — it can be removed in a future cleanup
  - RED: Write `tests/test_joplin_tools_unified.py` — test that each tool calls PG directly (mock the PG layer, verify no httpx calls)
  - GREEN: Implement the migration
  - REFACTOR: Clean up any leftover httpx references

  **Must NOT do**:
  - Do not delete `joplin_mcp.py` yet (add deprecation warning only)
  - Do not change tool names or descriptions (agent's tool selection depends on these)
  - Do not remove the MCP server (it still serves non-agent clients)
  - Do not change `notes.py` (save_note still uses Joplin Server API — different path, addressed in Task 11)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Careful migration of 12 tool functions preserving all contracts
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 9, 10, 11, 12)
  - **Blocks**: Tasks 11, 14
  - **Blocked By**: Task 6 (joplin_pg.py must exist)

  **References**:

  **Pattern References**:
  - `agent/tools/joplin_mcp.py:33-212` — All 12 tool functions with their current HTTP proxy implementation
  - `agent/tools/joplin_pg.py` (from Task 6) — The direct PG implementations to swap in
  - `agent/graph_tools.py:138-153` — `NOTE_TOOLS` definition referencing Joplin tools (variable is `NOTE_TOOLS`, not "NOTES_PACK")

  **WHY Each Reference Matters**:
  - `joplin_mcp.py:34-210`: Each function body changes from `_mcp_call(action, params)` to `joplin_pg.action(params)`, but the `@tool` decorator and docstrings stay the same
  - `graph_tools.py:25-36`: The Joplin tool imports must change from `joplin_mcp` to `joplin_pg`, but tool names remain identical so the agent's tool selection isn't affected

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_joplin_tools_unified.py
  - [ ] `uv run pytest tests/test_joplin_tools_unified.py` → PASS (≥12 tests, one per tool, verifying PG-direct path)

  **QA Scenarios:**

  ```
  Scenario: Joplin tool calls go direct to PG, not HTTP
    Tool: Bash
    Preconditions: joplin_pg module available, Joplin DB accessible
    Steps:
      1. Run: python -c "
         import asyncio
         from tools.joplin_pg import search_notes
         import tools.joplin_mcp
         # Verify no httpx import in joplin tools path
         import importlib, sys
         # Force reload to check new code
         result = asyncio.run(search_notes.ainvoke({'query': 'test'}))
         print(f'Search result type: {type(result).__name__}')"
      2. Assert result is from direct PG, not HTTP response
    Expected Result: Tools use psycopg/asyncpg, not httpx
    Failure Indicators: httpx import or HTTP request in call stack
    Evidence: .sisyphus/evidence/task-8-joplin-pg-direct.txt
  ```

  **Commit**: YES
  - Message: `refactor(agent): migrate Joplin tools to unified PG layer`
  - Files: `agent/tools/joplin_mcp.py` (deprecation), `agent/graph_tools.py`, `tests/test_joplin_tools_unified.py`

- [ ] 9. OpenWebUI Memories Pipeline (Direct PG)

  **What to do**:
  - Create `pipelines/memories_pipeline.py` — An OpenWebUI Pipeline that appears as a **model choice** in OpenWebUI:
    - Follows the OpenWebUI Pipeline pattern from `research_agent.py`: `Pipeline` class with `Valves`, `pipe()` method
    - Appears as a model named "🔍 Memories Search" in OpenWebUI's model selector
    - `pipe()` method: receives user query, queries `agent_memories` table directly from PG, returns formatted results
    - Uses the named pool `"agent_kb"` from Task 2's `db_pool.py`
    - Default filter: returns all memories; user can specify category or minimum importance in their query (e.g., "show me facts" → category="facts")
    - Valves (pipeline config): allow admin to set default `min_importance` and `category` filter via OpenWebUI's Valves UI
    - Falls back to agent's `GET /memories` endpoint if PG is unreachable
  - **ACTUAL `agent_memories` SCHEMA**: `id, category, content, importance, deleted_at, metadata, created_at, updated_at`
    - There is NO `layer`, `user_id`, `embedding`, or `search_vector` column
    - Filter by `category` and `importance` (these exist); CANNOT filter by `layer` (doesn't exist)
  - Register as a separate pipeline file (OpenWebUI loads all .py files in pipelines/)
  - RED: Write `tests/test_memories_pipeline.py` — test pipeline pipe() method, PG query, category filter, fallback
  - GREEN: Implement pipeline
  - REFACTOR: Share PG connection pattern with Task 10

  **Must NOT do**:
  - Do not modify OpenWebUI's source code (only add pipeline file)
  - Do not change agent's `GET /memories` endpoint (`main.py:223-263`)
  - Do not write to `agent_memories` from the pipeline (read-only)
  - Do not add authentication (security deferred)
  - Do not create REST endpoints (pipelines are model choices, not API routes)
  - Do not filter by `layer` (column doesn't exist in agent_memories)

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 10, 11, 12)
  - **Blocks**: Task 14
  - **Blocked By**: Task 2 (needs named pool)

  **References**:

  **Pattern References**:
  - `pipelines/research_agent.py:22-40` — Current Pipeline class structure (Valves, __init__, pipe) — follow this exact pattern
  - `agent/main.py:223-263` — Agent's `GET /memories` endpoint — the response shape to match for compatibility
  - `agent/tools/memory.py:30-46` — `agent_memories` INSERT — shows actual columns: `category, content, importance`

  **API/Type References**:
  - `agent_memories` table (ACTUAL schema): `id, category, content, importance, deleted_at, metadata, created_at, updated_at`
  - OpenWebUI Pipeline API: `pipe(user_message, model_id, messages, body)` — called when user selects this model

  **WHY Each Reference Matters**:
  - `research_agent.py:22-40`: The new pipeline must follow the same class pattern — same Valves pattern, same pipe() signature
  - `main.py:223-263`: The response format should be compatible so OpenWebUI users get the same data
  - `memory.py:30-46`: Confirms actual schema (no `layer`, no `user_id`, no `embedding`)

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_memories_pipeline.py
  - [ ] `uv run pytest tests/test_memories_pipeline.py` → PASS (≥5 tests: pipe returns memories, category filter, importance filter, fallback, empty result)

  **QA Scenarios:**

  ```
  Scenario: Memories pipeline appears as model choice in OpenWebUI
    Tool: Bash (curl)
    Preconditions: Pipeline file in pipelines/ dir, OpenWebUI and pipeline service running
    Steps:
      1. curl -s http://localhost:3000/api/v1/models/ | python -c "import json,sys; data=json.load(sys.stdin); names=[m.get('name','') for m in data.get('data',[])]; print(any('Memories' in n for n in names))"
      2. Assert: True (pipeline appears as model choice)
    Expected Result: Memories Search visible in OpenWebUI model list
    Failure Indicators: Model not listed, connection refused
    Evidence: .sisyphus/evidence/task-9-memories-model.txt

  Scenario: Selecting "Memories Search" returns memories from PG
    Tool: Bash (curl)
    Preconditions: Agent memories exist in PG, pipeline running on :9099
    Steps:
      1. curl -s -X POST http://localhost:3000/api/v1/chat/completions -H 'Content-Type: application/json' -d '{"model": "memories_search", "messages": [{"role": "user", "content": "show me all memories"}]}' | python -m json.tool | head -20
      2. Assert response contains memory entries with category, content, importance fields
    Expected Result: Memories returned directly from PG without hitting agent API
    Failure Indicators: Empty response, connection error to agent (should go to PG)
    Evidence: .sisyphus/evidence/task-9-memories-pipeline.txt

  Scenario: Pipeline falls back when PG is unreachable
    Tool: Bash
    Preconditions: Pipeline running, PG temporarily unreachable
    Steps:
      1. With PG down, send request to memories pipeline
      2. Assert response is still returned (from agent GET /memories fallback) or clear error
    Expected Result: Graceful degradation
    Failure Indicators: Unhandled exception
    Evidence: .sisyphus/evidence/task-9-memories-fallback.txt
  ```

  **Commit**: YES
  - Message: `feat(pipelines): OpenWebUI memories pipeline via direct PG`
  - Files: `pipelines/memories_pipeline.py`, `tests/test_memories_pipeline.py`

- [ ] 10. OpenWebUI KB Search Pipeline (Direct PG)

  **What to do**:
  - Create `pipelines/kb_pipeline.py` — An OpenWebUI Pipeline that appears as a **model choice** in OpenWebUI:
    - Follows the same Pipeline pattern as Task 9's memories pipeline
    - Appears as a model named "📚 KB Search" in OpenWebUI's model selector
    - `pipe()` method: receives user query, embeds it, queries `knowledge_chunks` directly from PG via pgvector
    - Uses the correct embed model per source (leveraging `SOURCE_MODEL_MAP`)
    - Supports filtering by source, limit, similarity threshold
    - Uses the named pool `"agent_kb"` from Task 2
  - RED: Write `tests/test_kb_pipeline.py` — test pipeline pipe(), source filter, embed routing
  - GREEN: Implement pipeline
  - REFACTOR: Share PG connection helper from Task 9

  **Must NOT do**:
  - Do not modify OpenWebUI's source code
  - Do not change the agent's search tools
  - Do not add write capability (read-only)
  - Do not implement RRF in the pipeline (that's the agent's job)
  - Do not create REST endpoints (pipelines are model choices, not API routes)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 11, 12)
  - **Blocks**: Task 14
  - **Blocked By**: Task 2

  **References**:

  **Pattern References**:
  - `pipelines/memories_pipeline.py` (from Task 9) — Same pipeline structure
  - `agent/tools/kb_search.py:35-37` — Model routing + pgvector query pattern
  - `agent/tools/router.py:24-26` — `SOURCE_MODEL_MAP`

  **API/Type References**:
  - `knowledge_chunks` table (ACTUAL schema from `db/init.sql`): `id, source, source_id, chunk_index, content, metadata, embedding, embedding_model, user_id, created_at, updated_at`
  - pgvector operator: `<=>` for cosine distance
  - OpenWebUI Pipeline API: `pipe(user_message, model_id, messages, body)` — called when user selects this model

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_kb_pipeline.py
  - [ ] `uv run pytest tests/test_kb_pipeline.py` → PASS (≥4 tests: pipe returns results, source filter, default model, empty result)

  **QA Scenarios:**

  ```
  Scenario: KB Search pipeline appears as model choice
    Tool: Bash (curl)
    Preconditions: Pipeline file in pipelines/, services running
    Steps:
      1. curl -s http://localhost:3000/api/v1/models/ | python -c "import json,sys; data=json.load(sys.stdin); names=[m.get('name','') for m in data.get('data',[])]; print(any('KB Search' in n for n in names))"
      2. Assert: True
    Expected Result: "KB Search" in model list
    Failure Indicators: Model not listed
    Evidence: .sisyphus/evidence/task-10-kb-model.txt

  Scenario: Selecting "KB Search" returns relevant chunks
    Tool: Bash (curl)
    Preconditions: knowledge_chunks has data
    Steps:
      1. curl -s -X POST http://localhost:3000/api/v1/chat/completions -H 'Content-Type: application/json' -d '{"model": "kb_search", "messages": [{"role": "user", "content": "machine learning"}]}' | python -m json.tool | head -20
      2. Assert response contains knowledge chunk results
    Expected Result: Relevant chunks via direct pgvector query
    Failure Indicators: Embedding error, wrong model, empty results
    Evidence: .sisyphus/evidence/task-10-kb-pipeline.txt
  ```

  **Commit**: YES
  - Message: `feat(pipelines): OpenWebUI KB search pipeline via direct PG`
  - Files: `pipelines/kb_pipeline.py`, `tests/test_kb_pipeline.py`

- [ ] 11. Pipeline Joplin Enrichment Dedup

  **What to do**:
  - Remove `_enrich_with_joplin()` from `pipelines/research_agent.py`:
    - The agent already has Joplin note tools (now unified via PG in Task 8)
    - The pipeline's Joplin enrichment was duplicating what the agent already does
    - Delete the `_enrich_with_joplin` method and its call in the `pipe()` flow
  - Remove the `joplin://` URL detection logic from the pipeline
  - If Joplin content is needed in the pipeline, the agent's tool calls already fetch it
  - RED: Write `tests/test_pipeline_joplin.py` — test that pipeline no longer calls Joplin, verify output still has agent's Joplin-fetched content
  - GREEN: Remove the enrichment and its tests
  - REFACTOR: Clean up unused imports (httpx for Joplin fetch, any Joplin URL parsing)

  **Must NOT do**:
  - Do not remove the auto-save session feature from the pipeline
  - Do not remove the SSE streaming logic
  - Do not change how the pipeline calls the agent /chat endpoint

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward deletion of redundant code
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 12)
  - **Blocks**: None
  - **Blocked By**: Task 8 (confirm Joplin tools work via PG before removing pipeline enrichment)

  **References**:

  **Pattern References**:
  - `pipelines/research_agent.py:67-77` — `_enrich_with_joplin()` method — the method to remove
  - `pipelines/research_agent.py:160-168` — Inline Joplin enrichment in streaming path — also remove
  - `pipelines/research_agent.py:221` — Call to `_enrich_with_joplin` in non-streaming `pipe()` — remove

  **WHY Each Reference Matters**:
  - `research_agent.py:150-180`: The exact code to delete — removes the double-enrichment where both pipeline and agent fetch Joplin content

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_pipeline_joplin.py
  - [ ] `uv run pytest tests/test_pipeline_joplin.py` → PASS (≥2 tests: no joplin enrichment call, pipeline still streams agent response)

  **QA Scenarios:**

  ```
  Scenario: Pipeline does not call Joplin enrichment
    Tool: Bash
    Preconditions: Pipeline running
    Steps:
      1. grep -n "enrich_with_joplin" pipelines/research_agent.py
      2. Assert: no matches found
    Expected Result: Method completely removed from pipeline
    Failure Indicators: Method still exists (even if commented out)
    Evidence: .sisyphus/evidence/task-11-joplin-dedup.txt

  Scenario: Pipeline still produces agent responses after Joplin removal
    Tool: Bash (curl)
    Preconditions: Agent and pipeline running
    Steps:
      1. curl -s -X POST http://localhost:9099/api/v1/chat/completions -H 'Content-Type: application/json' -d '{"model": "parsnip", "messages": [{"role": "user", "content": "hello"}]}' | head -5
      2. Assert: SSE stream with agent response content
    Expected Result: Pipeline still works — only the Joplin enrichment is removed
    Failure Indicators: Broken SSE stream, 500 error
    Evidence: .sisyphus/evidence/task-11-pipeline-still-works.txt
  ```

  **Commit**: YES
  - Message: `fix(pipelines): deduplicate Joplin enrichment in pipeline`
  - Files: `pipelines/research_agent.py`, `tests/test_pipeline_joplin.py`

- [ ] 12. Memory Tools Use Shared Connection Pool

  **What to do**:
  - Refactor `agent/tools/memory.py` to use the shared pool from `db_pool.py`:
    - Replace every `async with await psycopg.AsyncConnection.connect(db_url) as conn:` with `async with get_pool().connection() as conn:`
    - Remove the per-call connection creation overhead
    - Remove the `db_url` parameter construction at each call site
  - RED: Write `tests/test_memory_pool.py` — test memory tools use pool (mock pool, verify .connection() called, not .connect())
  - GREEN: Refactor memory.py to use pool
  - REFACTOR: Remove unused `db_url`/`DATABASE_URL` construction from memory.py

  **Must NOT do**:
  - Do not change memory tool API (input/output contracts unchanged)
  - Do not add new memory tools
  - Do not change the `agent_memories` table schema

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Search-and-replace refactor with clear pattern
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 9, 10, 11)
  - **Blocks**: None
  - **Blocked By**: Task 2 (pool must exist)

  **References**:

  **Pattern References**:
  - `agent/tools/memory.py:34-36` — Current fresh-connection pattern: `async with await psycopg.AsyncConnection.connect(db_url) as conn:`
  - `agent/tools/db_pool.py` (from Task 2) — New pattern: `async with get_pool().connection() as conn:`

  **WHY Each Reference Matters**:
  - `memory.py:34-36`: This pattern appears ~6 times in the file — each becomes a pool.connection() call

  **Acceptance Criteria**:

  **If TDD**:
  - [ ] Test file created: tests/test_memory_pool.py
  - [ ] `uv run pytest tests/test_memory_pool.py` → PASS (≥6 tests, one per memory tool, verifying pool usage)

  **QA Scenarios:**

  ```
  Scenario: Memory tools use pool, not fresh connections
    Tool: Bash
    Preconditions: Pool initialized, PostgreSQL accessible
    Steps:
      1. Run: python -c "
         import asyncio
         from tools.db_pool import init_pool, get_pool
         from tools.memory import save_memory
         async def test():
             await init_pool()
             pool = get_pool()
             initial_size = pool.size
             result = await save_memory.ainvoke({'content': 'pool test', 'category': 'test'})
             assert pool.size == initial_size, 'Pool should not grow'
             print(f'Pool stable: size={pool.size}')"
      2. Assert pool size doesn't grow (connections are returned, not created)
    Expected Result: Pool reuses connections instead of creating new ones per call
    Failure Indicators: Pool size growing, or psycopg connect calls instead of pool
    Evidence: .sisyphus/evidence/task-12-memory-pool.txt
  ```

  **Commit**: YES
  - Message: `refactor(memory): use shared connection pool`
  - Files: `agent/tools/memory.py`, `tests/test_memory_pool.py`

---

## Wave 3 Tasks (Validation + Documentation)

- [ ] 13. Integration Test Suite for Plugin Registry

  **What to do**:
  - Create `tests/integration/test_registry_integration.py` — end-to-end tests:
    - Registry initializes from sources.yaml
    - Each source's `main_async()` is importable and callable (with mock DB)
    - Scheduler can resolve and call every source through the registry
    - Adding a new source to sources.yaml makes it immediately available
    - Removing a source from sources.yaml cleanly prevents scheduling
  - Use real YAML parsing but mock DB connections
  - RED: Write failing integration test for "add new source, scheduler picks it up"
  - GREEN: Ensure registry + scheduler adapter support the integration scenario
  - REFACTOR: Extract test fixture for source YAML generation

  **Must NOT do**:
  - Do not connect to real PG in tests (use mocks)
  - Do not test ingestion logic itself (only registry plumbing)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration tests require understanding registry + scheduler interaction
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 14, 15, 16)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 1, 7

  **References**:
  - `tests/test_registry.py` (from Task 1) — Unit tests to build on
  - `tests/test_scheduler_registry.py` (from Task 7) — Scheduler integration tests to build on

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/integration/test_registry_integration.py` → PASS (≥5 tests)

  **QA Scenarios:**

  ```
  Scenario: End-to-end: new source appears in scheduler
    Tool: Bash
    Preconditions: Registry and scheduler adapter code complete
    Steps:
      1. Add a test source to sources.yaml: echo 'test_source: {module: ingest_test, schedule: "0 0 * * *", conflict: skip, enabled: true}' >> /tmp/test_sources.yaml
      2. Create a dummy ingest_test.py with main_async()
      3. Run: python -c "from ingestion.registry import SourceRegistry; r = SourceRegistry('/tmp/test_sources.yaml'); print(r.get_source('test_source').module)"
      4. Assert: 'ingest_test' returned
    Expected Result: New source is immediately resolvable
    Failure Indicators: Source not found, or stale registry state
    Evidence: .sisyphus/evidence/task-13-registry-e2e.txt
  ```

  **Commit**: YES
  - Message: `test(ingestion): integration test suite for plugin registry`
  - Files: `tests/integration/test_registry_integration.py`

- [ ] 14. Integration Test Suite for Joplin + OpenWebUI Pipes

  **What to do**:
  - Create `tests/integration/test_pipes_integration.py` — end-to-end tests:
    - Joplin PG tools create/read/search notes (against test Joplin DB)
    - OpenWebUI memories middleware reads from PG (against test PG)
    - OpenWebUI KB middleware searches chunks (against test PG)
    - Pipeline streams agent response without Joplin enrichment dedup
    - Cross-service: memories saved by agent are visible via pipeline middleware
  - Use test fixtures with sample data in test PG DB
  - RED: Write test for "agent saves memory → pipeline reads it"
  - GREEN: Ensure the data flow works end-to-end
  - REFACTOR: Shared test DB fixture

  **Must NOT do**:
  - Do not test against production data
  - Do not test MCP server (it's not modified, just deprecated)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multi-service integration tests require careful fixture setup
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 15, 16)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 8, 9, 10

  **References**:
  - `tests/test_joplin_pg.py` (from Task 6), `tests/test_joplin_tools_unified.py` (from Task 8)
  - `tests/test_memories_pipeline.py` (from Task 9), `tests/test_kb_pipeline.py` (from Task 10)

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/integration/test_pipes_integration.py` → PASS (≥4 tests)

  **QA Scenarios:**

  ```
  Scenario: Agent saves memory, pipeline reads it
    Tool: Bash
    Preconditions: Test DB with agent_memories table, both agent and pipeline running
    Steps:
      1. Insert a test memory directly: psql "$DATABASE_URL" -c "INSERT INTO agent_memories (category, content, importance) VALUES ('test', 'pipe test memory', 3)"
      2. Query via pipeline: curl -s -X POST http://localhost:3000/api/v1/chat/completions -H 'Content-Type: application/json' -d '{"model": "memories_search", "messages": [{"role": "user", "content": "pipe test memory"}]}' | python -m json.tool | head -20
      3. Assert response contains the inserted memory content
    Expected Result: Memory saved in PG is visible via pipeline model query
    Failure Indicators: Memory not found, or pipeline errors
    Evidence: .sisyphus/evidence/task-14-pipe-integration.txt
  ```

  **Commit**: YES
  - Message: `test(integration): Joplin + OpenWebUI pipes integration test suite`
  - Files: `tests/integration/test_pipes_integration.py`

- [ ] 15. Update ARCHITECTURE.md + Ingestion README

  **What to do**:
  - Update `ARCHITECTURE.md` to reflect:
    - Plugin registry architecture (sources.yaml, auto-discovery)
    - Unified Joplin access pattern (direct PG vs deprecated MCP chain)
    - OpenWebUI PG-pipe middleware diagram
    - Connection pool architecture
    - Circuit breaker file-based state
    - Updated Mermaid diagram showing the new paths
  - Update `ingestion/README.md` to document:
    - How to add a new ingestion source (3 steps: script → sources.yaml → optionally ROUTING_CONFIG)
    - sources.yaml schema reference
    - Conflict strategy options
    - Embed model assignment per source

  **Must NOT do**:
  - Do not add new docs files (only update existing)
  - Do not document internal implementation details (user-facing API only)
  - Do not include security recommendations (deferred)

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Documentation task requiring clear technical writing
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 16)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 1, 6, 8, 9

  **References**:
  - `ARCHITECTURE.md` (122 lines) — Current architecture doc to update
  - `ingestion/README.md` — Current ingestion docs to update
  - `docs/ARCHITECTURE_VISUALS.md`, `docs/CONFIGURATION.md`, `docs/DEPLOYMENT.md` — References for consistency

  **Acceptance Criteria**:
  - [ ] ARCHITECTURE.md contains Mermaid diagram with new Joplin PG path and pipeline middleware
  - [ ] ingestion/README.md contains "Adding a new source" section with 3-step process

  **QA Scenarios:**

  ```
  Scenario: ARCHITECTURE.md reflects new architecture
    Tool: Bash
    Preconditions: Docs updated
    Steps:
      1. grep -c "plugin registry" ARCHITECTURE.md
      2. grep -c "joplin_pg" ARCHITECTURE.md
      3. grep -c "memories_pipeline" ARCHITECTURE.md
    Expected Result: All counts > 0 (new concepts documented)
    Failure Indicators: Any count = 0
    Evidence: .sisyphus/evidence/task-15-arch-doc.txt
  ```

  **Commit**: YES
  - Message: `docs: update architecture and ingestion docs for new patterns`
  - Files: `ARCHITECTURE.md`, `ingestion/README.md`

- [ ] 16. ROUTING_CONFIG Docs + Pattern Checklist

  **What to do**:
  - Create `docs/ROUTING.md` (or update existing routing docs) documenting:
    - `ROUTING_CONFIG` structure and how to modify complexity weights
    - `SOURCE_MODEL_MAP` — which sources use which embed model, and WHY
    - Pattern checklist for adding new sources: (1) choose embed model, (2) add to SOURCE_MODEL_MAP, (3) verify existing chunks aren't affected
    - How `detect_intent()` maps to `intent_layers` → source ordering
    - The embed model mismatch bug (now fixed) and how to avoid it
  - Make it a reference doc for future source additions

  **Must NOT do**:
  - Do not change routing code (only document it)
  - Do not add new routing features

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Technical reference documentation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14, 15)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 1, 3

  **References**:
  - `agent/tools/router.py:1-60` — ROUTING_CONFIG and SOURCE_MODEL_MAP definitions
  - `agent/tools/holistic_search.py` — Where the fix from Task 3 was applied

  **Acceptance Criteria**:
  - [ ] ROUTING.md documents SOURCE_MODEL_MAP, intent_layers, complexity thresholds
  - [ ] ROUTING.md includes pattern checklist for adding new sources

  **QA Scenarios:**

  ```
  Scenario: ROUTING.md has complete source model map
    Tool: Bash
    Preconditions: Doc created
    Steps:
      1. grep -c "bge-m3" docs/ROUTING.md
      2. grep -c "mxbai-embed-large" docs/ROUTING.md
      3. grep -c "pattern checklist" docs/ROUTING.md
    Expected Result: All counts > 0
    Failure Indicators: Missing model documentation or checklist
    Evidence: .sisyphus/evidence/task-16-routing-doc.txt
  ```

  **Commit**: YES
  - Message: `docs: add routing configuration reference and pattern checklist`
  - Files: `docs/ROUTING.md`

---

## Wave 0: Decision Spike

- [ ] 17. Frontend Decision Spike: OpenWebUI vs assistant-ui

  **What to do**:
  - Evaluate whether to keep OpenWebUI or replace it with assistant-ui (or another slim TS/React frontend):
  - Create a decision document evaluating:
    - **Keep OpenWebUI**: Pipeline middleware approach (Tasks 9/10), Python-only extensibility, Svelte frontend hard to customize, heavy stack (Redis, 9 vector DBs, built-in RAG redundant with Parsnip's own)
    - **Replace with assistant-ui**: React component library, `makeAssistantToolUI` for custom per-tool rendering, `useChat` pointing at agent API, ultra-light (just components, no backend), but requires building a Next.js shell
    - **Keep both**: OpenWebUI for general chat, assistant-ui for power-user tool-centric interface
    - **Hybrid**: assistant-ui frontend + OpenWebUI auth/user management
  - Criteria: tool-call UI richness, TS/React customizability, ops burden, migration effort, backwards compatibility
  - Deliver a GO/NO-GO recommendation with specific frontend path
  - This task is a SPIKE — no code changes, just a decision document

  **Must NOT do**:
  - Do not implement any frontend code in this task
  - Do not remove OpenWebUI in this task
  - Do not commit to a path until user approves

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Research + decision doc, no implementation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (but should run FIRST to gate Tasks 9/10 vs 18/19)
  - **Parallel Group**: Wave 0
  - **Blocks**: Tasks 9, 10, 18, 19
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `pipelines/research_agent.py` — Current OpenWebUI pipeline integration
  - `docker-compose.yml` — OpenWebUI service definition (lines for openwebui + pipelines)

  **External References**:
  - assistant-ui: https://github.com/assistant-ui/assistant-ui (9.6k★, MIT, TS/React)
  - LibreChat: https://github.com/danny-avila/LibreChat (35.9k★, MIT, TS/React+Express)
  - Big-AGI: https://github.com/enricoros/big-AGI (6.9k★, MIT, Next.js/TS)
  - Libre WebUI: https://github.com/libre-webui/libre-webui (42★, Apache 2.0, React/Vite)

  **Acceptance Criteria**:
  - [ ] Decision document saved to `.sisyphus/evidence/task-17-frontend-decision.md`
  - [ ] Document contains: comparison table, pros/cons for each option, recommendation
  - [ ] User approves one path before Wave 2 frontend tasks start

  **QA Scenarios:**

  ```
  Scenario: Decision document exists with clear recommendation
    Tool: Bash
    Preconditions: Spike complete
    Steps:
      1. test -f .sisyphus/evidence/task-17-frontend-decision.md && echo "EXISTS" || echo "MISSING"
    Expected Result: EXISTS
    Failure Indicators: MISSING
    Evidence: .sisyphus/evidence/task-17-frontend-decision.txt
  ```

  **Commit**: NO (spike, no code changes)

---

## If Replacing OpenWebUI: assistant-ui Tasks

- [ ] 18. assistant-ui Setup + Agent API Compatibility

  **What to do** (ONLY if Task 17 recommends replacing OpenWebUI):
  - Scaffold a Next.js app with assistant-ui components:
    - `npx create-next-app@latest frontend --typescript --tailwind --app`
    - `npx assistant-ui init` in the new project
    - Point `useChat` at Parsnip agent's `/chat` SSE endpoint
    - Add OpenAI-compatible API adapter if needed (agent currently uses custom SSE format — may need a thin `/v1/chat/completions` endpoint on the agent that wraps the existing `/chat`)
  - Docker-ify the frontend (add to docker-compose.yml as new service, or serve static build via Caddy/Nginx)
  - Basic chat interface working: send message → see streaming response
  - RED: Test that chat sends to agent and receives SSE stream
  - GREEN: Implement the setup

  **Must NOT do**:
  - Do not remove OpenWebUI from docker-compose yet (parallel operation)
  - Do not implement custom tool UIs yet (that's Task 19)
  - Do not add auth/session management yet

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: New project scaffolding + API compatibility layer
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Wave 2 tasks, after Task 17)
  - **Parallel Group**: Wave 2 (alternative to Tasks 9/10)
  - **Blocks**: Task 19, Task 14
  - **Blocked By**: Task 17 (decision must say "replace")

  **References**:
  - assistant-ui docs: `makeAssistantToolUI`, `useChat`, runtime configuration
  - `agent/main.py` — Current `/chat` SSE endpoint (may need `/v1/chat/completions` adapter)

  **Acceptance Criteria**:
  - [ ] `frontend/` directory with working Next.js + assistant-ui setup
  - [ ] Chat sends to agent API and receives streaming responses
  - [ ] Docker compose can start the frontend

  **Commit**: YES
  - Message: `feat(frontend): scaffold assistant-ui chat interface`
  - Files: `frontend/` (new directory), `docker-compose.yml` (add frontend service)

- [ ] 19. assistant-ui Custom Tool UI Components

  **What to do** (ONLY if Task 17 recommends replacing OpenWebUI):
  - Create `makeAssistantToolUI` components for each Parsnip tool category:
    - **Search tools**: `kb_search`, `holistic_search`, `research` → expandable result cards with source, similarity score, content preview
    - **Joplin tools**: `joplin_search_notes`, `joplin_get_note` → Joplin-styled note cards with edit-in-Joplin deep link
    - **Memory tools**: `save_memory`, `recall_memories` → memory chips with category/importance badges
    - **Analysis tools**: `execute_python_script`, `execute_r_script` → code blocks with syntax highlighting + output panels
    - **Workspace tools**: `list_files`, `read_file` → file tree + syntax-highlighted content viewer
  - Each tool UI shows: loading state (skeleton/spinner), success state (formatted result), error state (error message + retry)
  - HITL approval UI for memory modifications and file writes (human approves before execution)
  - RED: Test each tool UI renders correctly with mock tool call data
  - GREEN: Implement all tool UI components

  **Must NOT do**:
  - Do not change agent tool APIs (tool names and response formats stay the same)
  - Do not implement backend logic (agent handles all tool execution)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multiple React components with careful UX design
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 18)
  - **Parallel Group**: Wave 2 (alternative to Task 10)
  - **Blocks**: Task 14
  - **Blocked By**: Task 17 (decision), Task 18 (setup must exist)

  **References**:
  - assistant-ui `makeAssistantToolUI` API
  - `agent/graph_tools.py:138-153` — NOTE_TOOLS list (tool names to match)
  - `agent/graph_tools.py:85-105` — RESEARCH_TOOLS list
  - `agent/graph_tools.py:108-125` — ANALYSIS_TOOLS list

  **Acceptance Criteria**:
  - [ ] Each tool category has a custom React component
  - [ ] Tool calls render with loading → success → error states
  - [ ] HITL approval buttons work for memory/file modifications

  **Commit**: YES
  - Message: `feat(frontend): custom tool UI components for assistant-ui`
  - Files: `frontend/components/tools/` (new directory with tool UI components)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `ruff check` + `mypy` (or `pyright`) + `uv run pytest`. Review all changed files for: empty catches, print/logging in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Lint [PASS/FAIL] | Type Check [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill for OpenWebUI)
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec. Check "Must NOT do" compliance. Detect cross-task contamination.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 0**: Task 17 is a spike — no commit (decision document only)
- **Wave 1 Combined**: `refactor(ingestion): add plugin registry and sources.yaml config` — ingestion/registry.py, ingestion/sources.yaml, tests/test_registry.py
- **Task 2**: `refactor(agent): multi-DSN connection pool for tool DB access` — agent/tools/db_pool.py, tests/test_db_pool.py
- **Task 3**: `test(search): add embed model routing regression tests` — tests/test_embed_routing.py
- **Task 4**: `fix(guardrails): process-safe circuit breaker with file-based state` — agent/graph_guardrails.py, tests/test_circuit_breaker.py
- **Task 5**: `fix(scheduler): auto-recover stuck ingestion jobs on timeout` — scheduler/scheduler.py, tests/test_stuck_jobs.py
- **Task 6**: `refactor(joplin): unified PG-direct access layer with HITL workflow` — agent/tools/joplin_pg.py, agent/tools/joplin_hitl.py, tests/test_joplin_pg.py, tests/test_joplin_hitl.py
- **Task 7**: `refactor(scheduler): decouple from ingestion via registry` — scheduler/scheduler.py, tests/test_scheduler_registry.py
- **Task 8**: `refactor(agent): migrate Joplin tools to unified PG layer` — agent/tools/joplin_mcp.py, agent/graph_tools.py, tests/test_joplin_tools_unified.py
- **Task 9** (if keeping OpenWebUI): `feat(pipelines): OpenWebUI memories pipeline via direct PG` — pipelines/memories_pipeline.py, tests/test_memories_pipeline.py
- **Task 10** (if keeping OpenWebUI): `feat(pipelines): OpenWebUI KB search pipeline via direct PG` — pipelines/kb_pipeline.py, tests/test_kb_pipeline.py
- **Task 11**: `fix(pipelines): deduplicate Joplin enrichment in pipeline` — pipelines/research_agent.py, tests/test_pipeline_joplin.py
- **Task 12**: `refactor(memory): use shared connection pool` — agent/tools/memory.py, tests/test_memory_pool.py
- **Task 18** (if replacing OpenWebUI): `feat(frontend): scaffold assistant-ui chat interface` — frontend/ (new), docker-compose.yml
- **Task 19** (if replacing OpenWebUI): `feat(frontend): custom tool UI components for assistant-ui` — frontend/components/tools/ (new)
- **Wave 3 Combined**: `docs: update architecture and routing docs` — ARCHITECTURE.md, ingestion/README.md

---

## Success Criteria

### Verification Commands
```bash
uv run pytest tests/ -v                    # Expected: ALL PASS
uv run pytest tests/test_registry.py -v    # Expected: registry discovers all 14 sources
uv run pytest tests/test_joplin_pg.py -v   # Expected: direct PG access works
uv run pytest tests/test_holistic_embed_routing.py  # Expected: GitHub queries use bge-m3
curl -s http://localhost:8000/health        # Expected: {"status": "ok", ...}
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Adding a new source requires only script + sources.yaml entry (+ optionally ROUTING_CONFIG for embed model)
- [ ] Joplin tools use direct PG, not HTTP→MCP chain
- [ ] Joplin HITL note workflow: generate→detect edits→review→publish cycle works
- [ ] Frontend: EITHER OpenWebUI pipelines working OR assistant-ui chat UI with custom tool rendering
- [ ] Circuit breaker state survives agent restarts
- [ ] Stuck ingestion jobs auto-recover