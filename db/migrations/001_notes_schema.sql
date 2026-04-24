-- ═══════════════════════════════════════════════════════════════════════════════
-- Migration: 001_notes_schema.sql
-- Purpose:   Replace Joplin's monolithic `items` table with normalized tables
--            in the main agent_kb database. All note-related data now lives
--            alongside knowledge_chunks, agent_memories, etc.
--
-- Key design decisions vs Joplin:
--   • UUID PKs everywhere (matching Joplin's convention) with gen_random_uuid()
--   • TIMESTAMPTZ instead of BIGINT millisecond timestamps
--   • Plain TEXT markdown content instead of JSON bytea
--   • Soft delete via deleted_at instead of Joplin's deleted_time integer
--   • Proper foreign keys with ON DELETE CASCADE / SET NULL
--   • GIN full-text search index on content
-- ═══════════════════════════════════════════════════════════════════════════════

-- Enable gen_random_uuid() (requires pgcrypto; already available in PG 13+)
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
    content         TEXT        NOT NULL,          -- plain markdown, not JSON bytea
    notebook_id     UUID        REFERENCES notebooks(id) ON DELETE SET NULL,
    is_todo         BOOLEAN     NOT NULL DEFAULT FALSE,
    todo_completed  BOOLEAN     NOT NULL DEFAULT FALSE,
    source_url      TEXT,                           -- original URL if clipped
    author          TEXT,                           -- author attribution
    deleted_at      TIMESTAMPTZ,                    -- soft delete; NULL = active
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Core lookup indexes
CREATE INDEX IF NOT EXISTS notes_notebook_id_idx  ON notes (notebook_id);
CREATE INDEX IF NOT EXISTS notes_created_at_idx   ON notes (created_at DESC);
CREATE INDEX IF NOT EXISTS notes_updated_at_idx   ON notes (updated_at DESC);

-- Partial index: active (non-deleted) notes only
CREATE INDEX IF NOT EXISTS notes_active_idx
    ON notes (id, title, notebook_id, updated_at)
    WHERE deleted_at IS NULL;

-- Soft-delete partial index for quick "find trashed" queries
CREATE INDEX IF NOT EXISTS notes_deleted_at_idx
    ON notes (deleted_at)
    WHERE deleted_at IS NOT NULL;

-- Full-text search GIN index on content
CREATE INDEX IF NOT EXISTS notes_content_fts_idx
    ON notes USING GIN (to_tsvector('english', content));

-- ── Tags ──────────────────────────────────────────────────────────────────────
-- Replaces Joplin items where jop_type = 17 (tag)
CREATE TABLE IF NOT EXISTS tags (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- name already has UNIQUE index from constraint, but add explicit for clarity
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
    content     BYTEA,                          -- binary blob for images, PDFs, etc.
    size        INTEGER,                        -- bytes
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS note_resources_note_id_idx ON note_resources (note_id);

-- ── HITL (Human-in-the-Loop) sessions ─────────────────────────────────────────
-- Tracks iterative LLM↔human review cycles for note content generation
CREATE TABLE IF NOT EXISTS hitl_sessions (
    id              SERIAL      PRIMARY KEY,
    note_id         UUID        NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    last_llm_content TEXT       NOT NULL,          -- most recent LLM output
    last_llm_hash   TEXT        NOT NULL,          -- SHA-256 of last_llm_content
    cycle_count     INTEGER     NOT NULL DEFAULT 0,
    status          TEXT        NOT NULL DEFAULT 'generated',
                                                    -- generated | reviewed | approved | rejected
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hitl_sessions_note_id_idx ON hitl_sessions (note_id);
CREATE INDEX IF NOT EXISTS hitl_sessions_status_idx  ON hitl_sessions (status);

-- Thread metadata: caches titles for fast thread listing (avoids 50x aget_state calls)
CREATE TABLE IF NOT EXISTS thread_metadata (
    thread_id   TEXT        PRIMARY KEY,
    title       TEXT        NOT NULL DEFAULT '',
    message_count INTEGER   NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);