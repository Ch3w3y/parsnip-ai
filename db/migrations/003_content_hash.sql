-- Phase 2: Add content_hash column to knowledge_chunks for data integrity
-- Safe to re-run: uses IF NOT EXISTS guard.

ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;