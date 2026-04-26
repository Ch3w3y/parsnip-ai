# Observability Audit Findings — Ingestion System

## 1. Monitoring Scripts

| File | Purpose |
|------|---------|
| `scripts/monitor_ingestion.sh` | Polling loop (default 30s interval) that runs `pi-ctl.sh status`, curls `/stats` and `/chat/sync` smoke tests, tails Docker scheduler logs |
| `scripts/ingestion_status.py` | CLI reporter with `--json`, `--watch`, `--wiki`, `--migration` flags; wraps `agent/ingestion_status.py` |
| `agent/ingestion_status.py` | Core helpers: `get_ingestion_overview()`, `get_migration_status()`, `get_wikipedia_bulk_status()`, `get_recent_jobs()`, `get_scheduled_next()` — queries `ingestion_jobs` + `knowledge_chunks` tables |
| `integrations/openwebui/ingestion_status.py` | OpenWebUI plugin: polls `/stats`, appends KB chunk count + ingestion progress badge to chat messages |

**Verdict:** Three observability surfaces exist but are all polling/pull-based. No push-based alerting.

## 2. Structured Logging

| Component | Format | Structured? |
|-----------|--------|-------------|
| All 18 `ingestion/*.py` files | `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")` | ❌ Plain text, no JSON |
| `scheduler/scheduler.py` | `"%(asctime)s %(levelname)s [%(name)s] %(message)s"` | ❌ Plain text with logger name, no JSON |
| `run_serial_ingestion.py` | `FileHandler + StreamHandler`, same plain format | ❌ Plain text |
| `agent/main.py` | `logging.basicConfig(level=logging.INFO)` | ❌ Bare minimum, no format specified |
| `agent/ingestion_status.py` | Uses `logging.getLogger(__name__)` | ❌ Standard logger, no structured formatter |

**Correlation IDs:** ❌ None. No job IDs, trace IDs, or request IDs in log lines. The `ingestion_jobs.id` exists in the DB but is never injected into log context.

**Scheduler log enrichment:** The scheduler logs source names (`"=== Starting daily news ingestion ==="`) but does NOT include job IDs or run durations in its structured log format.

## 3. Metrics Exposure (Prometheus/StatsD/OpenTelemetry)

| Technology | Found? |
|-----------|--------|
| Prometheus (`prometheus_client`) | ❌ Not found |
| StatsD/DogStatsD | ❌ Not found |
| OpenTelemetry SDK | ❌ Not found |
| `/metrics` endpoint | ❌ Not found |
| Custom counters/gauges | ❌ Not found |

The only near-match is `scripts/run_demo.py` line 138, which has a `"metrics"` key in a local JSON dict — it's a test harness, not an instrumentation library.

**Verdict:** Zero metrics instrumentation. No counters, gauges, histograms, or export endpoints.

## 4. Data Lineage Tracking (OpenLineage)

| Technology | Found? |
|-----------|--------|
| OpenLineage | ❌ Zero matches anywhere in the codebase |
| Custom lineage tracking | ❌ No lineage columns or tables |
| Data provenance in chunks | Partial: `source`, `source_id`, `created_at`, `updated_at` on `knowledge_chunks` but no lineage graph |

**Verdict:** No formal data lineage. Each `knowledge_chunks` row has `source` + `source_id` metadata but no trace of which ingestion run produced it, what API call fetched it, or transformation provenance.

## 5. Alerting Thresholds (DLQ Depth, SLA Breaches)

| Mechanism | Status |
|-----------|--------|
| DLQ (dead letter queue) | ❌ Does not exist. No failed-row tracking or retry queue. Failed ingestion jobs get `status='failed'` in `ingestion_jobs` but no detail on which rows failed. |
| SLA breach alerts | ❌ None. No alerting on job duration, stuck jobs past SLA, or data freshness. |
| Stuck job recovery | ✅ `utils.recover_stuck_jobs()` marks jobs stuck >2h as `"failed"`. Triggered on scheduler startup only. |
| Alerting webhooks (Slack, PagerDuty, etc.) | ❌ Zero matches for any alerting integration. |
| monitor_ingestion.sh alerting | ❌ Only logs to stdout. No threshold checks or notifications. |

