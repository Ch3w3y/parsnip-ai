# Rate Limiting Implementation - Learnings

## Summary
Added rate limiting to 4 ingestion scripts that lacked delay between API calls:
- `ingestion/ingest_forex.py` - RATE_DELAY = 0.5 (Frankfurter API)
- `ingestion/ingest_news.py` - RATE_DELAY = 1.0 (various news sources)
- `ingestion/ingest_rss.py` - RATE_DELAY = 1.0 (RSS feeds)
- `ingestion/ingest_worldbank.py` - RATE_DELAY = 0.5 (World Bank API)

## Implementation Pattern
1. **Constant Definition**: Added `RATE_DELAY = <value>` constant near top of each file (after imports, with other constants)
2. **Sleep Placement**: Added `await asyncio.sleep(RATE_DELAY)` after each HTTP API call
3. **Avoid Over-limiting**: Only added delays where actual HTTP requests occur, not in processing loops

## Key Decisions
- Used `asyncio.sleep()` instead of `time.sleep()` to avoid blocking the event loop
- Placed delays at the function level where HTTP calls occur, not in batch processing
- Chose delay values based on API characteristics:
  - 0.5s: Generous APIs (Frankfurter, World Bank)
  - 1.0s: Variable/mixed sources (news, RSS)

## Files Modified
1. `ingest_forex.py`: Added RATE_DELAY and sleep in `fetch_timeseries` loop
2. `ingest_news.py`: Added RATE_DELAY and sleep in `fetch_feed` and `fetch_full_text` functions
3. `ingest_rss.py`: Added RATE_DELAY and sleep in `fetch_feed` function
4. `ingest_worldbank.py`: Added RATE_DELAY and sleep in both `fetch_indicator_all_countries` and `fetch_indicator` functions

## Verification
- All files compile successfully with `py_compile`
- No syntax errors detected
- No LSP diagnostics errors
- Follows existing code style and conventions from `ingest_wikipedia_updates.py`
## content_hash column addition (Phase 2 data integrity)

- `content_hash TEXT` added nullable (no DEFAULT) to `knowledge_chunks` in `db/init.sql` between `content` and `metadata`
- Migration `003_content_hash.sql` uses `ADD COLUMN IF NOT EXISTS` — idempotent, follows `002_ingestion_jobs_extend.sql` pattern
- `compute_content_hash()` uses `hashlib.sha256(..., usedforsecurity=False)` — avoids FIPS issues
- `verify_hash()` returns True for None (backward compat) — callers must not assume False means corrupted; it could just be unhashed
- `py_compile` passes but runtime import needs httpx/psycopg — standalone hash logic verified independently
- SHA-256 hex is always 64 chars; stored as TEXT for simplicity (no CHAR(64) constraint needed)

## Structured JSON Logging Implementation

### New module: `ingestion/structured_logging.py`
- `StructuredFormatter`: JSON formatter emitting timestamp, level, message, source, source_id, correlation_id
- `get_correlation_id()` / `set_correlation_id()`: ContextVar-based, async-safe correlation ID tracking
- `get_ingestion_logger(source)`: Returns logger with structured or human-readable formatter based on `STRUCTURED_LOGGING` env var
- `configure_basic_logging(source)`: Replaces `logging.basicConfig()` at script startup
- Toggle: `STRUCTURED_LOGGING=true|1|yes` env var, or `enable_structured_logging()` call
- No external deps — pure stdlib `json.dumps` for JSON output

### Changes to all 14 ingest_*.py scripts
- Replaced `logging.basicConfig(...)` + `logger = logging.getLogger(__name__)` with `configure_basic_logging(<source>)` + `logger = get_ingestion_logger(<source>)`
- Added `set_correlation_id(str(job_id))` immediately after `create_job()` call
- Added `set_correlation_id(None)` in `finally` block (or at function end for joplin)
- Joplin: `extra={"source_id": note_id}` on per-note log calls
- Logging levels unchanged — INFO/WARNING/ERROR remain as they were

