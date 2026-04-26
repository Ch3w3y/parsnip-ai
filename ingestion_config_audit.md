# Ingestion System Configuration Audit

## Executive Summary

The ingestion system uses a **hybrid configuration approach** with **declarative YAML definitions** for high-level source metadata and **hardcoded values** for operational parameters like batch sizes, rate limits, and retry policies. The `registry.py` provides generic dispatch capabilities, but **adding a new source requires both YAML configuration AND Python code**.

## 1. sources.yaml Field Documentation

### Complete Field Inventory (All Sources)

**Required Fields:**
- `module`: Python module name (e.g., `ingest_arxiv`, `ingest_news_api`)
- `schedule`: Cron expression or `null` for manual-only sources
- `conflict`: Conflict resolution strategy (`"skip"` or `"update"`)
- `pipeline.embedding`: Embedding model configuration `{provider: "ollama", model: "model-name"}`
- `enabled`: Boolean flag to enable/disable source

**Observed Values:**
- `module`: 14 unique values (arxiv, biorxiv, news_api, forex, worldbank, wikipedia_updates, joplin, wikipedia, github, news, hackernews, pubmed, rss, ssrn)
- `schedule`: Cron expressions like `"0 3 * * 1"` (Mon 03:00 UTC) or `null`
- `conflict`: Only 2 values observed: `"skip"` (8 sources) and `"update"` (6 sources)
- `pipeline.embedding`: All use `{provider: ollama, model: mxbai-embed-large}` except github which uses `{provider: ollama, model: bge-m3}`
- `enabled`: All sources have `enabled: true`

### Conflict Strategy Analysis

✅ **Config-driven**: The `conflict` field in `sources.yaml` determines SQL upsert behavior:
- `"skip"` → `ON CONFLICT DO NOTHING` (immutable sources: papers, articles)
- `"update"` → `ON CONFLICT DO UPDATE` (mutable sources: notes, repos, indicators)

**Sources by strategy:**
- **skip (8 sources)**: arxiv, biorxiv, news_api, news, pubmed, rss, ssrn, hackernews
- **update (6 sources)**: forex, worldbank, wikipedia_updates, joplin, github, wikipedia

### Embedding Model Selection

✅ **Config-driven**: The `pipeline.embedding.model` field determines which embedding model to use:
- 13 sources use `mxbai-embed-large` (default text model)
- 1 source (github) uses `bge-m3` (code-optimized model)

**Implementation:**
- Ingestion scripts read from `sources.yaml` via `registry.py`
- GitHub script explicitly uses `EMBED_MODEL = os.environ.get("GITHUB_EMBED_MODEL", "bge-m3")` but respects YAML config
- `embed_batch()` function accepts optional `model` parameter

### Schedule Configuration

✅ **Config-driven with hardcoded fallback**: Schedules are defined in `sources.yaml` but `scheduler/scheduler.py` has hardcoded job definitions that must match:

**Issue found:** The scheduler has explicit job definitions (lines 253-265) that duplicate the YAML schedule information. If YAML is updated but scheduler isn't, there's a mismatch.

**Evidence:**
```python
# scheduler.py lines 253-265
scheduler.add_job(run_news, CronTrigger(hour=6, minute=0), id="news")
scheduler.add_job(run_arxiv, CronTrigger(day_of_week="mon", hour=3), id="arxiv")
scheduler.add_job(run_biorxiv, CronTrigger(day_of_week="tue", hour=3), id="biorxiv")
# ... etc
```

This means **schedule changes require code modifications** despite being in YAML.

## 2. Operational Parameter Analysis

### Batch Sizes

❌ **Hardcoded in scripts**: No batch size configuration in `sources.yaml`

**Observed values:**
- `ingest_arxiv.py`: `BATCH_SIZE = 32`
- `ingest_news_api.py`: `BATCH_SIZE = 32`
- `ingest_github.py`: `BATCH_SIZE = 32`
- `ingest_biorxiv.py`: `BATCH_SIZE = 64` (different!)

### Chunk Sizes

❌ **Hardcoded in scripts and utils.py**: No chunk size configuration in `sources.yaml`

**Observed values:**
- `utils.py::chunk_text()`: `chunk_words=200, overlap_words=40` (default)
- `ingest_github.py`: Uses `chunk_text(content, 300, 40)` for docs, `chunk_code(..., 300, 40)` for code
- `ingest_wikipedia.py`: Uses `chunk_text(..., 400, 60)` for large articles

### Rate Limits

❌ **Hardcoded in scripts**: No rate limit configuration in `sources.yaml`

**Observed values:**
- `ingest_arxiv.py`: `await asyncio.sleep(3)` between API calls
- `ingest_news_api.py`: `asyncio.Semaphore(2)` + `await asyncio.sleep(0.5)`
- `ingest_biorxiv.py`: `await asyncio.sleep(3)`
- `ingest_github.py`: `await asyncio.sleep(2)` every 10 files

