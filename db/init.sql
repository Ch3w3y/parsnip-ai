-- Create Joplin Server's database (Joplin does not create it automatically)
SELECT 'CREATE DATABASE joplin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'joplin')\gexec

-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;

-- ── Knowledge chunks ──────────────────────────────────────────────────────────
-- Stores all knowledge base content: Wikipedia, arXiv, GitHub, news, etc.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,   -- 'wikipedia' | 'arxiv' | 'github' | 'news'
    source_id       TEXT        NOT NULL,   -- article title, DOI, repo slug, URL
    chunk_index     INTEGER     NOT NULL DEFAULT 0,
    content         TEXT        NOT NULL,
    content_hash    TEXT,                    -- SHA-256 hex of content; NULL for pre-existing rows
    metadata        JSONB       NOT NULL DEFAULT '{}',
    embedding       VECTOR(1024),
    embedding_model TEXT        NOT NULL DEFAULT 'mxbai-embed-large',
    user_id         TEXT,               -- NULL = org-wide; set = user-specific (Joplin notes only)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    UNIQUE (source, source_id, chunk_index)
);

-- DiskANN index: graph stored on disk, not RAM — handles 20M+ vectors
-- without needing 90GB+ RAM like HNSW would require at Wikipedia scale
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_idx
    ON knowledge_chunks
    USING diskann (embedding);

-- Full-text search index for hybrid retrieval
CREATE INDEX IF NOT EXISTS knowledge_chunks_fts_idx
    ON knowledge_chunks
    USING GIN (to_tsvector('english', content));

-- Metadata filter index (filter by source before vector search)
CREATE INDEX IF NOT EXISTS knowledge_chunks_source_idx
    ON knowledge_chunks (source, source_id);

-- Partial index for user-specific content (Joplin notes layer)
CREATE INDEX IF NOT EXISTS knowledge_chunks_user_id_idx
    ON knowledge_chunks (user_id)
    WHERE user_id IS NOT NULL;

-- ── Agent conversation state ──────────────────────────────────────────────────
-- NOTE: LangGraph checkpointer tables (checkpoints, checkpoint_blobs,
-- checkpoint_writes, checkpoint_migrations) are created automatically by
-- AsyncPostgresSaver.setup() at agent startup. Do NOT define them here —
-- the schema diverges between langgraph-checkpoint-postgres versions.

-- ── Ingestion progress tracking ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id              SERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
    total           INTEGER,
    processed       INTEGER     NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,                                  -- last error for failed jobs
    failed_count    INTEGER     NOT NULL DEFAULT 0,         -- per-job failure count
    duration_ms     INTEGER,                                -- auto-calculated: finished_at - started_at
    metadata        JSONB       NOT NULL DEFAULT '{}'
);

-- ── Agent long-term memory (4-layer stack inspired by MemPalace) ──────────────
-- L1: Essential story — auto-curated facts, decisions, preferences
-- L2: Topic recall — scoped retrieval by category
-- Agent writes here via save_memory; loaded at session start for context
CREATE TABLE IF NOT EXISTS agent_memories (
    id          BIGSERIAL PRIMARY KEY,
    category    TEXT        NOT NULL,   -- 'user_prefs' | 'facts' | 'decisions' | 'project_context' | 'people'
    content     TEXT        NOT NULL,
    importance  INTEGER     NOT NULL DEFAULT 1,  -- 1-5, higher = loaded earlier in L1
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ,
    deleted_at  TIMESTAMPTZ,
    metadata    JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS agent_memories_category_idx ON agent_memories (category);
CREATE INDEX IF NOT EXISTS agent_memories_importance_idx ON agent_memories (importance DESC);
CREATE INDEX IF NOT EXISTS agent_memories_fts_idx
    ON agent_memories USING GIN (to_tsvector('english', content));

-- ── Forex rates (structured data for analysis) ───────────────────────────────
-- Stores daily FX rates from Frankfurter API. Queryable directly by analysis scripts.
-- The KB also has text chunks (source='forex') for semantic search.
CREATE TABLE IF NOT EXISTS forex_rates (
    id          BIGSERIAL PRIMARY KEY,
    pair        TEXT        NOT NULL,   -- e.g. 'EUR/USD'
    base_ccy    TEXT        NOT NULL,   -- e.g. 'EUR'
    quote_ccy   TEXT        NOT NULL,   -- e.g. 'USD'
    rate        NUMERIC     NOT NULL,
    rate_date   DATE        NOT NULL,
    source      TEXT        NOT NULL DEFAULT 'frankfurter',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pair, rate_date)
);

CREATE INDEX IF NOT EXISTS forex_rates_pair_date_idx ON forex_rates (pair, rate_date);
CREATE INDEX IF NOT EXISTS forex_rates_date_idx ON forex_rates (rate_date);

