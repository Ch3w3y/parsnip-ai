# Ingestion Architecture Audit — Learnings

## Data Flow Pattern: ETL (Extract-Transform-Load), NOT ELT

The current architecture is **ETL, not ELT**. All transformations happen in Python **before** data is loaded into PostgreSQL:

### Transformations that occur pre-load (in Python):

1. **`chunk_text()`** — word-count sliding window chunking (200 words, 40 overlap) in `utils.py:72`
2. **`clean_text()`** — strips null bytes, non-printable chars, truncates to 2000 chars in `utils.py:86`
3. **`embed_batch()`** — calls Ollama `/api/embed` to produce 1024-dim vectors in `utils.py:92`
4. **Source-specific text composition** — each script builds natural-language text from API responses:
   - `ingest_arxiv.py`: `"title\n\nabstract"` composition
   - `ingest_news.py`: title + summary + full-text extraction from HTML (`fetch_full_text()`)
   - `ingest_forex.py`: `records_to_chunks()` builds natural-language FX descriptions with trend analysis
   - `ingest_worldbank.py`: `records_to_chunks()` builds country-level indicator summaries with YoY% changes
   - `ingest_joplin.py`: `_parse_joplin_content()` parses Joplin-serialized note format, `_chunk_text()` chunks it
5. **Metadata enrichment at insert time** — metadata dicts are constructed in Python with source, title, url, dates, etc. and passed as JSONB to `upsert_chunks()` / `bulk_upsert_chunks()`
6. **Deduplication/decoration filtering** — e.g., `ingest_news.py` deduplicates by URL, `ingest_news_api.py` skips `removed.com` URLs, `ingest_arxiv.py` skips entries without abstracts

### What happens post-load (in PostgreSQL):

- **Nothing.** Zero SQL-based transformations, zero materialized views, zero post-load enrichment.
- The `metadata` JSONB column is written once at insert time and never updated via SQL.
- `kb_search.py` performs only read queries (hybrid vector + FTS with RRF).
- No stored procedures, triggers, or UPDATE statements that modify chunk content.

## Landing Zone (Bronze Layer Equivalent)

The landing zone pattern is well-implemented for API-based sources:

### How it works:
- `save_raw(records, source, label)` writes API responses as `JSONL.gz` to `ingestion/data/raw/<source>/YYYY-MM-DD[_label].jsonl.gz`
- `iter_raw(path)` reads back from saved files
- `latest_raw(source)` finds the most recent file for a source

### Sources that follow the landing zone pattern:
- ✅ `ingest_arxiv.py` — `fetch_all_categories()` → `save_raw()` → `process_papers()`
- ✅ `ingest_news_api.py` — `fetch_all_articles()` → `save_raw()` → `ingest_articles()`
- ✅ `ingest_forex.py` — `fetch_all()` → `save_raw()` → `process_records()`
- ✅ `ingest_worldbank.py` — `fetch_all()` → `save_raw()` → `process_records()`
- ✅ `ingest_github.py` (per README, has `--from-raw`)

### Sources that DO NOT follow the landing zone pattern:
- ❌ `ingest_news.py` (RSS) — no `save_raw()`, no `--from-raw` flag. Fetches and processes inline.
- ❌ `ingest_joplin.py` — reads directly from Joplin Server API, no landing zone cache.
- ❌ `ingest_wikipedia.py` — reads from pre-extracted dump files (different pattern, reasonable).
- ❌ `ingest_wikipedia_updates.py` — incremental MediaWiki API, no raw cache.
- ❌ `ingest_biorxiv.py` — not examined but README mentions `--from-raw`, so likely follows pattern.

### What the landing zone captures:
- **Raw API responses** — the exact JSON list returned by the API, before any Python transformation
- **No transformation before save** — the `save_raw()` call happens immediately after `fetch_all_*()`, before any `process_*()` function

### What the landing zone does NOT capture:
- Binary content (PDFs, images) from crawled full-text — only the final extracted text
- Joplin note content — fetched per-item from Joplin Server API
- Intermediate transform states (e.g., after chunking but before embedding)

## Database Schema — Bronze/Silver/Gold Assessment

### Current state (single-layer, flat schema):

All KB content lands in `knowledge_chunks` — a single flat table:
```
knowledge_chunks (source, source_id, chunk_index, content, metadata JSONB, embedding VECTOR(1024), embedding_model, user_id, created_at, updated_at)
```

Plus two structured tables:
- `forex_rates` — time-series FX rates (native NUMERIC, queryable by pair/date)
- `world_bank_data` — macro indicators (NUMERIC values, queryable by country/indicator/year)

