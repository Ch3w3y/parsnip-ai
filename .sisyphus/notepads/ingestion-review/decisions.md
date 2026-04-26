## Orphan Chunk Cleanup - Architecture Decisions

### Function: `cleanup_orphan_chunks(conn, source, source_id, new_chunk_count)`
- Location: `ingestion/utils.py`
- Pattern: DELETE FROM knowledge_chunks WHERE source=%s AND source_id=%s AND chunk_index >= %s
- Parameterized query to prevent SQL injection
- Returns count of deleted rows, logs if any orphans cleaned

### Integration strategy per script:
- **Batch-processing scripts** (wikipedia, github, hackernews, rss, ssrn): Track `{source_id: chunk_count}` dict, flush cleanup after each batch via `source_chunk_counts`
- **Per-item scripts** (joplin, wikipedia_updates): Call cleanup immediately after each item's chunks are written
- **Single-chunk sources** (forex, worldbank): Each source_id has exactly 1 chunk (index=0), cleanup called per source_id with count=1 — effectively a no-op unless chunking changes

### Source ID pattern fix:
- `wikipedia_updates` had source_id = "Title::idx" — fixed to source_id = "Title" (consistent with main wikipedia ingestion). This was a bug where same-named source_ids were inconsistent.
- Removed unused `upsert_chunks` import from `ingest_wikipedia_updates.py`
