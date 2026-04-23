# Extending Parsnip

This guide covers every extension pattern in Parsnip — from adding a new ingestion source to wiring a custom tool into the agent graph. Each section maps to concrete files and conventions in the repository.

## Ingestion Extension

All API-based ingestion scripts follow a four-stage pipeline:

1. **Fetch** — call the external API and collect raw records.
2. **Save raw** — persist the payload to the landing zone via `save_raw(records, source, label="")`, which writes a date-stamped JSONL.gz file to `ingestion/data/raw/<source>/YYYY-MM-DD.jsonl.gz`.
3. **Process** — transform, chunk, and embed the records using `chunk_text()`, `embed_batch()`, and `bulk_upsert_chunks()` from `ingestion/utils.py`.
4. **Upsert** — write chunks into `knowledge_chunks` with the appropriate conflict strategy (`skip` or `update`).

This two-phase design is critical: Phase 1 (fetch + save_raw) and Phase 2 (process + upsert) are separable. If Phase 2 fails (embedding error, DB issue, VRAM OOM), you can replay from the saved raw file without re-hitting the API:

```bash
uv run python ingest_arxiv.py --from-raw               # latest saved file
uv run python ingest_arxiv.py --from-raw path/to.jsonl.gz  # specific file
```

The `--from-raw` flag should call `iter_raw(path)` instead of the fetch function. See `ingestion/ingest_arxiv.py` for a complete reference implementation, and `ingestion/README.md` for the full list of 14 sources and shared utilities.

### sources.yaml Schema

Each source in `ingestion/sources.yaml` declares:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `module` | string | yes | Python module name matching `ingest_*.py` in `ingestion/` |
| `schedule` | string or null | yes | Cron expression for the scheduler, or `null` for manual-only |
| `conflict` | `"skip"` or `"update"` | yes | Upsert conflict strategy (see below) |
| `pipeline.embedding` | object | no | `{provider: ollama, model: <embed_model>}` — defaults to `mxbai-embed-large` |
| `enabled` | boolean | no | `false` = scheduler ignores this source entirely (default: `true`) |

**Conflict strategies:**

| Strategy | SQL | When to use |
|----------|-----|-------------|
| `skip` | `ON CONFLICT DO NOTHING` | Source content is immutable after publish (papers, articles) |
| `update` | `ON CONFLICT DO UPDATE` | Source content changes over time (notes, repos, indicators) |

For the complete sources.yaml schema reference with examples, see the "sources.yaml Schema Reference" section in `ingestion/README.md`.

## SourceRegistry Pattern

The `SourceRegistry` (`ingestion/registry.py`) is the central mechanism for discovering and managing ingestion sources. It combines declarative YAML definitions with automatic filesystem discovery, so adding a new source requires minimal wiring.

### How it works

1. **YAML-first** — On initialization, `SourceRegistry` reads `ingestion/sources.yaml`. Each entry is validated: the `module` field must exist and expose a `main_async()` or `main()` entry point. Missing or invalid entries raise `ValueError` immediately.

2. **Auto-discovery** — After loading YAML entries, the registry scans `ingestion/` (and any `extra_ingestion_dirs`) for `ingest_*.py` files not already declared in YAML. Each discovered module is validated for an entry point and registered automatically with `schedule=None` and `conflict_strategy="skip"`.

3. **Lookup** — Consumers (the scheduler, CLI tools) call `reg.get_source(name)` or `reg.list_sources(enabled_only=True)` to retrieve `SourceEntry` objects with their resolved `module_ref`, schedule, conflict strategy, and pipeline config.

### Adding a new source with the registry

To register a new source, you need exactly two things:

1. **`ingestion/ingest_<source>.py`** — A Python module with a `main_async()` async function (or `main()` sync fallback). This is the minimum requirement for auto-discovery.

2. **`ingestion/sources.yaml` entry** — A YAML block under `sources:` with `module`, `schedule`, and `conflict` fields. This gives the scheduler the cron expression and conflict strategy, which auto-discovery alone cannot provide.

If you forget to add the YAML entry, the source will still be auto-discovered but will have `schedule=None` (manual-only) and `conflict_strategy="skip"`. This is fine for quick testing but not for scheduled production sources.

