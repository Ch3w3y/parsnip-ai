# Ingestion Pipelines

All scripts run via `uv run python <script>` from the `ingestion/` directory. Shared utilities in `utils.py`.

## Pipelines

| Script | Source | Conflict | Schedule |
|--------|--------|----------|----------|
| `ingest_wikipedia.py` | Wikipedia dump (6.7M articles) | `DO UPDATE` | One-time seed + weekly updates |
| `ingest_wikipedia_updates.py` | MediaWiki recentchanges API | `DO UPDATE` | Weekly Sun 02:00 UTC, gated until seed completion |
| `ingest_arxiv.py` | arXiv API abstracts | `DO NOTHING` | Weekly Mon 03:00 UTC |
| `ingest_biorxiv.py` | bioRxiv/medRxiv API | `DO NOTHING` | Weekly Tue 03:00 UTC |
| `ingest_news_api.py` | NewsAPI.org (150k+ sources) | `DO NOTHING` | Daily 06:00 UTC |
| `ingest_forex.py` | Exchange-rate time series | `DO UPDATE` | Daily 07:00 UTC |
| `ingest_worldbank.py` | World Bank indicators | `DO UPDATE` | Weekly Sun 04:00 UTC |
| `ingest_news.py` | RSS feeds (fallback) | `DO NOTHING` | Manual only |
| `ingest_joplin.py` | Joplin Server notes | `DO UPDATE` | Watcher polls every 30s; scheduler safety every 6h |
| `ingest_github.py` | GitHub repos (source + docs) | `DO UPDATE` | Manual only |

**Conflict strategy rationale:** Wikipedia and Joplin notes change over time → overwrite. arXiv, bioRxiv, and news articles are immutable once published → skip duplicates. GitHub repos change → overwrite content+embedding, preserve `created_at`, set `updated_at`.

## Shared Utilities (`utils.py`)

```python
chunk_text(text, chunk_words=200, overlap_words=40)  # word-count sliding window
embed_batch(texts, retries=3)                         # Ollama /api/embed, truncate=True
bulk_upsert_chunks(conn, rows, on_conflict="update")  # executemany — one transaction
upsert_chunks(conn, ...)                              # row-by-row fallback
get_db_connection()                                   # psycopg async + pgvector registered
create_job / update_job_progress / finish_job         # ingestion_jobs tracking
save_raw(records, source, label="")                   # landing zone: JSONL.gz to data/raw/<source>/
iter_raw(path)                                        # generator: yield dicts from JSONL.gz
latest_raw(source)                                    # Path to most recent raw file for source
```

**Embedding models:** The system supports model-specific embeddings per source. `embed_batch(texts, model=...)` accepts an optional `model` parameter. Default is `mxbai-embed-large` (1024 dims). GitHub uses `bge-m3` (also 1024 dims, code-optimized). Both fit the `VECTOR(1024)` column without schema changes. `truncate=True` is set on all embed calls.

## Landing Zone Pattern

All API-based ingestion scripts (arxiv, biorxiv, news_api) follow a two-phase pattern:

```
Phase 1 — Fetch:   hit API → save_raw() → data/raw/<source>/YYYY-MM-DD.jsonl.gz
Phase 2 — Process: iter_raw() → embed_batch() → bulk_upsert_chunks()
```

If Phase 2 fails (embedding error, DB issue, VRAM OOM), replay from the saved file — **no API re-hit needed**:

```bash
uv run python ingest_arxiv.py --from-raw               # uses latest saved file
uv run python ingest_arxiv.py --from-raw path/to.jsonl.gz  # specific file
uv run python ingest_biorxiv.py --from-raw
uv run python ingest_news_api.py --from-raw
```

Raw files are gitignored (`ingestion/data/`). They accumulate over time as a local archive — delete old ones manually if disk space is a concern. A week of all sources is typically < 5MB total.

## GitHub Ingestion

Ingests source code and documentation from GitHub repositories using the GitHub API.