### Gotcha: indentation of inserted lines
- The `set_correlation_id(str(job_id))` line must be at the SAME indentation as the `job_id = await create_job(...)` line (inside the try block). Initial edits placed it at wrong indent causing SyntaxError. Fixed in all files.

## OpenTelemetry Tracing Implementation

### New module: `ingestion/tracing.py`
- `setup_tracing(service_name)`: Configures TracerProvider with ConsoleSpanExporter, or OTLP if `OTEL_EXPORTER_OTLP_ENDPOINT` set. Safe to call multiple times.
- `get_tracer(name)`: Returns real Tracer or `_NoopTracer` depending on `opentelemetry-api` availability
- `_NoopSpan`: Accepts `set_attribute`/`add_event`/`set_status`/`record_exception` — all no-ops
- `_NoopTracer`: `start_as_current_span()` returns `_NoopSpan` — zero overhead when OTEL absent
- `set_span_error(span, exc)`: Records exception + sets ERROR status; safe with _NoopSpan
- `_make_span_decorator()`: Factory creating async/sync-aware span decorators
- Public decorators: `trace_embed_batch`, `trace_upsert_chunks`, `trace_bulk_upsert_chunks`, `trace_db_write`, `trace_job`, `trace_dlq`
- Uses `inspect.iscoroutinefunction()` (not `asyncio.iscoroutinefunction` — deprecated in 3.14+)

### Changes to `ingestion/utils.py`
- Added `from tracing import ...` import after `pgvector` import
- Applied decorators: `@trace_embed_batch` on `embed_batch()`, `@trace_upsert_chunks` on `upsert_chunks()`, `@trace_bulk_upsert_chunks` on `bulk_upsert_chunks()`, `@trace_job` on `create_job()` and `finish_job()`, `@trace_dlq` on `write_to_dlq()`

### Changes to all 14 ingest_*.py scripts
- Added `from tracing import get_tracer, set_span_error` after structured_logging import
- Added `_tracer = get_tracer("parsnip.ingestion.<source>")` after logger line
- Added `with _tracer.start_as_current_span("ingest_<source>") as span:` at start of main_async (or process_articles for forex/worldbank/wikipedia)
- Added `span.set_attribute("source", "<source>")` after span creation
- Added `span.set_attribute("correlation_id", str(job_id))` after `create_job()` / manual job insert
- Added `set_span_error(span, exc)` in `except Exception as exc:` blocks
- Wikipedia: uses `process_articles()` not `main_async()` — span wraps process_articles
- Forex/WorldBank: `main_async` delegates to `process_records()` — span wraps process_records
- Joplin: No try/except around main body — span wraps processing after auth check

### Key design decisions
- NO hard dependency on `opentelemetry-api` — all tracing is no-op when not installed
- No `opentelemetry-api` added to requirements.txt
- Decorators preserve function names and signatures via `functools.wraps`
- Span attribute names use dot notation: `ingestion.operation`, `source`, `correlation_id`
- ConsoleSpanExporter for local dev, OTLP for production (env-configured)

## OpenLineage-lite addition (2026-04-26)

- All 14 ingest scripts follow one of two patterns:
  - Standard: `create_job → process → update_job_progress → finish_job("done")` + error path with `write_to_dlq → finish_job("failed")`
  - Joplin custom: manual SQL for job status update, no `finish_job` call
- Lineage emission is safe as fire-and-forget metadata — `record_lineage` swallows exceptions
- Source names in ingestion_jobs: arxiv, biorxiv/medrxiv, forex, joplin_notes, news, world_bank, wikipedia_update, github, hackernews, pubmed, rss, ssrn, wikipedia
- Some sources use a variable source name (biorxiv/medrxiv — `server` variable), news uses "news" in both RSS and API variants
- The `ingestion_jobs.processed` column is the best `record_count` for lineage — it's always set before finish_job
- py_compile is sufficient for syntax validation — the venv pytest may have pre-existing failures from missing modules (tracing, throttle)
