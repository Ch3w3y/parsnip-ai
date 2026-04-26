# Notepad
<!-- Auto-managed by OMC. Manual edits preserved in MANUAL section. -->

## Priority Context
<!-- ALWAYS loaded. Keep under 500 chars. Critical discoveries only. -->
Backup pipeline: D2 upload running (75.2GiB, 5MiB/s, ~4.3h ETA). D1 pgBackRest pending after D2. D3 done. D5 dry-run pending. D6 commit pending. Phase5 validation pending.

## Working Memory
<!-- Session notes. Auto-pruned after 7 days. -->
### 2026-04-25 21:44
## Database Schema Documentation — parsnip-ai

### ORM/Framework Summary
- **No ORM** (no SQLAlchemy, Django ORM, Prisma, etc.)
- **Raw SQL** via `psycopg` (async) and `psycopg_pool.AsyncConnectionPool`
- **pgvector** extension for vector embeddings (`VECTOR(1024)`)
- **vectorscale** extension for DiskANN indexes
- **Pydantic** used for config (`Settings`) and API response models (`BaseModel`), NOT for DB schemas
- Database: **PostgreSQL** (accessed via `DATABASE_URL` env var, database `agent_kb`)

### Schema Definition Files
1. `/home/daryn/parsnip/db/init.sql` — Primary DDL (all tables)
2. `/home/daryn/parsnip/db/migrations/001_notes_schema.sql` — Notes migration (notebooks, notes, tags, note_tags, note_resources, hitl_sessions, thread_metadata)
3. `/home/daryn/parsnip/agent/tools/joplin_hitl.py` — Legacy `joplin_hitl_sessions` DDL (inline, Joplin DB)
4. `/home/daryn/parsnip/scripts/add_performance_indexes.py` — Additional indexes (post-migration)

### Pydantic Models (not DB, but relevant)
- `agent/config.py`: `Settings(BaseSettings)` — config validation
- `agent/admin_routes.py`: `ServiceHealth`, `StackHealthResponse`, `BackupEntry`, etc. — API response models
- `agent/graph_state.py`: `AgentState(TypedDict)` — LangGraph state (no DB persistence)
- `ingestion/registry.py`: `SourceEntry(dataclass)` — ingestion source config
- `agent/ingestion_status.py`: `MigrationStatus`, `BulkIngestStatus`, `IngestionOverview` — status dataclasses

---

### TABLE 1: knowledge_chunks
**Purpose**: Stores all knowledge base content (Wikipedia, arXiv, GitHub, news, etc.)
**File**: `db/init.sql` lines 11-24

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | Auto-increment |
| source | TEXT | NOT NULL | 'wikipedia', 'arxiv', 'github', 'news', 'forex', 'world_bank', 'user_notes', 'user_docs', etc. |
| source_id | TEXT | NOT NULL | Article title, DOI, repo slug, URL, or `note_uuid::chunk_0` |
| chunk_index | INTEGER | NOT NULL DEFAULT 0 | Word-count chunk index within document |
| content | TEXT | NOT NULL | Chunked text content |
| metadata | JSONB | NOT NULL DEFAULT '{}' | Source-specific metadata |
| embedding | VECTOR(1024) | nullable | pgvector embedding |
| embedding_model | TEXT | NOT NULL DEFAULT 'mxbai-embed-large' | Model used for embedding |
| user_id | TEXT | nullable | NULL = org-wide, set for Joplin notes only |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | nullable | |

**Constraints**: UNIQUE (source, source_id, chunk_index)
**Indexes**:
- `knowledge_chunks_embedding_idx` — DiskANN on embedding
- `knowledge_chunks_fts_idx` — GIN on to_tsvector('english', content)
- `knowledge_chunks_source_idx` — (source, source_id)
- `knowledge_chunks_user_id_idx` — Partial on user_id WHERE NOT NULL
- `knowledge_chunks_source_created_idx` — (source, created_at DESC) [added by script]
- `knowledge_chunks_created_at_idx` — (created_at DESC) [added by script]
- `knowledge_chunks_has_embedding_idx` — Partial on source WHERE embedding IS NOT NULL [added by script]

---

### TABLE 2: ingestion_jobs
**Purpose**: Track ingestion job progress and resumability
**File**: `db/init.sql` lines 53-62

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PRIMARY KEY | |
| source | TEXT | NOT NULL | Matches knowledge_chunks.source values |
| status | TEXT | NOT NULL DEFAULT 'pending' | pending, running, done, failed |
| total | INTEGER | nullable | Total items to process |
| processed | INTEGER | NOT NULL DEFAULT 0 | Items completed |
| started_at | TIMESTAMPTZ | nullable | |
| finished_at | TIMESTAMPTZ | nullable | |
| metadata | JSONB | NOT NULL DEFAULT '{}' | |