For programmatic registration at runtime (e.g., in tests), use `reg.register_source(entry)` with a dict containing `name`, `module`, and `conflict` fields.

## Routing System Integration

The agent's routing pipeline (`agent/tools/router.py`) classifies queries by complexity and intent, then selects appropriate knowledge-base sources. When you add a new ingestion source that should be searchable by the agent, you must integrate it into the routing configuration.

For the full routing documentation including threshold definitions and intent keyword regexes, see [ROUTING.md](ROUTING.md).

### ROUTING_CONFIG fields to update

The `ROUTING_CONFIG` dict in `agent/tools/router.py` contains four sections relevant to source integration:

1. **`intent_layers`** — Maps intent keywords to ordered source lists. Add your source to every intent category where it should appear. The order determines search priority:

   ```python
   "intent_layers": {
       "code": ["github", "wikipedia", "your_source"],
       "research": ["arxiv", "biorxiv", "wikipedia", "your_source"],
       "current": ["news", "your_source"],
       "general": ["wikipedia", "github", "joplin_notes", "arxiv", "news", "your_source"],
   }
   ```

2. **`layer_budgets`** — Max results returned per source per query. Set a budget for your source based on expected result density:

   ```python
   "layer_budgets": {
       "your_source": 4,
   }
   ```

### SOURCE_MODEL_MAP

If your source uses a non-default embedding model, declare it in `SOURCE_MODEL_MAP` (also in `agent/tools/router.py`):

```python
SOURCE_MODEL_MAP = {
    "github": "bge-m3",
    "your_source": "your-embedding-model",  # must output 1024 dims
}
```

Sources not in `SOURCE_MODEL_MAP` fall back to `DEFAULT_MODEL` (`mxbai-embed-large`). Both models output 1024-dim vectors fitting the `VECTOR(1024)` column. The `embedding_model` column in `knowledge_chunks` tracks which model produced each chunk's embedding.

**Important:** Changing a source's embed model only affects newly ingested chunks. To re-embed an entire source, re-run ingestion with `--from-raw` after updating the model.

### Additional routing integration points

- **`agent/tools/kb_search.py`** — Add your source name to the `source` filter options in the tool's docstring so the LLM knows it can filter by that source.
- **`agent/tools/holistic_search.py`** — Add a layer tuple to `_reorder_layers()` with the format `("Label", ["your_source"], budgets.get("your_source", 3), None)`.

## Tool Extension

Adding a new agent tool requires changes in four files:

1. **Implement the tool** — Create `agent/tools/<name>.py` with a `@tool` decorated async function using LangChain's tool decorator. The function's docstring becomes the tool description that the LLM reads to decide when to use it.

2. **Register in `agent/tools/__init__.py`** — Import your tool and add it to the `__all__` list:

   ```python
   from .your_tool import your_tool_name
   __all__ = [
       # ... existing tools ...
       "your_tool_name",
   ]
   ```

3. **Wire into `agent/graph_tools.py`** — Add the import at the top of the file, then add the tool to the appropriate tool pack list (e.g., `CORE_TOOLS`, `RESEARCH_TOOLS`, `ANALYSIS_TOOLS`) and/or the combined `TOOLS` list at the bottom. Tool packs determine which tools are available based on the user's intent classification — see the `TOOL_PACKS` dict for the full mapping.

4. **Update `agent/graph_prompts.py`** — If the LLM needs explicit guidance on when to use the new tool, add a concise instruction line to `BASE_PROMPT`. Keep it brief; the tool's docstring already provides the primary usage contract.

### Tool pack strategy

Tools are grouped into packs that the agent selects based on intent:

| Pack | Included in | Typical content |
|------|-------------|-----------------|
| `core` | All requests | Search, memory, system status |
| `research` | Research requests | KB search, timeline, comparison, knowledge gaps |
| `analysis` | Analysis requests | Python/R execution, notebooks, dashboards |
| `workspace` | Workspace requests | File ops, bash, script execution |
| `github` | Code requests | Repository tools |
| `notes` | Note requests | Joplin CRUD |

High-complexity requests automatically include research tools even when the primary intent is narrower. See `_select_tools_for_request()` in `agent/graph_tools.py` for the full selection logic.

## Frontend Extension

