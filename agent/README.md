# Agent Runtime

FastAPI + LangGraph service on port `8000`. The agent owns tool orchestration,
retrieval, long-term memory, conversation checkpoints, model invocation, and
streaming responses back to the OpenWebUI pipeline adapter.

## Architecture

```text
graph.py (orchestrator)
  -> graph_nodes.py
       -> agent node      dynamic tool selection + guardrails
       -> classify node   complexity-based tier routing
  -> graph_llm.py         model alias resolution + multi-provider client
  -> graph_tools.py       tool pack definitions + request filtering
  -> graph_guardrails.py  circuit breaker, cascade fallback, message pruning
  -> graph_state.py       agent state, message helpers, memory loader
  -> graph_prompts.py     system identity / BASE_PROMPT
  -> tools/              LangChain tool implementations
```

The runtime uses OpenAI-compatible clients where possible. Provider-specific
model IDs are not hardcoded in graph code; they are resolved from `.env` through
`agent/config.py`.

## Configuration Contract

Model routing uses stable aliases:

- `FAST_MODEL`
- `SMART_MODEL`
- `REASONING_MODEL`
- `GRAPH_MODEL`
- `CLASSIFIER_MODEL`

`DEFAULT_LLM` and `RESEARCH_LLM` can point at aliases such as `smart` and
`reasoning`. Each alias can be a comma-separated fallback chain.

Supported provider modes are documented in `../docs/CONFIGURATION.md`.

## State and Memory

| Layer | Storage | Purpose |
|-------|---------|---------|
| Conversation checkpoints | PostgreSQL checkpoint tables | Resume in-flight sessions across restarts. |
| Long-term memory | `agent_memories` | Durable user/project facts. |
| Knowledge retrieval | `knowledge_chunks` | Vector and full-text search over ingested sources. |
| Structured datasets | Source-specific tables | Direct data access for forex and World Bank data. |

The FastAPI lifespan context opens the async PostgreSQL connection pool used by
LangGraph checkpointing.

## Tooling

The active tool list is defined in `agent/graph.py`. Tool implementations live
under `agent/tools/` and include retrieval, memory, web search, source
comparison, timeline retrieval, document reconstruction, Joplin operations, and
analysis execution.

Important retrieval entry points:

| Tool | Purpose |
|------|---------|
| `holistic_search` | Layered knowledge retrieval across current, research, established, and personal sources. |
| `adaptive_search` | Complexity-aware web + KB retrieval with optional HyDE and source expansion. |
| `kb_search` | Targeted hybrid search against a specific source. |
| `research` | Multi-query research with source synthesis. |
| `get_document` | Reconstruct full content from a chunk source identifier. |
| `timeline` | Chronological retrieval using published timestamps. |

## Routing Behavior

`agent/tools/router.py` classifies complex prompts by score, tier, and intent:

```text
User prompt -> classify_complexity() -> ComplexityResult(score, tier, intent)
```

The tier controls model selection and search depth. Intent controls preferred
knowledge layers, for example code-oriented prompts bias toward GitHub and
research prompts bias toward arXiv/bioRxiv before broader sources.

Thresholds, layer budgets, and intent/source mappings are in
`agent/tools/router.py`. Concrete model choices remain in `.env`.

## Adding a New Tool

1. Create `agent/tools/<name>.py` with a `@tool` decorated async function.
2. Import and export it in `agent/tools/__init__.py`.
3. Add it to the `TOOLS` list in `agent/graph_tools.py`.
4. Add concise guidance to `BASE_PROMPT` in `agent/graph_prompts.py` if the model needs
   to know when to use it.
5. Add tests for behavior that affects routing, persistence, or user-visible
   output.

## Adding a New Knowledge Source

If the new source is searchable through the knowledge base:

1. Add or update the ingestion pipeline under `ingestion/`.
2. Keep the `source` column value stable and unique.
3. Register source-specific embedding behavior in `agent/tools/kb_search.py` if
   it does not use the default text embedder.
4. Update router source mappings and holistic-search layers where the new source
   should appear.
5. Document the source in `../ingestion/README.md`.