**Indexes**: `ingestion_jobs_source_status_started_idx` — (source, status, started_at DESC) [added by script]

---

### TABLE 3: agent_memories
**Purpose**: Long-term memory (4-layer stack, L1 essential facts loaded at session start)
**File**: `db/init.sql` lines 68-82

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | |
| category | TEXT | NOT NULL | 'user_prefs', 'facts', 'decisions', 'project_context', 'people' |
| content | TEXT | NOT NULL | |
| importance | INTEGER | NOT NULL DEFAULT 1 | 1-5 scale |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | nullable | |
| deleted_at | TIMESTAMPTZ | nullable | Soft delete |
| metadata | JSONB | NOT NULL DEFAULT '{}' | |

**Indexes**:
- `agent_memories_category_idx` — (category)
- `agent_memories_importance_idx` — (importance DESC)
- `agent_memories_fts_idx` — GIN on to_tsvector('english', content)
- `agent_memories_active_importance_created_idx` — Partial (importance DESC, created_at DESC) WHERE deleted_at IS NULL [script]
- `agent_memories_active_category_importance_idx` — Partial (category, importance DESC) WHERE deleted_at IS NULL [script]

---

### TABLE 4: forex_rates
**Purpose**: Structured FX rate data for analysis queries
**File**: `db/init.sql` lines 85-100

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | |
| pair | TEXT | NOT NULL | e.g. 'EUR/USD' |
| base_ccy | TEXT | NOT NULL | e.g. 'EUR' |
| quote_ccy | TEXT | NOT NULL | e.g. 'USD' |
| rate | NUMERIC | NOT NULL | |
| rate_date | DATE | NOT NULL | |
| source | TEXT | NOT NULL DEFAULT 'frankfurter' | |
| fetched_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Constraints**: UNIQUE (pair, rate_date)
**Indexes**: `forex_rates_pair_date_idx` — (pair, rate_date), `forex_rates_date_idx` — (rate_date)

---

### TABLE 5: world_bank_data
**Purpose**: World Bank macro indicators for analysis
**File**: `db/init.sql` lines 105-120

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | |
| country_code | TEXT | NOT NULL | ISO 3-letter |
| country_name | TEXT | NOT NULL | |
| indicator_code | TEXT | NOT NULL | e.g. NY.GDP.MKTP.CD |
| indicator_name | TEXT | NOT NULL | |
| year | INTEGER | NOT NULL | |
| value | NUMERIC | nullable | |
| unit | TEXT | nullable | |
| source | TEXT | NOT NULL DEFAULT 'world_bank' | |
| fetched_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Constraints**: UNIQUE (country_code, indicator_code, year)
**Indexes**: `wb_data_country_indicator_idx` — (country_code, indicator_code), `wb_data_indicator_year_idx` — (indicator_code, year)

---

### TABLE 6: notebooks
**Purpose**: Hierarchical note folders (replaces Joplin items where jop_type=2)
**File**: `db/migrations/001_notes_schema.sql`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY DEFAULT gen_random_uuid() | |
| title | TEXT | NOT NULL | |
| parent_id | UUID | REFERENCES notebooks(id) ON DELETE SET NULL | Self-referential FK |
| "order" | INTEGER | NOT NULL DEFAULT 0 | Quoted (reserved word) |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Indexes**: `notebooks_parent_id_idx` — (parent_id), `notebooks_title_idx` — (title)

---

### TABLE 7: notes
**Purpose**: Notes and todos (replaces Joplin items where jop_type=1 or 5)
**File**: `db/migrations/001_notes_schema.sql`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY DEFAULT gen_random_uuid() | |
| title | TEXT | NOT NULL | |
| content | TEXT | NOT NULL | Markdown (not JSON bytea) |
| notebook_id | UUID | REFERENCES notebooks(id) ON DELETE SET NULL | |
| is_todo | BOOLEAN | NOT NULL DEFAULT FALSE | |
| todo_completed | BOOLEAN | NOT NULL DEFAULT FALSE | |
| source_url | TEXT | nullable | Original URL if clipped |
| author | TEXT | nullable | Author attribution |
| deleted_at | TIMESTAMPTZ | nullable | Soft delete |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Indexes**:
- `notes_notebook_id_idx` — (notebook_id)
- `notes_created_at_idx` — (created_at DESC)
- `notes_updated_at_idx` — (updated_at DESC)
- `notes_active_idx` — Partial (id, title, notebook_id, updated_at) WHERE deleted_at IS NULL
- `notes_deleted_at_idx` — Partial (deleted_at) WHERE deleted_at IS NOT NULL
- `notes_content_fts_idx` — GIN on to_tsvector('english', content)

