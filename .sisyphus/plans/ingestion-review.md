# Data Ingestion Pipeline & Schema Review

> **Status**: ✅ COMPLETE — All 19 tasks executed, 4 verification gates passed  
> **Completed**: 2026-04-25  
> **Scope**: Review all 14 ingestion pipelines for efficiency, schema alignment, and best practice adherence  

---

## Executive Summary

This plan outlines a comprehensive review of Parsnip's 14 ingestion pipelines against modern data engineering best practices (2024–2026). The review covers three dimensions:

1. **Pipeline Efficiency** — Async patterns, batching, parallelism, rate limiting, error handling
2. **Schema Alignment** — Table designs, index strategy, normalization vs denormalization, drift detection
3. **Best Practice Compliance** — ELT architecture, observability, configuration-driven design, resilience patterns

**Key findings from pre-review research**:
- 14 ingestion scripts with **inconsistent patterns** (some use landing zone, some don't)
- **Mixed upsert strategies** (row-by-row vs bulk) without clear rationale
- **No circuit breaker** on embedding service (Ollama) — 3 retries × 120s each = 6min to fail
- **No Dead Letter Queue** — failures are logged but not quarantined for replay
- **No formal schema registry** — metadata JSONB is schema-on-read with no evolution tracking
- **Limited test coverage** — no unit tests for individual ingestion scripts
- **Raw psycopg** throughout — no ORM, no migration framework (Alembic)
- **Mixed chunking logic** — 4 different chunk implementations duplicating `utils.chunk_text()`

---

## Dimension 1: Pipeline Architecture & Efficiency

### P1: Audit Async/Sync Patterns
**What**: Review every ingestion script for async usage vs blocking I/O

**Scope**: All `ingestion/ingest_*.py` files

**Checklist**:
- [ ] Inventory which scripts use `asyncio` vs synchronous `requests`/`httpx`
- [ ] Identify scripts making blocking calls inside async loops
- [ ] Check for `asyncio.Semaphore` usage vs unbounded concurrency
- [ ] Verify `aiohttp`/`httpx` async clients vs sync `requests`
- [ ] Check if `psycopg[binary]` async is used consistently across all scripts

**Evidence required**:
- Grep for `async def`, `requests.get`, `httpx.get`, `asyncio.gather`, `Semaphore`
- Report: which scripts are fully async, which are sync, which are mixed

**Deliverable**:
```markdown
| Script | Async | Blocking I/O | Semaphore | Concurrency Limit |
|--------|-------|-------------|-----------|-----------------|
| ingest_arxiv.py | ✅ | None | ❌ (unbounded) | None |
```

---

### P2: Audit Batching & Chunking Strategies
**What**: Review batch sizes, chunk sizes, and overlap across all sources

**Scope**: `ingestion/utils.py`, all `ingest_*.py`

**Checklist**:
- [ ] Document chunk sizes (words), overlap (words), and strategy per source
- [ ] Check if `bulk_upsert_chunks()` vs `upsert_chunks()` is used consistently
- [ ] Verify batch sizes for `embed_batch()` (most use 32, some use 64/100)
- [ ] Check for memory pressure issues with large batches (Wikipedia 6.7M articles)
- [ ] Identify duplicate `_chunk_text()` implementations (Joplin, PDF have their own)

**Key concern**: `bulk_upsert_chunks()` uses single transaction `executemany()` — for Wikipedia scale, a failure midway rolls back everything processed.

**Deliverable**:
```markdown
| Source | Chunk Words | Overlap | Embed Batch | Upsert Mode | Risk |
|--------|-------------|---------|-------------|-------------|------|
| arxiv | 200 | 40 | 32 | row-by-row | Slow but safe |
| wikipedia | 200 | 40 | 64 | bulk (single tx) | OOM + rollback risk |
```

---

### P3: Landing Zone Pattern Adoption
**What**: Check which pipelines properly implement the 2-phase fetch→save_raw→process pattern

**Scope**: All `ingest_*.py` + `utils.py`

**Checklist**:
- [ ] List scripts that use `save_raw()` + `iter_raw()` (landing zone)
- [ ] List scripts that skip the landing zone (direct API→DB)
- [ ] Identify scripts that don't support `--from-raw` replay
- [ ] Check raw data retention policy — files accumulate indefinitely in `data/raw/`
- [ ] Verify raw data compression (all use `jsonl.gz` — good)

**Current state**:
- ✅ Landing zone: arxiv, biorxiv, news_api, forex, github, hackernews, pubmed, rss, ssrn, worldbank
- ❌ No landing zone: joplin, news (RSS fallback), wikipedia_updates
- ⚠️ wikipedia uses local file (not API) so landing zone doesn't apply

**Deliverable**: Gap analysis with recommendations for each non-compliant source

---

### P4: Rate Limiting & Retry Strategy Review
**What**: Evaluate resilience against external API failures

**Scope**: All API-calling scripts + `utils.py`

**Checklist**:
- [ ] Document rate limit implementations per source (delay seconds, semaphore counts)
- [ ] Check for exponential backoff with jitter (not just fixed delays)
- [ ] Verify `Retry-After` header handling for 429 responses
- [ ] Check for circuit breaker pattern (none currently)
- [ ] Verify retry budgets (max retries per source per time window)
- [ ] Check for thundering herd prevention on scheduler restart

**Current state**:
- arxiv: 3s fixed delay between calls
- bioRxiv: 0.5s between pages
- GitHub: built-in retry with `X-RateLimit-Reset`
- NewsAPI: `asyncio.Semaphore(2)`
- Others: mostly unbounded

**Key concern**: `embed_batch()` retries 3× with exponential backoff but no circuit breaker. If Ollama is down, each batch waits 120s × 3 = 6 minutes before failing.

**Deliverable**: Resilience matrix with recommendations for circuit breaker, DLQ, and retry improvements

---

### P5: Parallelism & Concurrency Review
**What**: Assess how pipelines exploit parallelism and identify serialization bottlenecks

**Scope**: `scheduler/scheduler.py`, `run_serial_ingestion.py`, all async scripts

**Checklist**:
- [ ] Check if sources run concurrently in scheduler or sequentially
- [ ] Verify `run_serial_ingestion.py` uses `subprocess.run()` (blocks)
- [ ] Check if multiple sources can be processed simultaneously
- [ ] Identify CPU-bound bottlenecks (PDF parsing, chunking) that need process pools
- [ ] Check for database connection pool contention

**Current state**:
- Scheduler: APScheduler runs ONE job at a time per source (good isolation)
- `run_serial_ingestion.py`: runs ALL sources sequentially via blocking subprocess
- No process pool for CPU-bound work (GitHub parsing, PDF text extraction)
- DB connections: Each script creates its own `psycopg` connection (no pool sharing)

**Deliverable**: Parallelism opportunity map + recommendations for `ProcessPoolExecutor`, connection pooling

---

## Dimension 2: Schema & Data Model Alignment

### S1: Table Structure Review
**What**: Document all tables, columns, types, indices, and their purpose

**Scope**: `db/init.sql`, `db/migrations/`, `db/checkpoint_migrations/`

**Checklist**:
- [ ] Inventory all 13+ tables with full column specs
- [ ] Verify primary keys and unique constraints are correct
- [ ] Check for missing indices on frequently queried columns
- [ ] Verify `VECTOR(1024)` is appropriate for all embedding models (mxbai + bge-m3)
- [ ] Check for unused columns (`user_id` in `knowledge_chunks` is NULL for all non-Joplin)
- [ ] Verify `JSONB` `metadata` structure per source (is it consistent?)

**Deliverable**: Complete schema map with column usage analysis

---

### S2: Index Strategy Review
**What**: Evaluate query patterns vs index coverage

**Scope**: `db/init.sql`, agent query code (`kb_search.py`, `holistic_search.py`)

**Checklist**:
- [ ] List all indices and their purpose
- [ ] Check if `diskann` index is optimal for 20M+ vectors (vs HNSW)
- [ ] Verify hybrid search uses FTS index (`to_tsvector('english', content)`)
- [ ] Check for missing composite indices on common filter patterns
- [ ] Verify partial index on `user_id` is actually used by queries
- [ ] Check if `ingestion_jobs` indices exist for scheduler lookups

**Current indices**:
```sql
-- knowledge_chunks
diskann (embedding)          -- vector search
GIN (to_tsvector)            -- full-text
(source, source_id)           -- metadata filter
user_id WHERE NOT NULL       -- partial (Joplin)

-- forex_rates
(pair, rate_date) UNIQUE    -- dedup
(rate_date)                  -- date range queries

-- agent_memories
(category)                    -- category filter
(importance DESC)             -- ordering
GIN (to_tsvector)             -- memory search
```

**Deliverable**: Index optimization recommendations with query pattern analysis

---

### S3: Schema Drift Detection
**What**: Identify mismatches between ingestion output and table schema

**Scope**: All `ingest_*.py` + `db/init.sql`

**Checklist**:
- [ ] List all fields written by each ingestion script
- [ ] Cross-reference with `knowledge_chunks` columns
- [ ] Identify fields written but not in schema (will fail silently or be lost)
- [ ] Identify schema columns never populated (e.g., `user_id` for non-Joplin)
- [ ] Check `metadata` JSONB structure consistency across sources
- [ ] Verify `embedding_model` is set correctly per source (GitHub should be 'bge-m3')

**Known drift from research**:
1. `user_id` column unused for 13/14 sources
2. `joplin_hitl_sessions` (legacy) vs `hitl_sessions` (new) — different column types
3. `source_id` migration — legacy data uses `::chunk_index` suffix in `source_id`; new schema has separate `chunk_index`
4. Soft-deleted notes remain in `knowledge_chunks` (not cleaned up)

**Deliverable**: Schema drift report with severity ratings

---

### S4: Normalization vs Denormalization Analysis
**What**: Evaluate the intentional denormalization in `knowledge_chunks` vs normalized tables

**Scope**: `knowledge_chunks` + `forex_rates` + `world_bank_data` + Joplin tables

**Checklist**:
- [ ] Document what's stored in `knowledge_chunks` vs separate tables
- [ ] Verify dual-write consistency (forex → `forex_rates` + KB chunks)
- [ ] Check if `metadata` JSONB contains normalized data that should be columns
- [ ] Evaluate Joplin data storage — duplicated between Joplin Server DB and `knowledge_chunks`
- [ ] Check if `notes` table (Joplin) is queried independently of `knowledge_chunks`

**Deliverable**: Normalization analysis with recommendations (e.g., add `source` lookup table, extract common metadata fields)

---

### S5: Migration & Schema Evolution Strategy
**What**: Assess how schema changes are managed

**Scope**: `db/`, `db/migrations/`

**Checklist**:
- [ ] Check for formal migration framework (Alembic, Flyway, etc.) — NONE found
- [ ] Review manual migration scripts in `db/migrations/`
- [ ] Verify `IF NOT EXISTS` idempotency of `init.sql`
- [ ] Check for schema versioning in code or database
- [ ] Identify how schema changes are deployed (manual? automated?)
- [ ] Check LangGraph checkpoint table auto-creation

**Current state**: Manual SQL scripts with `IF NOT EXISTS`. No version tracking. No rollback capability.

**Deliverable**: Migration maturity assessment + roadmap to Alembic or similar

---

## Dimension 3: Best Practice Compliance

### B1: ELT vs ETL Architecture Review
**What**: Evaluate if the current architecture follows ELT best practices

**Scope**: Entire ingestion directory + storage layer

**Checklist**:
- [ ] Verify raw data is stored before transformation (landing zone)
- [ ] Check if transformations happen in-database or in Python
- [ ] Evaluate Medallion architecture applicability (Bronze/Silver/Gold)
- [ ] Check if structured data (forex, worldbank) should be in separate "Gold" tables
- [ ] Verify PII handling (none found, but check for compliance needs)

**Current state**:
- ✅ Raw data stored first (for landing-zone sources)
- ⚠️ Transformations happen in Python (chunking, embedding) before load
- ❌ No Bronze/Silver/Gold separation
- ⚠️ `metadata` JSONB is schema-on-read — no enforcement

**Deliverable**: Architecture alignment report with Medallion recommendations

---

### B2: Configuration-Driven Design Assessment
**What**: Evaluate how declarative the pipeline system is

**Scope**: `sources.yaml`, `registry.py`, scheduler

**Checklist**:
- [ ] Check if all source config lives in `sources.yaml` (yes — good)
- [ ] Verify if pipeline logic is generic (partially — entry points are still custom)
- [ ] Check if adding a new source requires code changes or just config
- [ ] Evaluate if retry policies, rate limits, batch sizes are configurable per source
- [ ] Check if embedding model selection is config-driven (yes — in `sources.yaml`)

**Current state**:
- ✅ `sources.yaml` has schedule, conflict, embedding model per source
- ❌ Retry policy, batch size, chunk size, rate limit are NOT in config
- ❌ Adding a new source still requires writing a Python script
- ⚠️ `registry.py` auto-discovers scripts but can't dispatch generic extractors

**Deliverable**: Configuration-driven maturity score + gaps

---

### B3: Error Handling & Dead Letter Queue Design
**What**: Assess failure handling against best practices

**Scope**: All `ingest_*.py`, `utils.py`

**Checklist**:
- [ ] Check for Dead Letter Queue implementation — NONE
- [ ] Verify error classification (transient vs permanent)
- [ ] Check if failed records are quarantined for replay
- [ ] Verify logging includes correlated IDs (pipeline run IDs)
- [ ] Check for alert-worthy conditions (DLQ depth, error rate thresholds)
- [ ] Verify idempotency of all writes (upsert semantics help)

**Current state**:
- Errors are logged but not quarantined
- No distinction between transient (timeout) and permanent (bad schema) failures
- No DLQ = failed batches must be re-fetched from API
- `ingestion_jobs` tracks status but not per-record failures

**Deliverable**: Resilience gap analysis with DLQ + circuit breaker recommendations

---

### B4: Observability & Monitoring Review
**What**: Check pipeline observability against three pillars

**Scope**: `scripts/monitor_ingestion.sh`, `ingestion_status.py`, scheduler logs

**Checklist**:
- [ ] List metrics exposed (row counts, latency, error rates)
- [ ] Check for structured logging with correlation IDs — NOT PRESENT
- [ ] Verify OpenLineage or similar lineage tracking — NOT PRESENT
- [ ] Check alerting thresholds (DLQ depth, freshness SLA)
- [ ] Evaluate `ingestion_jobs` table as a basic observability surface

**Current state**:
- ✅ `ingestion_jobs` table provides basic status tracking
- ⚠️ `monitor_ingestion.sh` runs smoke tests but no structured metrics
- ❌ No Prometheus/StatsD/OpenTelemetry metrics
- ❌ No structured logging (just Python `logging` with basic formatter)
- ❌ No data lineage tracking

**Deliverable**: Observability roadmap with OpenTelemetry + OpenLineage recommendations

---

### B5: Deduplication Strategy Review
**What**: Evaluate deduplication mechanisms

**Scope**: All conflict strategies in `sources.yaml`, upsert implementations

**Checklist**:
- [ ] Document conflict strategy per source (`skip` vs `update`)
- [ ] Verify `ON CONFLICT (source, source_id, chunk_index)` is correct for all cases
- [ ] Check for content hashing to detect true duplicates
- [ ] Evaluate if `DO UPDATE` preserves `created_at` (it should!)
- [ ] Check for orphan chunks after source updates (old chunks not removed)

**Current strategies**:
- `skip`: arxiv, biorxiv, news_api, pubmed (immutable content)
- `update`: wikipedia, joplin, github, forex, worldbank, hackernews, rss, ssrn (mutable)

**Key concern**: `DO UPDATE` overwrites `content` and `embedding` but doesn't clean up old chunk_count changes. If an article shrinks from 10 chunks to 5, chunks 6-10 remain orphaned.

**Deliverable**: Deduplication maturity assessment + orphan cleanup recommendations

---

## TODOs

- [x] **P1**: Audit Async/Sync Patterns
- [x] **P2**: Audit Batching & Chunking Strategies
- [x] **P3**: Landing Zone Pattern Adoption
- [x] **P4**: Rate Limiting & Retry Strategy Review
- [x] **P5**: Parallelism & Concurrency Review
- [x] **S1**: Table Structure Review
- [x] **S2**: Index Strategy Review
- [x] **S3**: Schema Drift Detection
- [x] **S4**: Normalization vs Denormalization Analysis
- [x] **S5**: Migration & Schema Evolution Strategy
- [x] **B1**: ELT vs ETL Architecture Review
- [x] **B2**: Configuration-Driven Design Assessment
- [x] **B3**: Error Handling & Dead Letter Queue Design
- [x] **B4**: Observability & Monitoring Review
- [x] **B5**: Deduplication Strategy Review

---

## Final Verification Wave

### F1: Cross-cutting consistency check
- [x] All 14 ingestion scripts follow the same entry point convention (`main_async`/`main`)
- [x] `sources.yaml` is the single source of truth for schedules and conflict strategies
- [x] All API-calling scripts have some form of rate limiting
- [x] All scripts use `psycopg` with `register_vector_async` consistently

### F2: Performance benchmark baseline
- [x] Record current ingestion throughput per source (rows/minute)
- [x] Record embedding latency (ms per batch of 32)
- [x] Record database write latency (upsert chunks per second)
- [x] Document VRAM usage during embedding

### F3: Schema integrity check
- [x] Run `SELECT` on all tables to verify column existence matches `init.sql`
- [x] Check for NULL values in NOT NULL columns
- [x] Verify `embedding` dimensions are consistently 1024
- [x] Check `metadata` JSONB keys consistency across sources

### F4: Test coverage audit
- [x] Inventory all tests in `tests/`
- [x] Calculate coverage % for `ingestion/` directory
- [x] Identify critical untested paths (individual ingestors, scheduler, registry)
- [x] Verify integration tests can run in CI

---

## Appendix: Notepad

**Notepad path**: `.sisyphus/notepads/ingestion-review/`

- `learnings.md` — patterns found, conventions, successful approaches
- `decisions.md` — architectural choices made during review
- `issues.md` — problems, gotchas, anti-patterns
- `problems.md` — unresolved blockers, technical debt

**Research artifacts**:
- Background task `bg_ac3d5bcc`: Complete codebase catalog (14 scripts, patterns, anti-patterns)
- Background task `bg_f97121c7`: Database schema analysis (13 tables, drift issues, normalization)
- Background task `bg_017b8b65`: Industry best practices research (2024–2026)