**`ingestion_jobs.status` values:** `pending`, `running`, `done`, `failed`. No `warning` or `partial` states.

**Stuck job recovery:** `INGESTION_JOB_TIMEOUT_HOURS` env var (default: 2). Only runs at scheduler startup. No periodic reaper.

## 6. `ingestion_jobs` as Observability Surface

Schema (from `db/init.sql`):

```sql
CREATE TABLE ingestion_jobs (
    id          SERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
    total       INTEGER,
    processed   INTEGER NOT NULL DEFAULT 0,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    metadata    JSONB NOT NULL DEFAULT '{}'
);
```

**Observability capabilities:**
- ✅ Job lifecycle: `pending → running → done/failed`
- ✅ Progress tracking: `processed / total` fields
- ✅ Timestamps: `started_at`, `finished_at`
- ✅ Extensible: `metadata` JSONB column
- ✅ API endpoints: `/stats` (last 10 jobs), `/ingestion/status` (full overview)
- ✅ CLI: `ingestion_status.py --json --watch`
- ✅ Performance index: `ingestion_jobs_source_status_started_idx` on `(source, status, started_at DESC)`

**Observability gaps:**
- ❌ No `error_message` column — failed jobs record status but not why
- ❌ No `rows_failed` counter — only `processed` (successful rows)
- ❌ No `bytes_processed` or size metrics
- ❌ No duration computed column — must calculate `finished_at - started_at` manually
- ❌ No retry count or parent job reference
- ❌ `metadata` is always `{}` — never populated by any ingestion script

## 7. Performance Metrics (rows/sec, latency)

| Metric | Tracking |
|--------|----------|
| Rows/sec | Partial: `ingest_wikipedia.py` logs `rate = total_articles / elapsed` and `ingest_biorxiv.py` logs `"completed: {inserted} new chunks from {n} papers in {elapsed:.0f}s"`. `reembed_chunks.py` logs a rate. But: not stored anywhere, only emitted as one-time log lines. |
| Job duration | `run_serial_ingestion.py` logs per-pipeline and total elapsed time. Not persisted to DB. |
| Ingestion latency end-to-end | ❌ Not tracked. No measurement of time from API fetch to DB write completion. |
| Embedding latency | ❌ Not tracked. `embed_batch()` in utils.py has no timing instrumentation. |
| DB write latency | ❌ Not tracked. `bulk_upsert_chunks()` has no timing. |

## 8. API Endpoints for Observability

| Endpoint | Returns |
|----------|---------|
| `GET /health` | Liveness probe (basic) |
| `GET /stats` | KB source counts + last 10 `ingestion_jobs` rows |
| `GET /ingestion/status` | Full `IngestionOverview` (migration, bulk, recent jobs, schedule hints) |
| `GET /ingestion/migration` | Migration status only |
| `GET /ingestion/wikipedia` | Wikipedia bulk status only |
| `GET /admin/stack/health` | Stack-level service health checks |

**No metrics endpoint exists (no `/metrics`, no Prometheus exposition).**

## Summary of Gaps

| Category | Status | Detail |
|----------|--------|--------|
| **Structured logging** | ❌ Absent | All logging is basic Python `logging` with plain-text format. No JSON, no correlation IDs. |
| **Metrics exposition** | ❌ Absent | No Prometheus, StatsD, or OTel. No `/metrics` endpoint. No counters/gauges. |
| **Data lineage** | ❌ Absent | No OpenLineage. No lineage graph. `metadata` column unused. |
| **Alerting** | ❌ Absent | No thresholds, no webhooks, no SLA tracking. Only passive stuck-job recovery at startup. |
| **DLQ** | ❌ Absent | No dead letter queue. Failed rows are silently skipped. No per-row error tracking. |
| **Performance metrics** | ⚠️ Partial | Some ad-hoc `logger.info` timing in Wikipedia/bioRxiv/serial runner. Not persisted, not aggregated. |
| **Correlation IDs** | ❌ Absent | Job IDs in DB but never threaded into log lines. |
| **ingestion_jobs surface** | ⚠️ Partial | Good lifecycle tracking with timestamps. Missing: error details, failure counts, duration columns, retry tracking. `metadata` always empty. |