```bash
uv run python ingest_github.py                                          # default repos
uv run python ingest_github.py --repos langchain-ai/langgraph openai/swarm
uv run python ingest_github.py --max-files 500                          # limit files per repo
uv run python ingest_github.py --from-raw                               # replay latest
uv run python ingest_github.py --from-raw path/to/file.jsonl.gz         # specific file
```

**Configuration:**
- `GITHUB_TOKEN` env var (recommended for rate limit headroom — 5000/hr vs 60/hr unauthenticated)
- `GITHUB_REPOS` env var for persistent repo list, or use `--repos`
- Default repos: `langchain-ai/langgraph`, `anthropics/anthropic-cookbook`, `microsoft/autogen`

**Chunking strategy:**
- **Code files** (`.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.cpp`): split at function/class/type boundaries. Oversized functions (>600 words) fall back to word-based chunking.
- **Doc files** (`.md`, `.txt`, `.rst`, `.json`, `.yaml`, etc.): standard word-based chunking.
- Each chunk is prefixed with `File: path/to/file (language)` so the LLM knows context.

**Metadata per chunk:**
```json
{
  "language": "python",
  "repo": "owner/repo",
  "file_path": "src/module.py",
  "stars": 1234,
  "url": "https://github.com/owner/repo/blob/main/src/module.py",
  "total_chunks": 5,
  "file_type": "source"
}
```

**File filtering:**
- Skips: binaries, images, fonts, archives, lockfiles, dotfiles, files >200KB
- Prioritizes: docs first, then source files, then others (when exceeding `--max-files`)

**Embedding:** Uses `bge-m3` (1024 dims, code-optimized). The `embedding_model` column tracks which model was used per chunk.

## Wikipedia Bulk Ingest

The dump is ~21GB compressed; extraction produces ~22GB of JSONL. Use `pi-ctl.sh` to manage the process:

```bash
./pi-ctl.sh wiki start   # auto-resumes from last DB checkpoint
./pi-ctl.sh wiki stop    # safe stop — checkpoints every 500 articles (~6 min)
./pi-ctl.sh wiki status  # progress, VRAM, chunk count
```

**Download + extract (first time only):**
```bash
bash scripts/download_wikipedia.sh   # uses Docker python:3.10-slim for wikiextractor
                                     # wikiextractor is broken on Python 3.11+
```

**VRAM:** ~3.6GB while running (mxbai-embed-large). Stop before VRAM-intensive gaming:
```bash
./pi-ctl.sh wiki stop && # game # && ./pi-ctl.sh wiki start
```

## Scheduling

The `scheduler` container (built from `scheduler/Dockerfile`) runs APScheduler jobs for recurring sources and backup maintenance. Wikipedia incremental updates are scheduled but skipped until the initial dump seed is marked complete in `ingestion_jobs`. Start/stop with:

```bash
./pi-ctl.sh ingest start|stop|status
```

Current scheduled jobs:

| Job | Trigger |
|-----|---------|
| NewsAPI | Daily 06:00 UTC |
| arXiv | Monday 03:00 UTC |
| bioRxiv/medRxiv | Tuesday 03:00 UTC |
| Forex | Daily 07:00 UTC |
| World Bank | Sunday 04:00 UTC |
| Wikipedia updates | Sunday 02:00 UTC, after seed completion |
| Joplin safety sync | Every 6h |
| Knowledge-base backup | 02:00, 08:00, 14:00, 20:00 UTC |
| Config backup | Daily 01:00 UTC |

The Joplin watcher (`scheduler/joplin_watcher.py`) polls Joplin Server every 30s and triggers incremental ingestion on any note change.

## Adding a New Source