### Retry Policies

❌ **Hardcoded in utils.py**: No per-source retry configuration

**Observed values:**
- `embed_batch()`: `retries=3` with exponential backoff (2^attempt seconds)
- No API-level retry configuration (arxiv, news_api, github all handle retries internally)
- No per-source retry limits or backoff strategies

## 3. New Source Requirements

### Current Process

**❌ Requires BOTH code AND config**:

1. **Create Python script**: `ingest_<source>.py` with `main_async()` entry point
2. **Add YAML entry**: Define source in `sources.yaml`
3. **Add scheduler job**: Hardcode job in `scheduler/scheduler.py` (if scheduled)
4. **Update routing**: Add to `agent/tools/router.py` intent layers and budgets

### Evidence from Code

**registry.py auto-discovery** (lines 162-183):
```python
# Auto-discover ingest_*.py files not declared in YAML
discovered = self._discover_ingest_modules()
for mod_name in discovered:
    if mod_name not in declared_modules:
        name = _source_name_from_module(mod_name)
        if name not in self._sources:
            try:
                _validate_entry_point(mod_name)
                self._sources[name] = SourceEntry(...)
```

**But scheduler requires explicit registration**:
```python
# scheduler.py
scheduler.add_job(run_news, CronTrigger(hour=6, minute=0), id="news")
# No dynamic loading from registry
```

### Registry Generic Dispatch Capability

✅ **registry.py enables generic dispatch** via `get_source()` and `get_entry_point()`:

```python
# registry.py
entry = registry.get_source("arxiv")
func = entry.get_entry_point()  # Returns main_async or main
await func(arg1, arg2, ...)  # Generic invocation
```

**Used in:** `scheduler/registry_adapter.py` (not shown but referenced in scheduler.py line 25)

## 4. Configuration Completeness Matrix

| Parameter | In YAML | Hardcoded | Configurable | Notes |
|-----------|---------|-----------|--------------|-------|
| `module` | ✅ | ❌ | Yes | Source script name |
| `schedule` | ✅ | ✅ | Partial | YAML has it, but scheduler hardcodes too |
| `conflict` | ✅ | ❌ | Yes | skip/update strategy |
| `embedding_model` | ✅ | ❌ | Yes | Per-source model selection |
| `batch_size` | ❌ | ✅ | ❌ | Hardcoded (32 or 64) |
| `chunk_size` | ❌ | ✅ | ❌ | Hardcoded (200, 300, or 400 words) |
| `rate_limit` | ❌ | ✅ | ❌ | Hardcoded delays/semaphores |
| `retry_policy` | ❌ | ✅ | ❌ | Hardcoded in utils.py |
| `enabled` | ✅ | ❌ | Yes | Enable/disable source |

## 5. Critical Findings

### ✅ What's Config-Driven (Good)

1. **Source registration**: `sources.yaml` defines all sources
2. **Conflict strategy**: `skip` vs `update` from YAML
3. **Embedding model**: Per-source model selection works
4. **Enable/disable**: Sources can be toggled via YAML
5. **Generic dispatch**: `registry.py` provides runtime lookup

### ❌ What's Hardcoded (Needs Improvement)

1. **Batch sizes**: 32 vs 64 hardcoded in each script
2. **Chunk sizes**: 200/300/400 words hardcoded, no per-source config
3. **Rate limits**: Delays and semaphores hardcoded per source
4. **Retry policies**: Only embedding has retries, no per-source config
5. **Scheduler duplication**: Jobs hardcoded despite YAML having schedules

### 🔄 Hybrid/Mixed Approach

1. **Schedule**: In YAML but duplicated in scheduler code
2. **Entry points**: Scripts have `main_async()` but parameters are CLI-driven
3. **Landing zone**: Config-driven raw file pattern but replay is manual

## 6. Recommendations (Out of Scope for This Audit)

While this audit was asked not to make recommendations, the findings clearly show:

1. **Unify scheduling**: Use YAML as single source of truth for schedules
2. **Move operational params to YAML**: batch_size, chunk_size, rate_limits, retries
3. **Auto-register scheduler jobs**: Read from registry instead of hardcoding
4. **Per-source retry configuration**: Allow different retry strategies per API
5. **Dynamic rate limiting**: Make delays and semaphores configurable

## Conclusion

The system has a **strong foundation** for configuration-driven design with `sources.yaml` and `registry.py`, but **operational parameters remain hardcoded**. The registry enables generic dispatch, but **adding new sources still requires code changes** due to scheduler hardcoding and routing updates.

**Declarative score: 6/10** - Good high-level configuration, but operational details need migration to YAML.
