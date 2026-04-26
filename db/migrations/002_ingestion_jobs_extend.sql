-- Phase 1.1: Extend ingestion_jobs with error tracking and duration columns
-- Safe to re-run: uses IF NOT EXISTS / IF NOT NULL guards where possible.

ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS failed_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

-- Backfill duration_ms for any already-finished jobs where started_at and finished_at exist
UPDATE ingestion_jobs
SET duration_ms = EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000
WHERE finished_at IS NOT NULL
  AND started_at IS NOT NULL
  AND duration_ms IS NULL;