---

### TABLE 8: tags
**Purpose**: Tags for notes (replaces Joplin items where jop_type=17)
**File**: `db/migrations/001_notes_schema.sql`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY DEFAULT gen_random_uuid() | |
| name | TEXT | NOT NULL UNIQUE | |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Indexes**: `tags_name_uidx` — UNIQUE on name

---

### TABLE 9: note_tags
**Purpose**: Many-to-many junction between notes and tags
**File**: `db/migrations/001_notes_schema.sql`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| note_id | UUID | NOT NULL REFERENCES notes(id) ON DELETE CASCADE | |
| tag_id | UUID | NOT NULL REFERENCES tags(id) ON DELETE CASCADE | |

**Constraints**: PRIMARY KEY (note_id, tag_id)
**Indexes**: `note_tags_tag_id_idx` — (tag_id)

---

### TABLE 10: note_resources
**Purpose**: Binary attachments for notes (replaces Joplin items where jop_type=4)
**File**: `db/migrations/001_notes_schema.sql`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY DEFAULT gen_random_uuid() | |
| note_id | UUID | NOT NULL REFERENCES notes(id) ON DELETE CASCADE | |
| filename | TEXT | NOT NULL | |
| mime_type | TEXT | nullable | Auto-detected from extension |
| content | BYTEA | nullable | Binary blob |
| size | INTEGER | nullable | Size in bytes |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Indexes**: `note_resources_note_id_idx` — (note_id)

---

### TABLE 11: hitl_sessions
**Purpose**: Human-in-the-Loop review tracking for LLM-generated notes
**File**: `db/init.sql` lines 211-225

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PRIMARY KEY | |
| note_id | UUID | NOT NULL REFERENCES notes(id) ON DELETE CASCADE | |
| last_llm_content | TEXT | NOT NULL | Most recent LLM output |
| last_llm_hash | TEXT | NOT NULL | SHA-256 hash (truncated to 16 chars in app) |
| cycle_count | INTEGER | NOT NULL DEFAULT 0 | |
| status | TEXT | NOT NULL DEFAULT 'generated' | generated, reviewed, approved, rejected |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Indexes**: `hitl_sessions_note_id_idx` — (note_id), `hitl_sessions_status_idx` — (status)

---

### TABLE 12: thread_metadata
**Purpose**: Caches thread titles for fast listing (avoids slow aget_state calls)
**File**: `db/migrations/001_notes_schema.sql`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| thread_id | TEXT | PRIMARY KEY | |
| title | TEXT | NOT NULL DEFAULT '' | |
| message_count | INTEGER | NOT NULL DEFAULT 0 | |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

---

### TABLE 13: joplin_hitl_sessions (LEGACY)
**Purpose**: Legacy HITL session table (uses Joplin DB, BIGINT timestamps)
**File**: `agent/tools/joplin_hitl.py` (inline DDL)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PRIMARY KEY | |
| note_id | TEXT | NOT NULL | Joplin note ID (not UUID) |
| last_llm_content | TEXT | NOT NULL | |
| last_llm_hash | TEXT | NOT NULL | |
| cycle_count | INTEGER | NOT NULL DEFAULT 0 | |
| status | TEXT | NOT NULL DEFAULT 'generated' | |
| created_at | BIGINT | NOT NULL | Joplin millisecond timestamps |
| updated_at | BIGINT | NOT NULL | Joplin millisecond timestamps |

**This is LEGACY** — uses `## Working Memory
`-style params (psycopg2/asyncpg) instead of `%s` (psycopg3). Uses Joplin's `joplin` pool. Being replaced by `hitl_sessions` in `agent_kb`.

### LangGraph Checkpoint Tables
Created automatically by `AsyncPostgresSaver.setup()` at agent startup. Schema is managed by `langgraph-checkpoint-postgres` and **NOT defined in init.sql** (explicitly noted):
- `checkpoints`
- `checkpoint_blobs`
- `checkpoint_writes`
- `checkpoint_migrations`

---

## SCHEMA DRIFT ANALYSIS

### Confirmed Drift Issues

1. **`joplin_hitl_sessions` vs `hitl_sessions`** — TWO HITL tables exist:
   - Legacy `joplin_hitl_sessions` in `joplin` DB (BIGINT timestamps, `note_id TEXT`)
   - New `hitl_sessions` in `agent_kb` DB (TIMESTAMPTZ, `note_id UUID REFERENCES notes(id)`)
   - Both `agent/tools/joplin_hitl.py` and `agent/tools/hitl.py` exist with parallel implementations
   - **DRIFT**: `joplin_hitl.py` uses `## Working Memory
`-style bind params (asyncpg/psycopg2) while `hitl.py` uses `%s`-style (psycopg3)

