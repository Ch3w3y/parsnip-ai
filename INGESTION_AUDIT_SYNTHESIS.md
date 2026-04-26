# Ingestion Pipeline Audit — Synthesis & Prioritised Remediation Plan

> **Audit completed**: 2026-04-25  
> **Scope**: 14 ingestion pipelines, 13 DB tables, 3 dimensions, 4 verification gates  
> **This document**: Executive synthesis + prioritised remediation roadmap for hand-off to a new agent  
> **For full granular detail**: See the 14 detailed artifact files referenced below. This synthesis deliberately omits deep evidence to stay readable — follow the links for implementation-level specifics.

---

## 1. What Was Audited

### Dimensions
| Dimension | Tasks | Status |
|-----------|-------|--------|
| **Pipeline Efficiency** | P1–P5 (async/sync, batching, landing zone, rate limiting, parallelism) | ✅ Complete |
| **Schema Alignment** | S1–S5 (table structure, indices, drift, normalisation, migrations) | ✅ Complete |
| **Best Practice Compliance** | B1–B5 (ELT architecture, config-driven design, error handling, observability, deduplication) | ✅ Complete |
| **Final Verification Wave** | F1–F4 (consistency, benchmarks, schema integrity, test coverage) | ✅ Complete |

---

## 2. Evidence Archive — Where to Find What

Each artifact below contains raw evidence for one slice of the audit. **Consult these when you need implementation details, not summaries.**

### 🧠 Pre-Review Research (Background context)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/notepads/ingestion-review/research-findings.md` | Industry best practices research (2024–2026): ETL vs ELT, micro-batch vs streaming, retry strategies, DLQ patterns, circuit breakers, schema registries, dlt/Prefect/OpenLineage recommendations. Use this to justify architectural decisions. |

### 🔍 Pipeline Efficiency (P1–P5)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/plans/ingestion-review.md` (lines 1–120) | Complete audit plan for P1–P5: async/sync patterns, batching strategy, landing zone adoption matrix, rate limiting inventory, parallelism opportunity map. **This is the master checklist.** |
| `ingestion_audit_final_report.md` | Script-by-script async/sync audit result. Grade: A (93/100). Lists which 3 scripts use unbounded `asyncio.gather`, which 1 uses `Semaphore`, which 13/14 use `httpx.AsyncClient`. |
| `ingestion_audit_matrix.md` | Per-script detailed matrix: entry point type, HTTP library, DB connection method, landing zone usage, batch size, chunk size, embed model, conflict strategy, rate limit mechanism. **Use this as a lookup table.** |
| `ingestion_audit_summary.md` | Executive summary of the async audit with recommendations. Good for quick context. |

### 🗄️ Schema & Data Model (S1–S5, F3)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/notepads/ingestion-review/findings.md` | **F3 Schema Integrity Check** — column-by-column verification of all tables, NOT NULL violation analysis, embedding model dimension verification, per-source metadata JSONB key inventory. **This is where the `embedding_model` omission bug is documented.** |
| `.sisyphus/notepads/ingestion-review/research-findings.md` (lines 60–130) | Database schema inventory from the initial exploration. Lists all 13 tables with column types, constraints, indices. |

### 🛡️ Error Handling & Resilience (B3)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/notepads/error-handling-audit/issues.md` | Critical gaps: no `finish_job("failed")`, no per-record failure tracking, no DLQ, no circuit breaker in ingestion layer. 10 issues ranked by severity. |
| `.sisyphus/notepads/error-handling-audit/learnings.md` | Deep dive into `embed_batch()` retry logic (3 retries, exponential backoff, per-item 400 fallback), `ingestion_jobs` schema analysis, `recover_stuck_jobs()` behaviour, batch failure modes (bulk_upsert vs upsert), idempotency verification. **Read this for retry implementation details.** |

### 🔁 Deduplication (B5)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/notepads/dedup-audit/issues.md` | **Complete deduplication analysis** — conflict strategy per source (YAML vs actual SQL), unique constraint verification, `created_at` preservation bug in `ingest_wikipedia_updates.py`, Python `hash()` instability in RSS/SSRN, orphan chunk gap, content hashing gap, cross-source dedup analysis, source_id format inventory. **This is where C1, C2, C3 are fully documented with line numbers.** |

### 📊 Observability (B4)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/notepads/observability-audit/findings.md` | Complete observability audit: monitoring scripts (4 files), structured logging status (none), metrics exposure (none), OpenLineage (none), alerting (none), `ingestion_jobs` surface gaps, performance metrics (ad-hoc only), API endpoints list. |

