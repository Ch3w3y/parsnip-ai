-- Phase 1.2: Add retry_count to ingestion_jobs for transient/permanent error classification
-- Safe to re-run: uses IF NOT EXISTS guard.

ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;