2. **`embedding_model` column** — Added to `knowledge_chunks` in `bulk_upsert_chunks()` but NOT explicitly in `init.sql` table DDL. The DDL says `embedding_model TEXT NOT NULL DEFAULT 'mxbai-embed-large'` so it IS defined, but the column was likely added via ALTER after initial deployment (or init.sql was updated after initial creation).

3. **`user_id` column in knowledge_chunks** — Present in `init.sql` but NOT written by any current ingestion pipeline. Only used by the Joplin ingestion pipeline for user-specific notes. No schema-level enforcement that `user_id` is set for Joplin-sourced content.

4. **`thread_metadata` table** — Defined in migration `001_notes_schema.sql` but NOT populated by any visible pipeline code. Likely populated by the agent's conversation handling code (not in the ingestion pipeline).

5. **Wikipedia source_id migration** — `scripts/migrate_wiki_source_ids.py` normalizes `source_id` from `"ArticleTitle::chunk_index"` format to separate `source_id` and `chunk_index` columns. This is a running migration that modifies data in place, suggesting the original ingestion used `source_id` to encode both article name AND chunk position. The current init.sql DDL has `chunk_index` as a proper column, so new ingests won't have this problem, but legacy data needs migration.

6. **`notes.deleted_at` vs search** — `notes_pg.py`'s `joplin_delete_note()` soft-deletes notes by setting `deleted_at`, then updates `knowledge_chunks.updated_at` for matching `user_notes` source. But it does NOT remove the knowledge_chunk row, meaning soft-deleted notes remain searchable in the KB.

### No-Drift Confirmed

- **forex_rates** and **world_bank_data** ingestion scripts write exactly the columns defined in the schema.
- **agent_memories** tool code writes exactly the columns in the schema.
- **knowledge_chunks** `bulk_upsert_chunks()` writes all columns defined in the schema.

---

## NORMALIZATION vs DENORMALIZATION PATTERNS

### Normalized (3NF)
- **notes ↔ tags** via `note_tags` junction table (proper M:N)
- **notes ↔ notebooks** via `notebook_id` FK (proper M:1)
- **note_resources** separate from notes (proper 1:N)
- **forex_rates** separate from `knowledge_chunks` (structured data stored independently, with KB text chunks for semantic search)
- **world_bank_data** separate from `knowledge_chunks` (same pattern)
- **hitl_sessions** FK to `notes(id)` with ON DELETE CASCADE

### Denormalized (Intentional)
- **knowledge_chunks.metadata** (JSONB) — denormalized source-specific metadata (repo stars, article URLs, forex rates, etc.). Each source has different metadata structure stored in the same JSONB column.
- **knowledge_chunks** stores both the text content AND the embedding AND metadata in the same row — intentional for retrieval performance (single-row vector + FTS + metadata filter access).
- **forex_rates** AND `knowledge_chunks` (source='forex') — data is stored in BOTH structured tabular form AND as text+embedding chunks. The structured table is for direct SQL queries; the KB chunks are for semantic search. This is intentional dual-storage.
- **world_bank_data** AND `knowledge_chunks` (source='world_bank') — same dual-storage pattern.
- **hitl_sessions.last_llm_content** — stores the full LLM output text, duplicating what may also be in the notes table. Needed for content-hash comparison.
- **ingestion_jobs.metadata** (JSONB) — denormalized job parameter storage.

---

## MIGRATION & SCHEMA VERSIONING

### Approach
- **No formal migration framework** (no Alembic, no Django migrations, no Flyway)
- Manual SQL scripts in `/db/migrations/`
- Only one migration file: `001_notes_schema.sql`
- Schema versioning is implicit — `init.sql` is the canonical full schema
- `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` make scripts idempotent
- Performance indexes are added via standalone script: `scripts/add_performance_indexes.py`
- Data migrations are standalone Python scripts: `scripts/migrate_wiki_source_ids.py`
- LangGraph checkpoint tables are auto-created by `AsyncPostgresSaver.setup()`

### Migration Files
1. `/db/init.sql` — Full schema DDL (all tables, indexes, extensions)
2. `/db/migrations/001_notes_schema.sql` — Notes subsystem tables (notebooks, notes, tags, note_tags, note_resources, hitl_sessions, thread_metadata)
3. `/scripts/add_performance_indexes.py` — Additional performance indexes (CONCURRENT IF NOT EXISTS)
4. `/scripts/migrate_wiki_source_ids.py` — Data migration for Wikipedia source_id normalization


## MANUAL
<!-- User content. Never auto-pruned. -->

