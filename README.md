# parsnip-ai

Open source research and analysis platform with:
- a tool-using backend agent service,
- persistent memory and vector retrieval in PostgreSQL/pgvector,
- extensible ingestion pipelines for trusted public and private sources,
- OpenWebUI integration for chat operations,
- optional Python/R analysis execution.

> Logo asset path: `docs/branding/logo.png` (drop-in file for GitHub/social previews).

## Why This Stack

`parsnip-ai` is designed for teams that want full control over data, model routing, and ingestion:
- **Fully self-hostable** end-to-end stack.
- **Memory + retrieval grounded outputs** with explicit ingestion flows.
- **Configurable model backend** via `.env` (OpenRouter, OpenAI-compatible APIs, Ollama embeddings).
- **Extension-ready ingestion architecture** for APIs, PDFs, notes, markdown, and org-specific sources.
- **Novelty focus**: combines agent memory, grounded ingestion, and enterprise-adaptable data connectors in one OSS deployment path.

## Core Capabilities

- ReAct-style agent orchestration with tool execution.
- Structured and unstructured retrieval:
  - `knowledge_chunks` (vector + FTS),
  - structured macro/market tables (e.g., World Bank / FX ingestion paths).
- Scheduled ingestion jobs (news, papers, notes).
- OpenWebUI-compatible chat endpoint via pipelines middleware.
- Optional analysis server for generated charts/reports (Python and R).

## Repository Layout

```text
agent/          # Backend agent API + tool graph
analysis/       # Analysis execution server
ingestion/      # Source ingestion scripts/pipelines
scheduler/      # Scheduled ingestion orchestration
joplin-mcp/     # Joplin bridge service
db/             # Database schema/init
pipelines/      # OpenWebUI pipelines connector
docs/           # Public deployment + extension docs
```

## Quick Start

```bash
cp .env.example .env
# fill required values
docker compose up -d --build
```

Primary endpoints (default):
- OpenWebUI: `http://localhost:3000`
- Pipelines: `http://localhost:9099`
- Agent API: `http://localhost:8000`
- Agent docs: `http://localhost:8000/docs`

## Configuration Model

All runtime configuration is environment-driven:
- `LLM_PROVIDER=openrouter|openai_compat`
- `OPENROUTER_API_KEY` for OpenRouter mode
- `OPENAI_COMPAT_BASE_URL` + `OPENAI_COMPAT_API_KEY` for OpenAI-compatible mode
- `OLLAMA_BASE_URL` for embeddings (local or remote)

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for complete configuration and backend routing notes.

## Deployment and Extension Docs

- Deployment: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- Configuration: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- Extending ingestion/tools: [docs/EXTENDING.md](docs/EXTENDING.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security: [SECURITY.md](SECURITY.md)

## License

Apache License 2.0 — see [LICENSE](LICENSE).
