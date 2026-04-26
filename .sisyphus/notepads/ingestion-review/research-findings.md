# Ingestion Review — Research Findings

## [2026-04-25] Pre-Review Research Complete

### 1. Ingestion Architecture (from `bg_ac3d5bcc`)

**14 ingestion scripts** across 3 categories:

| Category | Scripts | Pattern |
|----------|---------|---------|
| Scheduled (7) | arxiv, biorxiv, news_api, forex, worldbank, wikipedia_updates, joplin | Cron via APScheduler |
| Manual (6) | github, hackernews, news, pubmed, rss, ssrn | CLI invocation |
| One-time (1) | wikipedia | Bulk dump seed |

**Infrastructure**:
- `utils.py` — shared chunking, embedding, DB connection, landing zone I/O, job tracking
- `registry.py` — declarative config + auto-discovery from `sources.yaml`
- `scheduler/` — APScheduler daemon + Joplin 30s watcher
- `db/init.sql` — 13 tables, raw psycopg (no ORM), no migration framework

**Anti-patterns cataloged**:
1. Inconsistent landing zone adoption (3 scripts skip it)
2. Mixed upsert strategies without clear rationale
3. No circuit breaker on Ollama embedding service (6min to fail)
4. Synchronous subprocess in `run_serial_ingestion.py`
5. Duplicate `_chunk_text()` implementations (Joplin, PDF)
6. `run_serial_ingestion.py` not integrated with registry (hard-coded)
7. `ingest_joplin.py` doesn't use shared `utils.py`
8. No pagination/commit boundaries on bulk upserts
9. No per-source rate limit config (hard-coded delays)
10. No Dead Letter Queue

**Positive patterns**:
- Well-structured plugin registry with auto-discovery
- Consistent entry point convention (`main_async`/`main`)
- Landing zone for 10/14 scripts (replay without API re-hit)
- Dual-write for structured data (forex_rates + KB chunks)
- Job tracking with `ingestion_jobs` table
- Source-specific metadata in JSONB

---

### 2. Database Schema (from `bg_f97121c7`)

**13 tables** (raw psycopg, no ORM):

| Table | Purpose | Key Pattern |
|-------|---------|------------|
| `knowledge_chunks` | All KB content | Denormalized (text+vector+JSONB), dual-source for forex/worldbank |
| `ingestion_jobs` | Job tracking | Status, progress, timestamps |
| `agent_memories` | LTM | Soft-delete, ranked FTS |
| `forex_rates` | Structured FX | Unique (pair, date) |
| `world_bank_data` | Structured macro | Unique (indicator, country, year) |
| `notebooks` | Joplin folders | Hierarchical FK |
| `notes` | Joplin notes | Soft-delete, FTS, UUID PK |
| `tags` | Joplin tags | Unique name |
| `note_tags` | M:N junction | Composite PK |
| `note_resources` | Attachments | BYTEA, FK CASCADE |
| `hitl_sessions` | HITL reviews | UUID FK to notes |
| `thread_metadata` | Thread titles | TEXT PK, appears unused |
| `joplin_hitl_sessions` | Legacy HITL | Different column types — being replaced |

**Schema drift issues**:
1. Dual HITL tables (legacy vs new) with different column types
2. Soft-deleted notes not cleaned from `knowledge_chunks`
3. Wikipedia `source_id` migration (legacy format vs new `chunk_index`)
4. `user_id` unused (only set for Joplin, but schema supports it for all)
5. `thread_metadata` defined but not visibly populated

**Index strategy**:
- `diskann` on `knowledge_chunks.embedding` (20M+ vectors, disk-based)
- `GIN` FTS on `knowledge_chunks.content`
- Composite on `(source, source_id)`
- Partial index on `user_id WHERE NOT NULL`
- LangGraph checkpoint tables auto-created by library

---

### 3. Best Practices Research (from `bg_017b8b65`)

**Key trends (2024-2026)**:
- ELT dominates over ETL for analytics workloads
- Medallion architecture (Bronze→Silver→Gold) is standard
- Scheduled micro-batch for heterogeneous sources
- dlt (data load tool) for incremental loading, schema inference
- Prefect/Dagster for orchestration
- OpenLineage for data lineage
- OpenTelemetry for observability
- Pydantic for schema validation
- Circuit breaker + DLQ + retry budgets for resilience
- Token bucket rate limiters per source
- `MERGE`/`INSERT ON CONFLICT` for idempotent writes
- Content hashing for deduplication
- Structured logging with correlation IDs

**Relevant tech stack**:
- `dlt` — auto-schema, incremental, normalization
- `Prefect` — Python-native orchestration (async support)
- `tenacity` — retry with backoff
- `LimitPal` — combined limiter + circuit breaker + retry
- `aiolimiter` — async token bucket
- `OpenTelemetry` — traces + metrics
- `structlog` — structured logging
- `alembic` — DB migrations

**ParSNIP gaps vs industry**:
- No ELT separation (transform happens in Python before load)
- No Medallion layers (no Bronze/Silver/Gold)
- No schema registry (JSONB metadata is schema-on-read)
- No migration framework (manual IF NOT EXISTS SQL)
- No DLQ for failed records
- No circuit breaker on embedded embedding service
- No structured logging (basic Python logging only)
- No data lineage tracking
- No performance metrics exposure
- No configuration-driven retry/rate limit/batch size
- Raw psycopg (not wrong, but harder to evolve than SQLAlchemy + Alembic)
