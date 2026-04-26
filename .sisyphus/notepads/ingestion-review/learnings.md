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