-- ── World Bank macro indicators ──────────────────────────────────────────────
-- Stores key economic indicators for cross-referencing with forex data.
-- Analysis scripts query this directly: SELECT * FROM world_bank_data WHERE country_code='BRA'
CREATE TABLE IF NOT EXISTS world_bank_data (
    id              BIGSERIAL PRIMARY KEY,
    country_code    TEXT        NOT NULL,   -- ISO 3-letter (BRA, GBR, etc.)
    country_name    TEXT        NOT NULL,
    indicator_code  TEXT        NOT NULL,   -- NY.GDP.MKTP.CD, etc.
    indicator_name  TEXT        NOT NULL,
    year            INTEGER     NOT NULL,
    value           NUMERIC,
    unit            TEXT,
    source          TEXT        NOT NULL DEFAULT 'world_bank',
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (country_code, indicator_code, year)
);

CREATE INDEX IF NOT EXISTS wb_data_country_indicator_idx ON world_bank_data (country_code, indicator_code);
CREATE INDEX IF NOT EXISTS wb_data_indicator_year_idx ON world_bank_data (indicator_code, year);

-- ── Joplin replacement: normalized note tables ────────────────────────────────
-- Replaces Joplin's monolithic `items` table (separate `joplin` database)
-- with proper normalized tables in the main agent_kb database.
-- Key design decisions vs Joplin:
--   • UUID PKs with gen_random_uuid() (matching Joplin's convention)
--   • TIMESTAMPTZ instead of BIGINT millisecond timestamps
--   • Plain TEXT markdown content instead of JSON bytea
--   • Soft delete via deleted_at instead of Joplin's deleted_time integer
--   • Proper foreign keys with ON DELETE CASCADE / SET NULL
--   • GIN full-text search index on content

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Notebooks (hierarchical folders) ──────────────────────────────────────────
-- Replaces Joplin items where jop_type = 2 (notebook)
CREATE TABLE IF NOT EXISTS notebooks (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT        NOT NULL,
    parent_id   UUID        REFERENCES notebooks(id) ON DELETE SET NULL,
    "order"     INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS notebooks_parent_id_idx ON notebooks (parent_id);
CREATE INDEX IF NOT EXISTS notebooks_title_idx     ON notebooks (title);

-- ── Notes ─────────────────────────────────────────────────────────────────────
-- Replaces Joplin items where jop_type = 1 (note) and jop_type = 5 (todo)
CREATE TABLE IF NOT EXISTS notes (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    notebook_id     UUID        REFERENCES notebooks(id) ON DELETE SET NULL,
    is_todo         BOOLEAN     NOT NULL DEFAULT FALSE,
    todo_completed  BOOLEAN     NOT NULL DEFAULT FALSE,
    source_url      TEXT,
    author          TEXT,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS notes_notebook_id_idx  ON notes (notebook_id);
CREATE INDEX IF NOT EXISTS notes_created_at_idx   ON notes (created_at DESC);
CREATE INDEX IF NOT EXISTS notes_updated_at_idx   ON notes (updated_at DESC);
CREATE INDEX IF NOT EXISTS notes_active_idx
    ON notes (id, title, notebook_id, updated_at)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS notes_deleted_at_idx
    ON notes (deleted_at)
    WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS notes_content_fts_idx
    ON notes USING GIN (to_tsvector('english', content));

-- ── Tags ──────────────────────────────────────────────────────────────────────
-- Replaces Joplin items where jop_type = 17 (tag)
CREATE TABLE IF NOT EXISTS tags (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS tags_name_uidx ON tags (name);

-- ── Note ↔ Tag junction ──────────────────────────────────────────────────────
-- Replaces Joplin items where jop_type = 6 (note_tag link)
CREATE TABLE IF NOT EXISTS note_tags (
    note_id     UUID        NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id      UUID        NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

CREATE INDEX IF NOT EXISTS note_tags_tag_id_idx ON note_tags (tag_id);

-- ── Note resources (attachments) ─────────────────────────────────────────────
-- Replaces Joplin items where jop_type = 4 (resource)
CREATE TABLE IF NOT EXISTS note_resources (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id     UUID        NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    filename    TEXT        NOT NULL,
    mime_type   TEXT,
    content     BYTEA,
    size        INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS note_resources_note_id_idx ON note_resources (note_id);

-- ── HITL (Human-in-the-Loop) sessions ─────────────────────────────────────────
-- Tracks iterative LLM↔human review cycles for note content generation
CREATE TABLE IF NOT EXISTS hitl_sessions (
    id              SERIAL      PRIMARY KEY,
    note_id         UUID        NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    last_llm_content TEXT       NOT NULL,
    last_llm_hash   TEXT        NOT NULL,
    cycle_count     INTEGER     NOT NULL DEFAULT 0,
    status          TEXT        NOT NULL DEFAULT 'generated',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hitl_sessions_note_id_idx ON hitl_sessions (note_id);
CREATE INDEX IF NOT EXISTS hitl_sessions_status_idx  ON hitl_sessions (status);

-- ── Thread metadata (caches titles for fast thread listing) ──────────────────
-- Avoids slow aget_state() per thread — titles are extracted once on first load
-- and served from this table for instant /threads responses.
CREATE TABLE IF NOT EXISTS thread_metadata (
    thread_id   TEXT        PRIMARY KEY,
    title       TEXT        NOT NULL DEFAULT '',
    message_count INTEGER   NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
