## Error Handling Audit — Learnings

### Audit Date: 2026-04-25

---

### 1. embed_batch() Retry Logic (utils.py:92-160)

**3 retries with exponential backoff, per-item fallback on 400:**

- Default: 3 attempts (`retries=3`)
- Backoff: `2^attempt` seconds (1s, 2s, 4s on attempts 0/1/2)
- On HTTP 400 (Bad Request) from Ollama on first attempt: falls back to per-item embedding
  - Each item is cleaned via `clean_text()` (strips non-printable chars, caps at 2000 chars)
  - Per-item errors are logged but don't fail the batch — item gets `None` embedding
  - Returns full list if ANY item succeeds; returns `None` if ALL items fail
- On other HTTP errors (429, 500, 502, etc.): full-batch retry with backoff
- On connection/timeout exceptions: full-batch retry with backoff
- After all retries exhausted: returns `None`
- **No circuit breaker.** No throttling. No rate-limit awareness.
- **No transient vs permanent classification outside the 400 special case.** A 404 from Ollama would be retried 3 times uselessly.

**Key detail:** Empty/whitespace-only texts are pre-filtered before sending to Ollama. This prevents 400 errors from blank strings but doesn't handle other 400 causes (e.g., encoding issues, oversized inputs after clean_text's 2000-char cap).

### 2. Dead Letter Queue (DLQ)

**NONE found.** No DLQ implementation anywhere in the codebase.

- Zero matches for: `DLQ`, `dead_letter`, `deadletter`, `quarantine`, `failed_records`, `error_records`
- Failed records are simply logged (`logger.error`/`logger.warning`) and skipped
- No mechanism to retry individual failed records later
- The landing zone pattern (`save_raw` → `--from-raw` replay) partially compensates: raw data is preserved, but re-processing replays the ENTIRE batch, not just failed items

### 3. Circuit Breaker

**NONE in the ingestion layer.**

- A circuit breaker DOES exist in `agent/graph_guardrails.py` for OpenRouter API (model routing), but it is completely separate from ingestion
- The ingestion scripts have no circuit breaker pattern for:
  - Ollama embedding API calls
  - External API calls (arXiv, GitHub, NewsAPI, etc.)
  - Database connection failures
- If Ollama or an external API goes down, ingestion scripts will retry 3 times then silently give up on that batch

### 4. Error Classification (Transient vs Permanent)

**NOT formally classified.** No error taxonomy exists.

- `embed_batch()` treats **only HTTP 400** as potentially recoverable-at-item-level (per-item fallback). This is the closest thing to "permanent error" classification.
- All other errors (timeouts, 5xx, connection refused) are treated identically: retry with exponential backoff
- No distinction between:
  - Transient: timeout, 429 rate limit, 502/503 service unavailable, connection reset
  - Permanent: 400 bad request, 401 unauthorized, 404 not found, invalid data schema
- Individual ingestion scripts catch `Exception` broadly and break/continue:
  - `ingest_arxiv.py`: catches Exception on fetch, logs, breaks out of loop
  - `ingest_github.py`: catches Exception on API calls, logs, breaks
  - `ingest_news.py`: uses `return_exceptions=True` in `asyncio.gather` for feed fetches (best pattern found)
  - `ingest_joplin.py`: catches Exception broadly, logs and continues

### 5. ingestion_jobs Table — Error Tracking

**Job-level only, no per-record failure tracking.**

Schema (from `db/init.sql:53-62`):
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

- `status` field: `pending`, `running`, `done`, `failed`
- `total`: total records intended for processing
- `processed`: records successfully processed
- **No `error_message` column** — no way to see WHY a job failed without checking logs
- **No `failed_count` column** — can't tell how many records failed vs succeeded
- `metadata` JSONB could theoretically hold error details but no scripts write error info there
- Every script calls `finish_job(conn, job_id, "done")` on success
- **No script calls `finish_job(conn, job_id, "failed")`** — failed jobs are only marked by `recover_stuck_jobs()` or left as `running` forever

### 6. recover_stuck_jobs() (utils.py:294-338)

- Marks `running` jobs older than `INGESTION_JOB_TIMEOUT_HOURS` (default: 2) as `failed`
- Logs each stuck job with id, source, and duration
- Called at scheduler startup (scheduler.py:244)
- **Problem:** This doesn't distinguish between:
  - A job that truly crashed (should be `failed`)
  - A job that's running fine but slowly (e.g., Wikipedia bulk is still in progress)
  - A job where the scheduler/inventory process died but the script is still running
- **No mechanism to restart recovered jobs** — they're just marked `failed`

### 7. Upsert Idempotency (ON CONFLICT patterns)

**Well-implemented.** All upsert paths are idempotent.

**knowledge_chunks table:**
- UNIQUE constraint on `(source, source_id, chunk_index)` — natural key
- Two conflict strategies:
  1. `DO UPDATE SET content=EXCLUDED.content, embedding=EXCLUDED.embedding, metadata=EXCLUDED.metadata, updated_at=NOW()` — updates changed content
  2. `DO NOTHING` — skips existing records entirely
- Strategy chosen per-source via `sources.yaml` `conflict` field or `on_conflict` parameter
- `bulk_upsert_chunks()`:Uses `executemany` in a single transaction — efficient for bulk loads
- `upsert_chunks()`: Row-by-row with individual transactions — slower but isolates per-row failures

**Other upsert patterns:**
- `forex_rates`: `ON CONFLICT (pair, rate_date) DO UPDATE` — idempotent for rate updates
- `world_bank_data`: `ON CONFLICT (country_code, indicator_code, year) DO UPDATE` — idempotent
- `ingest_wikipedia_updates`: Uses `upsert_chunks` with ON CONFLICT — idempotent
- `ingest_joplin.py`: inline `ON CONFLICT ... DO UPDATE SET` — idempotent

**Key insight:** The `updated_at = NOW()` in DO UPDATE ensures re-ingesting the same data DOES update the timestamp even if content hasn't changed. This could cause unnecessary writes but is correct for change-tracking.

### 8. Batch Failure Behavior

**When embed_batch() returns None:**
- The calling code doesn't universally handle this. Some scripts (arXiv, GitHub) use `flush()` patterns that silently skip items with `None` embeddings
- No script writes records with `None` embeddings to the database
- No script quarantines or logs which specific records failed embedding
- The net effect: failed records are **silently dropped** with only a log message as evidence

**When DB operations fail:**
- `upsert_chunks()`: catches per-row exceptions, logs them, increments nothing for that row. Other rows in the batch continue.
- `bulk_upsert_chunks()`: uses a single `executemany` transaction — if ANY row fails, the ENTIRE batch rolls back. No partial success.