# F3: Schema Integrity Check Findings

## 1. Column Presence Verification (init.sql vs code)

### knowledge_chunks table
All columns in `init.sql` are used in code:

| Column | Schema Type | Schema Constraint | Code Status |
|--------|-------------|-------------------|-------------|
| `id` | BIGSERIAL | PK | Auto-generated ✅ |
| `source` | TEXT | NOT NULL | Always set ✅ |
| `source_id` | TEXT | NOT NULL | Always set ✅ |
| `chunk_index` | INTEGER | NOT NULL DEFAULT 0 | Always set ✅ |
| `content` | TEXT | NOT NULL | Always set ✅ |
| `metadata` | JSONB | NOT NULL DEFAULT '{}' | Always set ✅ |
| `embedding` | VECTOR(1024) | nullable | **See issue below** |
| `embedding_model` | TEXT | NOT NULL DEFAULT 'mxbai-embed-large' | **See issue below** |
| `user_id` | TEXT | nullable | **See issue below** |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Auto ✅ |
| `updated_at` | TIMESTAMPTZ | nullable | Set on upsert ✅ |

### Other tables (all verified)
- `ingestion_jobs`: all columns used ✅
- `agent_memories`: all columns match code ✅
- `forex_rates`: all columns used ✅
- `world_bank_data`: all columns used ✅
- `notebooks`, `notes`, `tags`, `note_tags`, `note_resources`: migration matches init.sql ✅
- `hitl_sessions`, `thread_metadata`: schema matches code ✅

---

## 2. NOT NULL Column Violations (NULL values where NOT NULL)

### ⚠️ ISSUE: `embedding_model` column NOT properly set by 3 ingestion paths

The schema defines `embedding_model TEXT NOT NULL DEFAULT 'mxbai-embed-large'`.

**Two code paths correctly set `embedding_model`:**
- `bulk_upsert_chunks()` in `utils.py` — passes 7-tuple including `embedding_model` ✅
- Wikipedia ingestion — passes `"mxbai-embed-large"` via `bulk_upsert_chunks` ✅
- HackerNews, SSNR, RSS — pass `"mxbai-embed-large"` explicitly ✅
- GitHub — passes `EMBED_MODEL` (which is `bge-m3`) ✅
- World Bank — passes `EMBED_MODEL` ✅
- Forex — passes `EMBED_MODEL` ✅

**Three code paths DO NOT set `embedding_model` — they rely on the column DEFAULT:**
1. `upsert_chunks()` in `utils.py` — INSERT statement omits `embedding_model` column ❌
2. `save_note()` in `agent/tools/notes.py` — INSERT omits `embedding_model` ❌
3. `ingest_joplin.py` — INSERT omits `embedding_model` ❌
4. `pdf_ingest.py` — INSERT omits `embedding_model` ❌

**Impact:** These 4 paths rely on the `DEFAULT 'mxbai-embed-large'`. For `save_note` and `pdf_ingest`, this is correct (they use mxbai-embed-large). For Joplin, also correct (uses mxbai). However, this is **fragile** — if someone changes the default or the embedding model for these paths, rows could get the wrong model tag.

**Verdict:** No data corruption currently, but the inconsistency is a maintenance risk. The `upsert_chunks()` helper should include `embedding_model` like `bulk_upsert_chunks()` does.

### ⚠️ `agent_memories.metadata` is NEVER written

The `save_memory` tool in `agent/tools/memory.py` does:
```sql
INSERT INTO agent_memories (category, content, importance) VALUES (%s, %s, %s)
```
It omits `metadata` from the INSERT entirely. The schema has `metadata JSONB NOT NULL DEFAULT '{}'`, so rows get `{}`, but the field is **never populated with any data**.

**Impact:** Low currently — no code reads from `agent_memories.metadata`. But it's dead weight. The column could be documented as reserved/unused, or future features should actually populate it.

### ⚠️ `user_id` is NULL for all sources except Joplin

Only `ingest_joplin.py` writes `user_id` (the Joplin user_id). All other 12+ sources leave it NULL. This is **by design** (comment in schema: "NULL = org-wide; set = user-specific (Joplin notes only)").

**Verdict:** Working as intended ✅

---

## 3. Embedding Dimension Verification (VECTOR(1024))

### Model dimensions confirmed:
| Model | Dimensions | Fits VECTOR(1024)? |
|-------|-----------|--------------------|
| `mxbai-embed-large` | 1024 | ✅ Yes |
| `bge-m3` | 1024 | ✅ Yes |