1. **Create the ingestion script** — `ingest_<source>.py` in `ingestion/`. The script must expose a `main_async()` entry point (or `main()` as fallback). Use `embed_batch` and `bulk_upsert_chunks` from `utils.py`.
2. **Add an entry to `sources.yaml`** — define the source with its schedule, conflict strategy, pipeline config, and enabled flag. The scheduler will auto-discover and schedule it based on this entry.
3. **Optionally add to `ROUTING_CONFIG`** — if the source should be searchable by the agent, add it to `intent_layers` and `layer_budgets` in `agent/tools/router.py`. If it uses a non-default embedding model, add it to `SOURCE_MODEL_MAP`.
4. **Follow the landing zone pattern** — separate `fetch_all_*()` from `process_*()`. Call `save_raw()` after fetching and add a `--from-raw` argument that calls `iter_raw()` instead of the API.
5. **Document it** — add a row to the Pipelines table above and update the source list in `agent/tools/kb_search.py` and `agent/tools/holistic_search.py` docstrings.

### sources.yaml Schema Reference

Each source entry in `sources.yaml` has these required fields:

| Field | Type | Description |
|-------|------|-------------|
| `module` | string | Python module name (must match `ingest_*.py` in `ingestion/`) |
| `schedule` | string or null | Cron expression for the scheduler, or `null` for manual-only |
| `conflict` | `"skip"` or `"update"` | Upsert conflict strategy (see below) |
| `pipeline.embedding` | object | `{provider: ollama, model: <embed_model>}` — which embedding model to use |
| `enabled` | boolean | `false` = scheduler ignores this source entirely |

**Conflict strategies:**

| Strategy | SQL | When to use |
|----------|-----|-------------|
| `skip` | `ON CONFLICT DO NOTHING` | Source content is immutable after publish (papers, articles) |
| `update` | `ON CONFLICT DO UPDATE` | Source content changes over time (notes, repos, indicators) |

The `SourceRegistry` (`ingestion/registry.py`) reads these entries, auto-discovers any `ingest_*.py` files not in YAML, and validates that every declared module has a `main_async()` or `main()` entry point.

## Extending the Routing System

When adding a new ingestion pipeline, update these files to integrate it into the agent's search:

### 1. `agent/tools/router.py` — `ROUTING_CONFIG`

```python
ROUTING_CONFIG = {
    "intent_layers": {
        # Add your source to relevant intent categories
        "code": ["github", "wikipedia", "your_source"],
        "research": ["arxiv", "biorxiv", "your_source"],
        "current": ["news", "your_source"],
        "general": ["wikipedia", "github", "joplin_notes", "arxiv", "news", "your_source"],
    },
    "layer_budgets": {
        "your_source": 4,  # max results per layer query
    },
}
```

### 2. `agent/tools/router.py` — `SOURCE_MODEL_MAP` (if using a non-default embedding model)

```python
SOURCE_MODEL_MAP = {
    "github": "bge-m3",
    "your_source": "your-embedding-model",  # must output 1024 dims
}
```

### 3. `agent/tools/kb_search.py` — update docstring source list

Add your source name to the `source` filter options in the docstring so the LLM knows it exists.

### 4. `agent/tools/holistic_search.py` — `_reorder_layers()`

Add your source to the default layer list and intent-specific reorderings:

```python
# In _reorder_layers(), add to each returned list:
("Your Source Label", ["your_source"], budgets.get("your_source", 3), None),
```

### 5. `agent/tools/__init__.py`, `agent/graph_tools.py`, and `agent/graph_prompts.py`

If you create a dedicated tool for the source (e.g. `code_search`):
1. Add it to `__all__` in `agent/tools/__init__.py`.
2. Add it to the `TOOLS` list in `agent/graph_tools.py`.
3. Update `BASE_PROMPT` in `agent/graph_prompts.py` if the model needs to know when to use it.

### Pattern Checklist

- [ ] Ingestion script follows landing zone pattern (`fetch_all` → `save_raw` → `process` → `bulk_upsert_chunks`)
- [ ] `source` column value is consistent and unique
- [ ] `embedding_model` column is set correctly (use `bge-m3` for code, `mxbai-embed-large` for text)
- [ ] `ROUTING_CONFIG` updated with intent layers and budget where relevant
- [ ] `SOURCE_MODEL_MAP` updated if using a custom embedder
- [ ] Docstrings updated in `kb_search.py` and `holistic_search.py`
- [ ] Documented in this README and user-facing docs where relevant