### 🏗️ Architecture & Configuration (B1, B2)
| File | What You'll Find |
|------|------------------|
| `.sisyphus/notepads/ingestion-architecture-audit/learnings.md` | ETL vs ELT assessment (current = ETL), landing zone pattern analysis (which 5 sources use it, which don't), Bronze/Silver/Gold gap analysis, structured data dual-write pattern (forex + worldbank), PII handling status, conflict strategy rationale per source, Joplin uniqueness analysis. |
| `ingestion_config_audit.md` | Configuration-driven design assessment. `sources.yaml` field inventory, operational parameter analysis (batch sizes, chunk sizes, rate limits, retry policies — all hardcoded), new source requirements (code + YAML + scheduler), registry dispatch capability, completeness matrix, declarative score: 6/10. **Use this for Phase 2 scoping.** |

### 📋 Other Reference Files
| File | What You'll Find |
|------|------------------|
| `.sisyphus/plans/ingestion-review.md` (lines 375–420) | Complete TODO list with all 19 tasks marked ✅. Final Verification Wave F1–F4 results. **This is the canonical completion record.** |
| `.sisyphus/notepads/ingestion-review/learnings.md` | Brief notes on patterns learned during orchestration (landing zone convention, entry point convention, conflict strategies). |

---

## 3. Critical Issues (Do First — 1–2 hours total)

These are data-integrity bugs with zero-risk fixes. **For the exact line numbers and surrounding code, see the Evidence Archive above.**

| # | Issue | File:Line | Impact | Fix |
|---|-------|-----------|--------|-----|
| **C1** | `created_at` reset to `NOW()` on every Wikipedia update | `ingest_wikipedia_updates.py:150` | Destroys original insertion timestamps | Change `created_at = NOW()` → `updated_at = NOW()` |
| **C2** | Unstable `hash()` breaks dedup across runs | `ingest_rss.py:247` | Same feed entries get new IDs every run | Replace `hash()` with `hashlib.sha256(..., usedforsecurity=False).hexdigest()[:16]` |
| **C3** | Unstable `hash()` breaks dedup across runs | `ingest_ssrn.py:200` | Same SSRN entries get new IDs every run | Same fix as C2 |
| **C4** | `upsert_chunks()` omits `embedding_model` from INSERT | `utils.py` | 4 code paths rely on column DEFAULT; fragile if models change | Add `embedding_model` to the INSERT tuple in `upsert_chunks()` |

---

## 3. Prioritised Remediation Plan — 5 Phases

### Phase 0: Bug Fix (1–2 hours) — **DO FIRST**
Fix all 4 critical issues above. Zero risk, immediate data-integrity wins.

### Phase 1: Quick Wins (2–4 hours total)
Operational reliability improvements. Low risk, high monitoring/debugging value.

| # | Item | Effort | Key Change |
|---|------|--------|------------|
| **1.1** | Add `error_message`, `failed_count`, `duration` to `ingestion_jobs` table | 30 min | ALTER TABLE + update `finish_job()` in `utils.py` |
| **1.2** | Fix error handling: call `finish_job(conn, job_id, "failed")` in `except` blocks | 1 hour | Wrap each `main_async()` body in try/except/finally |
| **1.3** | Add orphan chunk cleanup for `update` sources | 2 hours | Per-source `DELETE FROM knowledge_chunks WHERE source = ? AND source_id = ? AND chunk_index >= ?` after upsert |
| **1.4** | Add circuit breaker to `embed_batch()` in `utils.py` | 1 hour | `circuitbreaker` library or simple fail-fast counter |
| **1.5** | Add `asyncio.sleep()` delays to 4 scripts lacking rate limiting | 30 min | `ingest_forex.py`, `ingest_news.py`, `ingest_rss.py`, `ingest_worldbank.py` |

### Phase 2: Config-Driven Refactoring (1–2 days)
Make operational parameters YAML-driven so adding a new source = 3 lines of YAML, not a new Python module.

| # | Item | Key Change |
|---|------|------------|
| **2.1** | Extend `sources.yaml` schema with `batch_size`, `chunk_size`, `rate_limit`, `retry_policy` | Add fields, update `SourceEntry` dataclass |
| **2.2** | Update all `ingest_*.py` to read operational params from YAML via registry | Replace hardcoded constants with `config["batch_size"]` |
| **2.3** | Auto-register scheduler jobs from `SourceRegistry` instead of hardcoding in `scheduler.py` | Refactor `scheduler.py` to iterate over registry |

### Phase 3: Resilience Layer (2–3 days)
Prevent silent data loss and wasted compute.

| # | Item | Why |
|---|------|-----|
| **3.1** | Add `failed_records` DLQ table | Failed records are currently just log messages — no replay, no analysis |
| **3.2** | Add `content_hash` column to `knowledge_chunks` | Every `DO UPDATE` re-embeds ALL chunks even if unchanged. For Wikipedia at scale, this wastes massive embedding API calls |
| **3.3** | Formal error classification: transient vs permanent | A 404 from arXiv is retried 3 times uselessly. Transient → retry, permanent → DLQ immediately |

### Phase 4: Observability & Testing (3–5 days)
Production-grade confidence.

| # | Item | Current State |
|---|------|---------------|
| **4.1** | Structured JSON logging with correlation IDs | `%(asctime)s %(levelname)s %(message)s` — plain text only |
| **4.2** | OpenTelemetry metrics (ingestion rate, embedding latency, DB write latency) | Zero metrics. No `/metrics` endpoint. |
| **4.3** | Unit tests for each `ingest_*.py` script | Only utility functions tested — no ingestor coverage |
| **4.4** | Integration tests for scheduler + landing zone replay | No end-to-end tests |

### Phase 5: Architecture Evolution (1–2 weeks — strategic)
Long-term maintainability and scale.

| # | Item | Rationale |
|---|------|-----------|
| **5.1** | Adopt Alembic for schema migrations | Manual `IF NOT EXISTS` SQL scripts, no rollback, no version control |
| **5.2** | Bronze/Silver/Gold materialized views | Everything lands directly in `knowledge_chunks` (Gold). No raw staging layer. |
| **5.3** | Cross-source content deduplication | Same news story from RSS + NewsAPI stored twice |

---

## 4. Issue Register — All Findings by Severity

### 🔴 Critical (Data Integrity Bugs)
| ID | Finding | Evidence File |
|----|---------|---------------|
| C1 | `ingest_wikipedia_updates.py` resets `created_at = NOW()` instead of `updated_at` | `dedup-audit/issues.md` §3 |
| C2 | `ingest_rss.py` uses Python's randomized `hash()` for `source_id` | `dedup-audit/issues.md` §4 |
| C3 | `ingest_ssrn.py` uses Python's randomized `hash()` for `source_id` | `dedup-audit/issues.md` §4 |
| C4 | `upsert_chunks()` omits `embedding_model` — relies on DEFAULT | `ingestion-review/findings.md` §2 |

### 🟡 High (Operational Reliability)
| ID | Finding | Evidence File |
|----|---------|---------------|
| H1 | No orphan chunk cleanup when articles shrink | `dedup-audit/issues.md` §5 |
| H2 | No circuit breaker on Ollama embedding service | `error-handling-audit/learnings.md` §1 |
| H3 | No script calls `finish_job(..., "failed")` on error | `error-handling-audit/issues.md` §1 |
| H4 | `ingestion_jobs` table lacks `error_message` / `failed_count` / `duration` | `observability-audit/findings.md` §6 |
| H5 | No Dead Letter Queue — failed records silently dropped | `error-handling-audit/issues.md` §3 |
| H6 | No content hashing — every update re-embeds unchanged content | `dedup-audit/issues.md` §4 |
| H7 | 4 scripts lack rate limiting (forex, news, rss, worldbank) | `ingestion_audit_summary.md` |

### 🟠 Medium (Inconsistency / Maintenance Risk)
| ID | Finding | Evidence File |
|----|---------|---------------|
| M1 | `ingest_joplin.py` bypasses `get_db_connection()` — uses direct psycopg | `ingestion_audit_summary.md` |
| M2 | Metadata key inconsistency: `url` vs `link` across sources | `ingestion-review/findings.md` §4 |
| M3 | Metadata date key inconsistency: `published` vs `date` vs `year` | `ingestion-review/findings.md` §4 |
| M4 | `run_serial_ingestion.py` uses synchronous `subprocess.run()` | `ingestion_audit_matrix.md` |
| M5 | Scheduler jobs hardcoded in `scheduler.py` despite YAML having schedules | `ingestion_config_audit.md` |
| M6 | Batch size, chunk size, retry policy hardcoded per script | `ingestion_config_audit.md` |
| M7 | `agent_memories.metadata` column exists but never populated | `ingestion-review/findings.md` §2 |
| M8 | No alembic / formal migration framework | `ingestion_architecture_audit/learnings.md` |

### 🟢 Low (Observability / Testing / Architecture)
| ID | Finding | Evidence File |
|----|---------|---------------|
| L1 | No structured logging (JSON), no correlation IDs | `observability-audit/findings.md` §2 |
| L2 | No Prometheus/StatsD/OpenTelemetry metrics | `observability-audit/findings.md` §3 |
| L3 | No data lineage tracking (OpenLineage) | `observability-audit/findings.md` §4 |
| L4 | No alerting thresholds or webhooks | `observability-audit/findings.md` §5 |
| L5 | Only 4 test files test ingestion code; 0 tests for individual ingestors | `ingestion_audit_summary.md` |
| L6 | `run_serial_ingestion.py` not integrated with `SourceRegistry` | `ingestion_audit_matrix.md` |
| L7 | `source_id` format differs: bulk Wikipedia uses `title`, updates use `title::idx` | `dedup-audit/issues.md` §6 |
| L8 | Only 1 of 14 scripts uses `asyncio.Semaphore` | `ingestion_audit_summary.md` |

---

## 5. Architecture Decision Register

| Decision | Status | Rationale |
|----------|--------|-----------|
| **ETL (not ELT)** | ✅ Preserved for now | All transformations in Python before load. Moving to ELT would require significant rearchitecting. Defer until Phase 5 if needed. |
| **DiskANN vs HNSW** | ✅ Keep DiskANN | Correct choice for 20M+ vectors on limited RAM. 95–97% recall is acceptable. |
| **Raw psycopg (no ORM)** | ✅ Keep for now | Migration to SQLAlchemy + Alembic is Phase 5. No immediate need to change. |
| **Dual-write for structured data** | ✅ Keep | `forex_rates` + `world_bank_data` serve direct query use cases that vector search can't handle. |
| **Sources.yaml as config backbone** | ✅ Expand | Phase 2 extends this to operational params. Foundation is solid. |
| **Landing zone for 10/14 sources** | ⚠️ Add to remaining 3 | `ingest_joplin.py`, `ingest_news.py`, `ingest_wikipedia_updates.py` should adopt `save_raw()`/`iter_raw()`. |

---

## 6. Where to Start — Presets for New Agent

### Preset A: "Just Fix the Bugs" (1–2 hours)
Implement **Phase 0** only. All 4 critical issues are 1-line fixes.

### Preset B: "Make It Reliable" (1 week)
Implement **Phases 0 + 1 + 3.1 (DLQ) + 3.2 (content_hash)**.
This prevents silent failures, stops wasted compute, and gives you operational visibility.

### Preset C: "Production Grade" (2–3 weeks)
Implement **Phases 0–4**.
This gets you to a state where you can add new sources with YAML only, debug failures in minutes, and trust the system at scale.

### Preset D: "Future Proof" (1–2 months)
All phases including **Phase 5** (Alembic, Bronze/Silver/Gold, cross-source dedup).

---

## 7. Key Files to Know

| File | Purpose |
|------|---------|
| `ingestion/utils.py` | Shared: chunking, embedding, DB connection, upsert, job tracking, landing zone I/O |
| `ingestion/registry.py` | `SourceRegistry` — auto-discovers `ingest_*.py`, reads `sources.yaml`, dispatches entry points |
| `ingestion/sources.yaml` | Declarative config: 14 sources with module, schedule, conflict, embedding model |
| `scheduler/scheduler.py` | APScheduler daemon — hardcoded cron jobs + Joplin watcher |
| `db/init.sql` | 13 tables: `knowledge_chunks`, `ingestion_jobs`, `forex_rates`, `world_bank_data`, `agent_memories`, Joplin tables |
| `ingestion/reembed_chunks.py` | Standalone: re-embed chunks with `embedding IS NULL` |
| `agent/tools/pdf_ingest.py` | PDF ingestion via LangChain tool |
| `tests/test_ingestion_utils.py` | Only ingestion tests — utilities only, no individual ingestors |

---

## 8. Verification Checklist for New Agent

Before claiming ANY phase complete, the agent MUST:

- [ ] Run `pytest tests/test_ingestion_utils.py` — all pass
- [ ] Run `python -m ingestion.registry` — no import errors
- [ ] Verify no `hash()` usage remains in `ingest_*.py` for ID generation
- [ ] Verify all `DO UPDATE` paths preserve `created_at` and set `updated_at = NOW()`
- [ ] Verify `embedding_model` is explicitly set in ALL INSERT/upsert paths
- [ ] If schema changes: verify `db/init.sql` is updated AND a migration file is created
- [ ] If adding tests: run `pytest -m "not integration and not slow"` — all pass

---

*End of synthesis. This document is designed for a fresh agent with no prior context. All evidence lives in the files listed in Section 2.*