The frontend renders tool-specific UI components in `frontend/src/components/tools/ToolUIs.tsx`. Each tool is mapped to a React component via `makeAssistantToolUI` from `@assistant-ui/react`.

### How to add a custom tool UI

1. **Write a render function** — Define a function matching the signature `({ args, status, result }) => JSX` that handles three states: `status.type === "running"`, `status.type === "error"`, and the completed state. Existing render functions like `renderSearchTool`, `renderJoplinTool`, and `renderAnalysisTool` serve as templates.

2. **Register the component** — Call `makeAssistantToolUI<ToolArgs, string>({ toolName: "your_tool_name", render: yourRenderFunction })` and assign it to a named const.

3. **Add to the registry** — Render the component inside the `ToolUIRegistry` component's JSX at the bottom of the file.

Tools without a custom UI fall through to `renderGenericTool`, which shows the tool name, status, and raw result text. For full frontend documentation, see `frontend/FRONTEND.md`.

## Structured Data Extension

Some ingestion sources write to dedicated structured tables in addition to (or instead of) embedding text into `knowledge_chunks`. The `forex_rates` table is the primary example — it stores numeric rate observations for direct SQL queries while also generating searchable text chunks.

### The forex_rates pattern

When adding a new structured dataset:

1. **Create the table** — Define a PostgreSQL table with appropriate columns and a unique conflict target (e.g., `(pair, rate_date)` for forex). Include `fetched_at` or `updated_at` timestamps.

2. **Write the upsert function** — In your ingestion script, implement an `upsert_<table>_rates()` function that uses `INSERT ... ON CONFLICT ... DO UPDATE` semantics. Use `executemany` inside a transaction for bulk efficiency. See `upsert_forex_rates()` in `ingestion/ingest_forex.py` for the canonical pattern.

3. **Generate KB chunks** — Also create text representations of the structured data for semantic search. The `records_to_chunks()` function in `ingest_forex.py` converts rate records into natural-language descriptions like `"Foreign exchange rate for EUR/USD on 2025-01-15: 1 EUR = 1.087500 USD"` — readable by both the embedding model and the LLM.

4. **Preflight validation** — For analysis queries against structured tables, add preflight validation that checks for required identifiers (country codes, currency pairs, indicator names) before running expensive analysis. This prevents the agent from hallucinating column values or running queries against empty partitions.

**When to use structured tables vs. KB chunks:** Use structured tables when the data is numeric and analyzed by SQL (rates, indicators, time series). Use KB chunks when the data is text and searched semantically (papers, articles, notes). Many sources benefit from both.

## Connection Pool Extension

The agent uses named connection pools managed by `agent/tools/db_pool.py` to share PostgreSQL connections across tools and modules. Each pool is a `psycopg_pool.AsyncConnectionPool` instance keyed by a logical name.

### How to add a new named pool

1. **Initialize the pool at startup** — In the FastAPI lifespan context (or wherever pools are initialized), call:

   ```python
   from tools.db_pool import init_pool
   await init_pool("your_pool_name", settings.database_url, min_size=2, max_size=10)
   ```

   If a pool with the same name already exists, it is closed and replaced. The `min_size` and `max_size` parameters control idle and maximum connections.

2. **Use the pool in tools** — Any module can retrieve the pool by name:

   ```python
   from tools.db_pool import get_pool
   pool = get_pool("your_pool_name")
   async with pool.connection() as conn:
       await conn.execute("SELECT 1")
   ```

   Calling `get_pool()` for an unregistered name raises `ValueError` with the list of available pools.

3. **Shutdown** — `close_all()` is called at app shutdown to clean up every registered pool. You do not need to close individual pools manually.

### Current pools

The default pool is `"agent_kb"`, used by most knowledge-base tools. Modules that need isolated connection budgets (e.g., long-running analysis queries) should define their own named pool rather than sharing the default.

## Complete Example: Adding a Hypothetical Source

This walkthrough demonstrates adding a hypothetical "SEC filings" source end-to-end, touching every integration point.

### Step 1: Create the ingestion script

Create `ingestion/ingest_sec.py` following the landing zone pattern:

```python
#!/usr/bin/env python3
"""SEC filings ingestion: fetch 10-K/10-Q filings and embed into pgvector."""

import argparse
import asyncio
import logging
from pathlib import Path

from utils import (
    chunk_text, embed_batch, bulk_upsert_chunks,
    get_db_connection, create_job, finish_job,
    update_job_progress, save_raw, iter_raw, latest_raw,
)

SEC_API = "https://api.sec.gov/filings"

async def fetch_all_filings(ticker: str, years: int = 5) -> list[dict]:
    """Phase 1: Fetch SEC filings from the API."""
    # ... API call logic ...
    return filings

async def process_filings(filings: list[dict]):
    """Phase 2: Embed and upsert filings."""
    conn = await get_db_connection()
    job_id = await create_job(conn, "sec")
    await conn.commit()

    # Chunk, embed, upsert
    chunks = []
    for filing in filings:
        text_chunks = chunk_text(filing["text"])
        for i, chunk in enumerate(text_chunks):
            chunks.append({
                "source_id": f"{filing['accession_number']}::{i}",
                "text": chunk,
                "metadata": {
                    "ticker": filing["ticker"],
                    "form_type": filing["form_type"],
                    "filed_date": filing["filed_date"],
                    "url": filing["url"],
                },
            })

    # Batch embed and upsert
    total = 0
    for batch_start in range(0, len(chunks), 32):
        batch = chunks[batch_start:batch_start + 32]
        texts = [c["text"] for c in batch]
        embeddings = await embed_batch(texts)
        rows = [
            ("sec", c["source_id"], 0, c["text"], c["metadata"], emb, "mxbai-embed-large")
            for c, emb in zip(batch, embeddings) if emb is not None
        ]
        total += await bulk_upsert_chunks(conn, rows, on_conflict="update")

    await finish_job(conn, job_id, "done")
    await conn.commit()
    await conn.close()
    return total

async def main_async(ticker: str = "AAPL", years: int = 5,
                     from_raw: Path | None = None):
    """Async entrypoint for scheduler and CLI."""
    if from_raw:
        filings = list(iter_raw(from_raw))
    else:
        filings = await fetch_all_filings(ticker, years)
        save_raw(filings, "sec")

    await process_filings(filings)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--from-raw", nargs="?", const="", default=None)
    args = parser.parse_args()

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("sec")

    asyncio.run(main_async(args.ticker, args.years, raw_path))

if __name__ == "__main__":
    main()
```

### Step 2: Add to sources.yaml

```yaml
  sec:
    module: ingest_sec
    schedule: "0 5 * * 1"       # Mon 05:00 UTC
    conflict: update            # DO UPDATE — filings can be amended
    pipeline:
      embedding: {provider: ollama, model: mxbai-embed-large}
    enabled: true
```

### Step 3: Update routing

In `agent/tools/router.py`:

```python
# Add to intent_layers
"intent_layers": {
    "research": ["arxiv", "biorxiv", "wikipedia", "sec", "news"],
    "general": ["wikipedia", "github", "joplin_notes", "arxiv", "sec", "news"],
}

# Add budget
"layer_budgets": {
    "sec": 4,
}
```

No `SOURCE_MODEL_MAP` entry is needed since SEC filings use the default embedder.

### Step 4: Update search docstrings

In `agent/tools/kb_search.py`, add `"sec"` to the `source` filter options in the tool's docstring. In `agent/tools/holistic_search.py`, add to `_reorder_layers()`:

```python
("SEC Filings", ["sec"], budgets.get("sec", 3), None),
```

### Step 5: Document

Add a row to the Pipelines table in `ingestion/README.md`:

| `ingest_sec.py` | SEC 10-K/10-Q filings | `DO UPDATE` | Weekly Mon 05:00 UTC |

### Verification checklist

- [ ] `ingest_sec.py` follows landing zone pattern (fetch → save_raw → process → upsert)
- [ ] `source` column value `"sec"` is consistent and unique
- [ ] `embedding_model` column set correctly (default: `mxbai-embed-large`)
- [ ] `sources.yaml` entry has `module`, `schedule`, and `conflict`
- [ ] `ROUTING_CONFIG` updated with intent layers and budget
- [ ] Docstrings updated in `kb_search.py` and `holistic_search.py`
- [ ] Pipeline documented in `ingestion/README.md`