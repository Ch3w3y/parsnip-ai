# Ingestion Scripts Async/Sync Audit Matrix

## Summary

All 14 ingestion scripts use `async def` and async patterns. The codebase is well-structured with async I/O throughout.

## Detailed Matrix

### Legend
- ✅ = Good pattern
- ⚠️ = Needs attention
- ❌ = Problematic pattern

| Script | Async Def | HTTP Library | DB Connection | Rate Limiting | Concurrent Calls | Blocking Calls | Notes |
|--------|-----------|--------------|----------------|---------------|------------------|----------------|-------|

### 1. ingest_arxiv.py
- **Async Def**: ✅ Yes (async def throughout)
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async psycopg)
- **Rate Limiting**: ✅ `await asyncio.sleep(3)` between API calls
- **Concurrent Calls**: ✅ Controlled (sequential with sleep)
- **Blocking Calls**: ✅ None found
- **Notes**: Uses landing zone pattern, proper async/await throughout

### 2. ingest_biorxiv.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ `await asyncio.sleep(0.5)`
- **Concurrent Calls**: ✅ Controlled
- **Blocking Calls**: ✅ None
- **Notes**: Similar to arxiv, proper async patterns

### 3. ingest_forex.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ Controlled with sleep
- **Concurrent Calls**: ✅ Limited
- **Blocking Calls**: ✅ None
- **Notes**: Uses async DB operations

### 4. ingest_github.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ Multiple `await asyncio.sleep()` calls
- **Concurrent Calls**: ✅ Controlled with batches
- **Blocking Calls**: ✅ None
- **Notes**: Complex async workflow with proper error handling

### 5. ingest_hackernews.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ `await asyncio.sleep(0.5)`
- **Concurrent Calls**: ⚠️ Uses `asyncio.gather(*tasks)` with 20 tasks per batch
- **Blocking Calls**: ✅ None
- **Notes**: Uses unbounded `asyncio.gather` but in small batches (20)

### 6. ingest_joplin.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ⚠️ Uses `psycopg.AsyncConnection.connect()` directly (not utils)
- **Rate Limiting**: ✅ No explicit rate limiting (private API)
- **Concurrent Calls**: ✅ None (sequential)
- **Blocking Calls**: ✅ None
- **Notes**: Has its own DB logic, uses async psycopg correctly

### 7. ingest_news_api.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ `asyncio.Semaphore(2)` for max 2 concurrent requests
- **Concurrent Calls**: ✅ Controlled with Semaphore
- **Blocking Calls**: ✅ None
- **Notes**: **Best practice** - uses Semaphore for rate limiting

### 8. ingest_news.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ None (RSS feeds typically allow high volume)
- **Concurrent Calls**: ⚠️ Uses `asyncio.gather` for all feeds at once
- **Blocking Calls**: ✅ None
- **Notes**: Unbounded gather but RSS feeds are usually tolerant

### 9. ingest_pubmed.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ `await asyncio.sleep(1)`
- **Concurrent Calls**: ✅ Controlled
- **Blocking Calls**: ✅ None
- **Notes**: Proper async patterns

### 10. ingest_rss.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ None
- **Concurrent Calls**: ⚠️ Uses `asyncio.gather(*tasks)` for all feeds
- **Blocking Calls**: ✅ None
- **Notes**: Unbounded gather, similar to ingest_news.py

### 11. ingest_ssrn.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ `await asyncio.sleep(0.1)` and `sleep(2)`
- **Concurrent Calls**: ✅ Controlled
- **Blocking Calls**: ✅ None
- **Notes**: Proper async patterns

### 12. ingest_wikipedia.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ❌ None (reads local JSON files from Wikipedia dump)
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ Controlled with batches
- **Concurrent Calls**: ✅ Limited
- **Blocking Calls**: ✅ None
- **Notes**: Large-scale ingestion from local files, proper async DB operations

### 13. ingest_wikipedia_updates.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ `RATE_DELAY` with `await asyncio.sleep()`
- **Concurrent Calls**: ✅ Controlled
- **Blocking Calls**: ✅ None
- **Notes**: Incremental updates with proper async

### 14. ingest_worldbank.py
- **Async Def**: ✅ Yes
- **HTTP Library**: ✅ `httpx.AsyncClient`
- **DB Connection**: ✅ `get_db_connection()` (async)
- **Rate Limiting**: ✅ Controlled
- **Concurrent Calls**: ✅ Limited
- **Blocking Calls**: ✅ None
- **Notes**: Proper async patterns throughout

## Key Findings

### ✅ Positive Patterns (All Scripts)
1. **All scripts use `async def`** - No synchronous entry points
2. **All use `httpx.AsyncClient`** - No blocking `requests` library
3. **All use async psycopg** - Either via `get_db_connection()` or direct `psycopg.AsyncConnection`
4. **All use `await` properly** - No blocking calls inside async functions
5. **All follow landing zone pattern** - Separate fetch from processing phases

### ⚠️ Areas for Attention
1. **Unbounded `asyncio.gather` usage** in 3 scripts:
   - `ingest_hackernews.py`: Gathers 20 tasks per batch (but limited batch size)
   - `ingest_news.py`: Gathers all RSS feed tasks at once
   - `ingest_rss.py`: Gathers all feed tasks at once
   
2. **No Semaphore in most scripts** - Only `ingest_news_api.py` uses `asyncio.Semaphore(2)` for rate limiting

### ❌ No Critical Issues Found
- **No blocking `requests` library usage**
- **No `time.sleep()` in async code** (all use `await asyncio.sleep()`)
- **No blocking subprocess calls**
- **No unbounded concurrent DB operations**

## Recommendations Priority

1. **HIGH**: Add Semaphore-based rate limiting to `ingest_news.py` and `ingest_rss.py`
2. **MEDIUM**: Consider Semaphore for `ingest_hackernews.py` batch gathering
3. **LOW**: Standardize DB connection usage (ingest_joplin.py uses direct psycopg instead of utils)

## Library Usage Summary

- **httpx**: 13 scripts (all except ingest_wikipedia.py which reads local files)
- **aiohttp**: 0 scripts
- **requests**: 0 scripts (synchronous)
- **urllib**: 0 scripts for HTTP (only urllib.parse for URL parsing)
- **psycopg async**: 14 scripts (all use async database operations)

## Concurrency Patterns

- **Semaphore usage**: Only 1 script (`ingest_news_api.py`)
- **asyncio.gather usage**: 4 scripts (hackernews, news, rss, news_api)
- **asyncio.create_task**: 0 scripts
- **Unbounded gather**: 3 scripts (hackernews, news, rss)

## Database Patterns

- **get_db_connection()**: 13 scripts (all except ingest_joplin.py)
- **Direct psycopg.AsyncConnection**: 1 script (ingest_joplin.py)
- **All async DB operations**: ✅ All 14 scripts

## Conclusion

The ingestion layer is **well-architected with async I/O throughout**. The codebase demonstrates:

1. ✅ Consistent async patterns
2. ✅ Proper async/await usage
3. ✅ Async database operations
4. ✅ Async HTTP with httpx
5. ✅ Landing zone pattern for resilience

**Minor improvements needed**: Add Semaphore-based rate limiting to 3 scripts that use unbounded `asyncio.gather`.
