CREATE TABLE IF NOT EXISTS failed_records (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,
    source_id       TEXT        NOT NULL,
    content         TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    error_message   TEXT,
    error_class     TEXT,
    retry_count     INTEGER     NOT NULL DEFAULT 0,
    status          TEXT        NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    last_retry_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS failed_records_source_status_idx ON failed_records(source, status);