### There are NO:
- Materialized views
- SQL views
- Staging/intermediary tables
- Post-load data transformations
- UPDATE statements that enrich metadata after initial insert

## PII/Sensitive Data Handling

### Current state:
- No PII detection or redaction pipeline exists
- News articles: `ingest_news.py` `fetch_full_text()` scrapes arbitrary web content — may contain names, emails, phone numbers
- NewsAPI: `ingest_news_api.py` stores author info from article metadata
- Joplin notes: `ingest_joplin.py` stores user-created notes which may contain any personal content
- `metadata` JSONB often contains author names (arXiv), URLs, publication dates
- No `clean_text()` or PII redaction is applied to metadata — only to content passed to embedding

### Sensitive data flows:
- Raw API responses saved to `data/raw/` (gitignored, but on-disk in plaintext JSONL.gz)
- Content stored in `knowledge_chunks.content` — full text, no redaction
- `metadata` JSONB often contains author names, URLs, source attribution
- `user_id` on Joplin notes links chunks to specific users

## Structured Data (forex, worldbank) — Different Treatment

These two sources are the **only ones with dual storage**:

1. **Structured table**: `forex_rates` / `world_bank_data` — proper typed columns (NUMERIC, DATE, TEXT)
2. **KB text chunks**: `knowledge_chunks` with `source='forex'` / `source='world_bank'` — natural language descriptions for semantic search

### How they differ from other sources:
| Aspect | Unstructured (arXiv, news, GitHub) | Structured (forex, world bank) |
|--------|-------------------------------------|-------------------------------|
| Raw data | Article metadata + abstract | Time-series numerical data |
| Transform | Text composition | `records_to_chunks()` builds NL summaries with trend calculations |
| Structured table | None | Separate table with typed columns |
| KB chunks | Only semantic path | Both semantic search AND direct SQL queries |
| Conflict strategy | `DO NOTHING` (immutable) | `DO UPDATE` (mutable, updates over time) |
| Landing zone | ✅ JSONL.gz of API response | ✅ JSONL.gz of API response |

### The NL text chunk generation itself is a significant pre-load transformation:
- `ingest_forex.py:records_to_chunks()` — computes percentage changes, min/max ranges, trend direction ("strengthened"/"weakened"), produces sentences like "Over the past 30 days, the USD has strengthened 1.234% against the EUR"
- `ingest_worldbank.py:records_to_chunks()` — formats values ("$1.23 trillion"), computes YoY% changes, groups by country/indicator

These transformations are **irreversible** — the original numerical data is in the structured table, but the text representation is a derived product.

## Conflict Strategies

| Source | Strategy | Rationale |
|--------|----------|-----------|
| arXiv | `DO NOTHING` | Papers are immutable after publication |
| bioRxiv | `DO NOTHING` | Preprints are immutable |
| news (RSS) | `DO NOTHING` | Articles don't change |
| news_api | `DO NOTHING` | Articles don't change |
| PubMed | `DO NOTHING` | Abstracts are immutable |
| Wikipedia | `DO UPDATE` | Articles are edited over time |
| Wikipedia updates | `DO UPDATE` | Incremental updates |
| Joplin notes | `DO UPDATE` | Notes are edited |
| GitHub | `DO UPDATE` | READMEs and code change |
| Forex | `DO UPDATE` | Rates change daily |
| World Bank | `DO UPDATE` | Indicators are revised |
| Hacker News | `DO UPDATE` | Stories can change (score, title) |
| SSRN | `DO UPDATE` | Papers can be updated |

## Summary of Key Observations

1. **Architecture is ETL, not ELT** — all transformations (chunking, embedding, text composition, deduplication, cleaning) happen in Python before DB load
2. **Landing zone pattern is partial** — ~5 of 13+ sources follow it; RSS, Joplin, and Wikipedia updates skip it
3. **No Bronze/Silver/Gold layering** — everything lands directly into `knowledge_chunks` (the effective "gold" table). No staging, no intermediary transformations, no views
4. **No post-load SQL transformations** — zero materialized views, zero enrichment updates, zero triggers
5. **Structured data gets special treatment** — forex and worldbank have both structured tables AND derived NL chunks in the KB
6. **Pre-load NL generation is lossy** — forex/worldbank `records_to_chunks()` produces derived text that can't be perfectly reconstructed from the chunk alone
7. **No PII redaction** — author names, user content, and scraped text are stored without sanitization
8. **Joplin is unique** — no landing zone, reads from another service's API, uses `user_id` scoping, and has an incremental sync model using `last_sync_ms` metadata