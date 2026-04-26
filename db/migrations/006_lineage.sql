-- OpenLineage-lite: add lineage_events table for data flow tracking
-- Safe to re-run: uses IF NOT EXISTS guard.

CREATE TABLE IF NOT EXISTS lineage_events (
    id              SERIAL      PRIMARY KEY,
    run_id          TEXT        NOT NULL,
    source          TEXT        NOT NULL,
    job_id          INTEGER     REFERENCES ingestion_jobs(id),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    input_type      TEXT,
    output_type     TEXT,
    record_count    INTEGER,
    schema_version  TEXT,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS lineage_events_source_timestamp_idx
    ON lineage_events (source, timestamp);