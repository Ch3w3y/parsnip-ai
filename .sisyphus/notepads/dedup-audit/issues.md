
## Deduplication Audit — 2025-04-25

### 1. Conflict Strategy per Source (sources.yaml vs Actual SQL)

| Source | `sources.yaml` | Actual SQL Strategy | Consistent? |
|--------|----------------|---------------------|-------------|
| **arxiv** | `skip` | Uses `bulk_upsert_chunks(on_conflict="skip")` → `DO NOTHING` | ✅ |
| **biorxiv** | `skip` | Uses `bulk_upsert_chunks(on_conflict="skip")` → `DO NOTHING` | ✅ |
| **news_api** | `skip` | Uses `bulk_upsert_chunks(on_conflict="skip")` → `DO NOTHING` | ✅ |
| **news** (RSS fallback) | `skip` | Uses `upsert_chunks(on_conflict="nothing")` → `DO NOTHING` | ✅ |
| **pubmed** | `skip` | Uses `bulk_upsert_chunks(on_conflict="skip")` → `DO NOTHING` | ✅ |
| **wikipedia** | `update` | Uses `bulk_upsert_chunks(on_conflict="update")` → `DO UPDATE` | ✅ |
| **wikipedia_updates** | `update` | Inline SQL: `ON CONFLICT ... DO UPDATE SET content, embedding, created_at = NOW()` | ✅ (but BUG — see §3) |
| **joplin** | `update` | Inline SQL: `ON CONFLICT ... DO UPDATE SET content, embedding, metadata, user_id, updated_at = NOW()` | ✅ |
| **github** | `update` | Uses `bulk_upsert_chunks(on_conflict="update")` → `DO UPDATE` | ✅ |
| **forex** | `update` | Separate table `forex_rates` with `ON CONFLICT (pair, rate_date) DO UPDATE SET rate = EXCLUDED.rate, fetched_at = NOW()` | ✅ (uses separate table, not knowledge_chunks) |
| **worldbank** | `update` | Separate table `world_bank_data` with `ON CONFLICT (country_code, indicator_code, year)` | ✅ (uses separate table) |
| **hackernews** | `update` | Uses `bulk_upsert_chunks(on_conflict="update")` → `DO UPDATE` | ✅ |
| **rss** | `update` | Uses `upsert_chunks(on_conflict="update")` → `DO UPDATE` | ✅ |
| **ssrn** | `update` | Uses `bulk_upsert_chunks(on_conflict="update")` → `DO UPDATE` | ✅ |

**Additional sources NOT in sources.yaml but writing to knowledge_chunks:**

| Source | Conflict Strategy | Location |
|--------|-------------------|----------|
| **user_notes** | `update` | `agent/main.py` (3 locations), `agent/tools/notes.py`, `agent/tools/notes_pg.py` |
| **user_docs** | `update` | `agent/tools/pdf_ingest.py` |

All are inline SQL with `ON CONFLICT (source, source_id, chunk_index) DO UPDATE SET content, metadata, embedding, updated_at = NOW()`.

### 2. `ON CONFLICT (source, source_id, chunk_index)` — Unique Constraint Verification

The `knowledge_chunks` table has:
```sql
UNIQUE (source, source_id, chunk_index)  -- defined in db/init.sql line 23
```

This three-column composite unique constraint is correct and sufficient for deduplication. Each chunk is uniquely identified by its source type, source article ID, and chunk position within that article.

**Index support:** A `knowledge_chunks_source_idx` btree index on `(source, source_id)` also exists, supporting filtered queries but the UNIQUE constraint handles dedup.

### 3. `created_at` Preservation During Updates — BUG FOUND ⚠️

**Most upsert paths correctly omit `created_at` from the `DO UPDATE SET` clause**, relying on PostgreSQL's behavior where columns not mentioned in `SET` retain their existing values. This preserves the original insertion timestamp.

**However, `ingest_wikipedia_updates.py` has a BUG:**

```python
# Line 150 — ingest_wikipedia_updates.py
ON CONFLICT (source, source_id, chunk_index)
DO UPDATE SET
    content   = EXCLUDED.content,
    embedding = EXCLUDED.embedding,
    created_at = NOW()       # ← BUG: Overwrites original created_at!
```

This **resets `created_at` to the update timestamp** every time a Wikipedia article is re-ingested, destroying the original insertion date. All other `DO UPDATE` paths correctly omit `created_at` from the SET clause.

**All other `DO UPDATE` paths verified correct:**

| Location | SET clause includes `created_at`? |
|----------|-----------------------------------|
| `utils.py:upsert_chunks()` (line 183) | ❌ No — correct, preserves |
| `utils.py:bulk_upsert_chunks()` (line 237) | ❌ No — correct, preserves |
| `ingest_joplin.py` (line 332) | ❌ No — correct, preserves |
| `ingest_wikipedia_updates.py` (line 150) | ✅ **Yes — BUG, overwrites** |
| `agent/main.py` 3 locations (lines 1246, 1373, 1673) | ❌ No — correct, preserves |
| `agent/tools/notes.py` (line 44) | ❌ No — correct, preserves |
| `agent/tools/notes_pg.py` (line 65) | ❌ No — correct, preserves |
| `agent/tools/pdf_ingest.py` (line 94) | ❌ No — correct, preserves |

Also note: **all `DO UPDATE` paths set `updated_at = NOW()`** except `ingest_wikipedia_updates.py`, which sets `created_at = NOW()` instead of `updated_at = NOW()`. It's missing `updated_at` entirely and incorrectly has `created_at`.

### 4. Content-Based Deduplication (Hashing)

**No content hashing exists anywhere in the codebase.**

