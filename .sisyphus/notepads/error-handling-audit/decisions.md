## Error Handling Audit — Architectural Decisions & Observations

### Design Decisions (Documented, No Change Needed)

1. **Landing zone pattern** (`save_raw` → `--from-raw` replay)
   - Chosen as the primary retry mechanism instead of DLQ
   - Raw API responses are persisted as JSONL.gz before processing
   - If embedding/DB fails, entire batch can be replayed from disk without re-hitting APIs
   - This is a reasonable substitute for DLQ when the failure domain is "entire batch"

2. **Idempotent upserts via ON CONFLICT**
   - All write paths use `ON CONFLICT` for idempotency
   - Two strategies: `DO UPDATE` (for changing data: forex, joplin, wikipedia) vs `DO NOTHING` (for immutable data: arxiv, biorxiv, pubmed)
   - Configured per-source in `sources.yaml` and respected by both `upsert_chunks()` and `bulk_upsert_chunks()`

3. **`embed_batch()` per-item fallback on 400**
   - Treats 400 as "bad input" (permanent) rather than transient
   - Falls back to per-item embedding to isolate which items are problematic
   - Items that still fail get `None` and are excluded from DB writes
   - This is the only instance of transient vs permanent error classification

4. **Scheduler-startup job recovery**
   - `recover_stuck_jobs()` runs at scheduler startup to clean up stale `running` jobs
   - 2-hour default timeout (configurable via `INGESTION_JOB_TIMEOUT_HOURS` env var)
   - Logs each recovered job with duration info

### Patterns by Ingestion Script

| Script | Error Handling | Async Gather | finish_job("failed")? |
|--------|---------------|-------------|----------------------|
| ingest_arxiv | catch+break on fetch fail | N/A (serial) | Never called |
| ingest_biorxiv | catch+break on fetch fail | N/A | Never called |
| ingest_forex | catch+log per-indicator | N/A | Never called |
| ingest_github | catch+break, retry in github_request | N/A | Never called |
| ingest_hackernews | catch+log | gather (no return_exceptions) | Never called |
| ingest_joplin | catch+log broad | N/A | Never called |
| ingest_news | catch+log, return_exceptions=True | gather+return_exceptions | Never called |
| ingest_news_api | catch+log | gather (no return_exceptions) | Never called |
| ingest_pubmed | catch+log | N/A | Never called |
| ingest_rss | catch+log | N/A | Never called |
| ingest_ssrn | catch+log | N/A | Never called |
| ingest_wikipedia | try/except around DB writes | N/A | Never called |
| ingest_wikipedia_updates | catch+log | N/A | Never called |
| ingest_worldbank | catch+log per-indicator | N/A | Never called |

**Only `ingest_news.py` uses `return_exceptions=True`** — isolating individual feed failures from the batch.

### Summary Statistics

- **DLQ Implementation:** NONE
- **Circuit Breaker:** NONE (ingestion layer); exists for agent LLM routing only
- **Error Classification:** Partial — only embed_batch 400 case
- **Per-record failure tracking:** NONE in DB (log-only)
- **finish_job("failed") calls:** ZERO — no script marks jobs as failed on error
- **Idempotent upserts:** FULL — all write paths use ON CONFLICT correctly