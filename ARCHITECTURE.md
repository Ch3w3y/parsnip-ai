# Architecture

## High-Level Topology

```text
OpenWebUI (3000)
  -> Pipelines (9099)
    -> Agent API (8000)
      -> PostgreSQL + pgvector (5432)
      -> Analysis server (8095, optional)
      -> Joplin bridge (8090, optional)
      -> Search providers (SearXNG/Tavily/Brave)
```

## Main Services

- `agent`: tool-orchestrating API for retrieval, synthesis, and workflow execution.
- `pipelines`: OpenWebUI-compatible middleware for model/tool routing.
- `analysis`: optional execution runtime for Python/R workloads.
- `scheduler`: recurring ingestion and maintenance jobs.
- `postgres`: primary storage for vectors, memories, and ingestion metadata.
- `joplin-mcp`: optional integration bridge for note-based ingestion/output.

## Data Model Highlights

- `knowledge_chunks`: chunked content + embeddings for retrieval.
- `agent_memories`: durable memory records for cross-session context.
- `ingestion_jobs`: ingestion run state and progress tracking.

## Ingestion Pattern

Ingestion pipelines follow a fetch/process split:
1. Fetch source payloads.
2. Persist raw landing artifacts.
3. Transform/chunk/embed.
4. Upsert into structured/vector tables.

This allows replay on downstream failures without re-fetching upstream APIs.

## Model Routing and Backends

Runtime backend selection is environment-driven:
- `LLM_PROVIDER=openrouter` (default)
- `LLM_PROVIDER=openai_compat` (OpenAI-compatible API endpoint)
- Embeddings via `OLLAMA_BASE_URL` (local or remote)

## Deployment Posture

- Local-first Docker Compose baseline.
- Provider-flexible deployment (VM, managed DB, cloud object storage optional).
- GCP can be productionized directly; AWS/Azure parity patterns documented in `docs/DEPLOYMENT.md`.