The only uses of `hash()` are for `source_id` generation (not content dedup):

- `ingest_ssrn.py` line 200: `source_id = openalex_id if openalex_id else f"openalex_{hash(title)}"`
- `ingest_rss.py` line 247: `source_id = f"rss_{hash(link or title)}"`

These use Python's `hash()` (which is randomized per process, not stable across runs) for generating source identifiers when canonical IDs are missing. This is fragile — Python's `hash()` is not deterministic across interpreter sessions (PYTHONHASHSEED randomization).

**No content hash column exists** in the `knowledge_chunks` table. Deduplication relies entirely on the `(source, source_id, chunk_index)` composite key. There is no mechanism to detect:
- Content drift (same article ID, different content)
- Identical content across different source_ids
- Redundant re-embedding of unchanged content

### 5. Orphan Chunk Handling — NO CLEANUP EXISTS ⚠️

**No orphan chunk cleanup logic exists anywhere in the codebase.**

Grepping for `DELETE FROM knowledge_chunks`, `TRUNCATE knowledge_chunks`, `prune_chunks`, `clean*chunk`, `remove.*chunk`, `stale.*chunk`, or `orphan` returned **zero results**.

This is a significant gap. When an article changes and shrinks from N chunks to M chunks (where M < N), the old chunks at indices M..N-1 remain in the database indefinitely as orphaned data:

- **Wikipedia articles** that are shortened in updates → orphan chunks persist
- **Joplin notes** edited to be shorter → orphan chunks persist
- **GitHub repos** where files are removed → orphan chunks persist
- **HackerNews stories** updated with shorter content → orphan chunks persist
- **RSS feed entries** that shrink → orphan chunks persist

For `skip` sources (arxiv, biorxiv, news_api, pubmed, news), this is less of a concern since `DO NOTHING` means content is never re-processed, so chunks can't shrink.

### 6. Cross-Source Duplicate Detection

**No cross-source deduplication exists.**

The `(source, source_id, chunk_index)` unique constraint only prevents duplicates within the same source. An article about the same topic appearing in both `arxiv` and `news_api` would be stored as separate records with different `source` values. This is by design — the system intentionally keeps content from different sources separate for retrieval diversity.

The `source_id` values are not normalized across sources:
- arxiv: raw arXiv ID (e.g., `2301.01234`)
- biorxiv: DOI
- news_api: URL
- pubmed: `pmid_{pmid}`
- hackernews: `hn_{story_id}`
- github: `{owner}/{repo}/{path}`
- joplin: `{note_id}` (with user_id filter)
- wikipedia: `{article_title}::{chunk_index}` (in updates; note the `::` format differs from bulk)
- rss: `rss_{hash(link)}` (unstable — see §4)
- ssrn: `openalex_{hash(title)}` (unstable — see §4)

### 7. source_id Format Inconsistencies

| Source | source_id format | Stable? | Notes |
|--------|-----------------|---------|-------|
| arxiv | Raw ID (e.g. `2301.01234`) | ✅ | Deterministic |
| biorxiv | DOI | ✅ | Deterministic |
| news_api | Full URL | ⚠️ | Could vary with query params/tracking params |
| pubmed | `pmid_{id}` | ✅ | Deterministic |
| hackernews | `hn_{id}` | ✅ | Deterministic |
| github | `{owner}/{repo}/{path}` | ⚠️ | Path could change if files are renamed |
| joplin | UUID note ID | ✅ | Deterministic |
| wikipedia | Article title | ✅ | Deterministic |
| wikipedia_updates | `{title}::{idx}` | ✅ | Different format from bulk (which uses just title) |
| rss | `rss_{hash(link)}` | ❌ | Python `hash()` not stable across sessions |
| ssrn | `openalex_{hash(title)}` | ❌ | Python `hash()` not stable across sessions |

**Critical issues with rss/ssrn:** Python's `hash()` function is randomized per interpreter session since Python 3.3 (PYTHONHASHSEED). This means:
- The same RSS link will generate different `source_id` values across different runs
- This breaks deduplication entirely — the same content will be inserted as new rows instead of hitting the conflict clause
- This appears to be an existing bug that hasn't been caught because these are manual-only sources

### 8. Summary of Findings

| Finding | Severity | Detail |
|---------|----------|--------|
| **wikipedia_updates resets `created_at`** | 🐛 Bug | `DO UPDATE SET created_at = NOW()` instead of `updated_at = NOW()`. Destroys original insertion timestamp. Missing `updated_at` entirely. |
| **No orphan chunk cleanup** | ⚠️ Gap | When content shrinks (article edited shorter), old chunks at higher indices are never deleted. Orphans accumulate indefinitely. |
| **No content hashing** | ⚠️ Gap | No way to detect unchanged content and skip re-embedding. Every update re-embeds regardless of whether content changed. |
| **Unstable `hash()` for source_id** | 🐛 Bug | `ingest_rss.py` and `ingest_ssrn.py` use Python's `hash()` which is randomized per session. Breaks dedup. |
| **wikipedia_updates source_id format differs from bulk** | ⚠️ Inconsistency | Bulk uses `title`, updates use `title::idx`. May cause duplicate entries if both paths are used for the same article. |
| **`created_at` preserved in all other paths** | ✅ OK | All other `DO UPDATE` paths correctly omit `created_at`, preserving original insertion time. |
| **Unique constraint works correctly** | ✅ OK | `(source, source_id, chunk_index)` prevents exact duplicate rows. |
| **Skip vs Update strategies match sources.yaml** | ✅ OK | All 14 declared sources use the correct strategy from config. |
| **No cross-source dedup needed** | ✅ OK | Different sources for same topic is intentional design for retrieval diversity. |
