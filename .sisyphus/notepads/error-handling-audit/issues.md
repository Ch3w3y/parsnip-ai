## Error Handling Audit — Issues Found

### Critical Gaps

1. **No script calls `finish_job(conn, job_id, "failed")` on error**
   - Every script only calls `finish_job(conn, job_id, "done")` on the happy path
   - When exceptions occur, the job remains in `running` status forever
   - Only `recover_stuck_jobs()` (2h timeout) eventually marks it as `failed`
   - This means: failed jobs show as `running` for up to 2 hours, making operational monitoring unreliable

2. **No per-record failure tracking**
   - `ingestion_jobs` has `total` and `processed` but no `failed_count`
   - No `error_message` column
   - `metadata` JSONB field exists but no script populates it with error details
   - Result: can't tell from the DB how many records failed or why

3. **No Dead Letter Queue**
   - Failed records are silently dropped (logged but not persisted)
   - No mechanism to retry individual failed records
   - The `--from-raw` landing zone pattern allows replaying the entire batch, but not selective retry

4. **No circuit breaker for ingestion APIs**
   - Circuit breaker exists in `agent/graph_guardrails.py` but only for OpenRouter (agent LLM routing)
   - If Ollama goes down: 3 retries with backoff, then None is returned and records are silently dropped
   - If external APIs (arXiv, GitHub, etc.) go down: exception caught, loop breaks, partial data ingested

### Moderate Issues

5. **Broad `except Exception` everywhere**
   - Every script catches bare `Exception` — no differentiation between transient and permanent errors
   - No error taxonomy or classification
   - Makes it impossible to implement targeted retry strategies

6. **`bulk_upsert_chunks()` is all-or-nothing**
   - Single `executemany` transaction: one bad row kills the entire batch
   - `upsert_chunks()` handles per-row failures better but is slower
   - No fallback from bulk → row-by-row on batch failure

7. **`embed_batch()` backoff starts at 1s**
   - `2**attempt` gives 1s, 2s, 4s — reasonable for a local Ollama instance
   - But no jitter, so concurrent retries could thundering-herd
   - No max-wait cap beyond the retry count

### Low-Priority Issues

8. **`recover_stuck_jobs()` doesn't restart recovered jobs**
   - Only marks as `failed`, doesn't enqueue for retry
   - Requires manual intervention to re-run

9. **`ingest_news.py` is the only script using `return_exceptions=True`**
   - Most scripts use `await asyncio.gather(*tasks)` which fails the entire gather on any exception
   - `ingest_news.py` correctly uses `return_exceptions=True` to isolate per-feed failures

10. **No embedding failure quarantine**
    - If `embed_batch()` returns `None`, records with no embedding are silently dropped
    - No table or mechanism to track which records have missing embeddings