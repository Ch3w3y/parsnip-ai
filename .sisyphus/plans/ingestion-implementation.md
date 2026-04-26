# Ingestion Pipeline Remediation — Implementation Plan

> **Status**: IN PROGRESS — Phase 0 complete, Phase 1 active  
> **Started**: 2026-04-26  
> **Scope**: Implement all fixes from INGESTION_AUDIT_SYNTHESIS.md  
> **Synthesis**: See `INGESTION_AUDIT_SYNTHESIS.md` for full context

---

## Phase 0: Critical Bug Fixes ✅ COMPLETE

- [x] **C1**: `ingest_wikipedia_updates.py` — `created_at = NOW()` changed to `updated_at = NOW()`
- [x] **C2**: `ingest_rss.py` — Replaced `hash()` with `hashlib.sha256(..., usedforsecurity=False).hexdigest()[:16]`
- [x] **C3**: `ingest_ssrn.py` — Replaced `hash()` with `hashlib.sha256(..., usedforsecurity=False).hexdigest()[:16]`
- [x] **C4**: `upsert_chunks()` in `ingestion/utils.py` — Added `embedding_model` column to INSERT + all callers
- [x] **C4-ext**: `agent/main.py` — 3 inline INSERTs also updated with `embedding_model`
- [x] **C4-ext**: `scripts/restore_db.py` — Updated to handle `embedding_model` column with backward compatibility
- [x] **C5**: `ingestion/utils.py` embed_batch unbound variable — `batch` vs `texts` (already correct)
- [x] **C6**: `ingest_ssrn.py` variable shadowing — `doc` vs `docs` (already correct)
- [x] **C7**: `ingest_wikipedia_updates.py` row-at-a-time INSERT (already using `upsert_chunks`)
- [x] **C8**: `ingest_wikipedia_full.py` created_at bug (already correct)
- [x] **Verify**: No `hash()` for ID generation in `ingestion/*.py`
- [x] **Verify**: All `embedding_model` set in all INSERT paths
- [x] **Verify**: All changed files pass `python -m py_compile`

---

## Phase 1: Operational Reliability 🔄 ACTIVE

- [x] **1.1**: Extend `ingestion_jobs` schema: add `error_message TEXT`, `failed_count INTEGER NOT NULL DEFAULT 0`, `duration_ms INTEGER`
- [x] **1.1b**: Update `finish_job()` in `ingestion/utils.py` to accept/write new columns
- [x] **1.1c**: Add migration SQL for existing `ingestion_jobs` rows
- [x] **1.2**: Fix error handling in ALL `ingest_*.py` — wrap `main_async()` in try/except/finally, call `finish_job(..., 'failed')` with error_message
- [x] **1.3**: Add orphan chunk cleanup for `update` sources after upsert completes
- [x] **1.4**: Add circuit breaker to `embed_batch()` in `ingestion/utils.py`
- [x] **1.5**: Add rate limiting to 4 scripts lacking it: `ingest_forex.py`, `ingest_news.py`, `ingest_rss.py`, `ingest_worldbank.py`

---

## Phase 2: Data Integrity

- [x] **2.1**: Add `content_hash` column to `knowledge_chunks` table (`TEXT` or `BYTEA`)
- [ ] **2.2**: Hash content before embedding in all `ingest_*.py` (SHA-256)
- [ ] **2.3**: Skip embedding for unchanged content in upsert logic (ON CONFLICT + content_hash check)
- [ ] **2.4**: Backfill `content_hash` for existing rows
- [x] **2.5**: Add `verify_hash()` utility function for integrity checks

---

## Phase 3: Resilience & Error Handling

- [ ] **3.1**: Create `failed_records` Dead Letter Queue table
- [ ] **3.2**: Integrate DLQ writes into ingestion pipeline (on permanent failures)
- [ ] **3.3**: Add `retry_count` tracking to `ingestion_jobs`
- [ ] **3.4**: Add `last_error` classification (transient vs permanent)
- [ ] **3.5**: Add DLQ replay command (manual reprocess)
- [ ] **3.6**: Add scheduler job for stuck/failed ingestion retries

---

## Phase 4: Observability

- [ ] **4.1**: Add structured JSON logging with correlation IDs to ingestion pipeline
- [ ] **4.2**: Add prometheus_client metrics (ingestion rate, embedding latency, DB write latency)
- [ ] **4.3**: Add OpenTelemetry tracing to ingestion pipeline
- [ ] **4.4**: Add data lineage tracking (OpenLineage-lite)
- [ ] **4.5**: Add alerting thresholds (DLQ depth, error rate)

---

## Phase 5: Architecture

- [ ] **5.1**: Extend `sources.yaml` with operational params (batch_size, chunk_size, rate_limit)
- [ ] **5.2**: Update `SourceEntry` dataclass to read new YAML fields
- [ ] **5.3**: Update `ingest_*.py` to read operational params from YAML
- [ ] **5.4**: Auto-register scheduler jobs from `SourceRegistry` instead of hardcoding
- [ ] **5.5**: Consider Alembic for schema migrations (Phase 5.5 — strategic)

---

## Phase 6: Testing

- [ ] **6.1**: Add unit tests for each `ingest_*.py` script (mock external APIs)
- [ ] **6.2**: Add integration tests for landing zone replay
- [ ] **6.3**: Add tests for circuit breaker, DLQ, rate limiting
- [ ] **6.4**: Add CI pipeline for ingestion tests

---

## Final Verification Wave

- [ ] **F1**: All tests pass (`pytest tests/test_ingestion_utils.py`)
- [ ] **F2**: Registry imports cleanly (`python -m ingestion.registry`)
- [ ] **F3**: No `hash()` usage for ID generation in `ingest_*.py`
- [ ] **F4**: All `DO UPDATE` paths preserve `created_at` and set `updated_at = NOW()`
- [ ] **F5**: All `INSERT` paths explicitly set `embedding_model`
- [ ] **F6**: Schema changes have migrations in `db/migrations/`
- [ ] **F7**: Full test suite passes (`pytest -m "not integration and not slow"`)
- [ ] **F8**: Smoke test ingestion pipeline end-to-end

---

**Verification Checklist** (per SYNTHESIS.md §8):
- [ ] Run `pytest tests/test_ingestion_utils.py` — all pass
- [ ] Run `python -m ingestion.registry` — no import errors
- [ ] Verify no `hash()` usage remains in `ingest_*.py` for ID generation
- [ ] Verify all `DO UPDATE` paths preserve `created_at` and set `updated_at = NOW()`
- [ ] Verify `embedding_model` is explicitly set in ALL INSERT/upsert paths
- [ ] If schema changes: verify `db/init.sql` is updated AND a migration file is created
- [ ] If adding tests: run `pytest -m "not integration and not slow"` — all pass