**Sources and their models:**
- Wikipedia → `mxbai-embed-large` (1024) ✅
- arXiv → `mxbai-embed-large` (via `upsert_chunks`, default) ✅
- bioRxiv → `mxbai-embed-large` (via `upsert_chunks`, default) ✅
- news_api → `mxbai-embed-large` (via `upsert_chunks`, default) ✅
- news (RSS) → `mxbai-embed-large` (explicit in row tuple) ✅
- hackernews → `mxbai-embed-large` (explicit in row tuple) ✅
- pubmed → `mxbai-embed-large` (via `upsert_chunks`, default) ✅
- ssrn → `mxbai-embed-large` (explicit in row tuple) ✅
- joplin_notes → `mxbai-embed-large` (via Joplin's own embed function) ✅
- user_notes → `mxbai-embed-large` (via `get_embedding()`) ✅
- user_docs (PDF) → `mxbai-embed-large` (via `_embed_batch()`) ✅
- github → `bge-m3` (1024 dims) ✅
- forex → `mxbai-embed-large` (1024 dims) ✅
- world_bank → `mxbai-embed-large` (1024 dims) ✅

**reembed_chunks.py:** Uses `EMBED_MODEL` env var (defaults to `mxbai-embed-large`). Casts to `::vector` which validates dimensions against the column type. ✅

**kb_search.py and router.py:** `SOURCE_MODEL_MAP = {"github": "bge-m3"}` with default `mxbai-embed-large`. Both 1024 dims. ✅

**Verdict:** All embedding models produce 1024-dim vectors. Schema is correct. ✅

---

## 4. Metadata JSONB Key Consistency

### Per-source metadata key inventory:

| Source | Metadata Keys |
|--------|---------------|
| `wikipedia` | `url`, `wiki_id` |
| `wikipedia_updates` | `url`, `wiki_id` |
| `arxiv` | `title`, `published`, `authors` (list), `link`, `category` |
| `biorxiv` | `title`, `doi`, `authors`, `category`, `published`, `url`, `server` |
| `news` (NewsAPI) | `title`, `url`, `source` (news org name), `published` |
| `news` (RSS) | `title`, `url`, `feed`, `published` |
| `github` | `language`, `repo`, `file_path`, `stars`, `url`, `total_chunks`, `file_type` |
| `hackernews` | `hn_id`, `title`, `url`, `author`, `score`, `date`, `source` (always "hackernews") |
| `pubmed` | `title`, `journal`, `year`, `authors`, `link`, `search_term` |
| `ssrn` | `title`, `link`, `authors`, `date`, `category` |
| `joplin_notes` | `title`, `note_id`, `notebook_id`, `notebook`, `url`, `updated_time_ms` |
| `user_notes` | `title`, `saved_at` |
| `user_docs` | `filename`, `pages`, `source_id` |
| `forex` | `pair`, `base`, `quote`, `rate`, `date`, `days`, `url` |
| `world_bank` | `country_code`, `country_name`, `latest_year`, `indicators`, `url` |

### Inconsistencies found:

1. **`url` key is inconsistent across sources:**
   - `arxiv` uses `link` instead of `url` ⚠️
   - `biorxiv` has both `url` and `doi` (ok, they're different) ✅
   - `pubmed` uses `link` instead of `url` ⚠️
   - `ssrn` uses `link` instead of `url` ⚠️
   - `hackernews` uses both `url` and `source` ⚠️ (redundant `source` key — always "hackernews")

2. **Date key naming inconsistency:**
   - `arxiv`: `published`
   - `biorxiv`: `published`
   - `news` (NewsAPI): `published`
   - `news` (RSS): `published`
   - `hackernews`: `date`
   - `ssrn`: `date`
   - `pubmed`: `year`
   - `forex`: `date`
   - `world_bank`: `latest_year`
   - `joplin_notes`: `updated_time_ms`

3. **Author key naming:**
   - `arxiv`, `pubmed`: `authors` (list) ✅
   - `biorxiv`: `authors` (string from API) ⚠️ (sometimes list, sometimes string)
   - `ssrn`: `authors` (list from API) ✅
   - `hackernews`: `author` (single string) ✅ (correct — a single author)

4. **`hackernews` has redundant `source` key in metadata:**
   Already stored in the `source` column. Minor redundancy. ⚠️

5. **`news` source name collision:**
   Both NewsAPI and RSS feeds write to `source='news'` but with slightly different metadata shapes (NewsAPI has `source` key for news org name, RSS has `feed` key for feed name). The `source` DB column disambiguates, but metadata structure differs within same source value.

---

## 5. Summary of Issues

### Critical (data integrity risk):
1. **`upsert_chunks()` omits `embedding_model` column** — 4 code paths rely on DEFAULT value. If the default ever changes or a path uses a different model, data would be mislabeled.

### Moderate (consistency/maintenance):
2. **Metadata key `url` vs `link` inconsistency** — `arxiv`, `pubmed`, `ssrn` use `link` while all others use `url`. Makes querying across sources harder.
3. **Date key inconsistency** — `published` vs `date` vs `year` vs `updated_time_ms` across sources.
4. **`agent_memories.metadata` is never written** — column exists but is unused `DEFAULT '{}'`.
5. **`hackernews` metadata has redundant `source: "hackernews"` key** that duplicates the `source` column.

### Low (by design, working correctly):
6. **`user_id` is NULL for all sources except joplin** — Working as designed.
7. **VECTOR(1024)** — All embedding models produce 1024-dim vectors. ✅
8. **Column presence** — All init.sql columns are present and used. ✅
