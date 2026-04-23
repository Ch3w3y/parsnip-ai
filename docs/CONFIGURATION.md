# Configuration

Runtime configuration lives in `.env`. Code should read deployment-specific
values through `agent/config.py`, `docker-compose.yml`, or service-specific
environment variables rather than hardcoding local machine choices.

Use `.env.example` as the public contract. Keep real `.env` files out of git.

## Required Baseline

| Variable | Purpose |
|----------|---------|
| `POSTGRES_PASSWORD` | Password for the local PostgreSQL container. |
| `DATABASE_URL` | Agent knowledge-base database URL. |
| `WEBUI_SECRET_KEY` | OpenWebUI session/security secret. |
| `LLM_PROVIDER` | Model provider mode: `openrouter` or `openai_compat`. |
| `GUARDRAIL_MODE` | Runtime guardrail strictness (`strict`, `balanced`, `lenient`). Default: `balanced`.

`JOPLIN_DATABASE_URL` is used by backup tooling when exporting the Joplin
database. In the compose stack this points at the same PostgreSQL service but a
separate `joplin` database.

## Model Aliases

The codebase uses stable aliases so prompts, graph code, and tests stay portable
across providers. Configure the concrete model IDs in `.env`:

| Variable | Used for |
|----------|----------|
| `FAST_MODEL` | Low-latency classification, simple synthesis, and cheap utility calls. |
| `SMART_MODEL` | General reasoning and default chat work. |
| `REASONING_MODEL` | Higher-complexity research and synthesis. |
| `GRAPH_MODEL` | LangGraph orchestration calls. |
| `CLASSIFIER_MODEL` | Complexity and intent classification. |

Each alias can contain a comma-separated fallback chain:

```ini
SMART_MODEL=provider/model-a,provider/model-b
```

`DEFAULT_LLM` and `RESEARCH_LLM` can point to aliases, for example:

```ini
DEFAULT_LLM=smart
RESEARCH_LLM=reasoning
```

Concrete model IDs in `.env.example` are examples only. Change them in `.env`
without editing Python code.

## Provider Modes

### OpenRouter

```ini
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=...
DEFAULT_LLM=smart
RESEARCH_LLM=reasoning
```

Alias variables still control the actual model IDs used by `smart`,
`reasoning`, and the other stable names.

### OpenAI-Compatible Endpoint

```ini
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=http://host:port/v1
OPENAI_COMPAT_API_KEY=...
DEFAULT_LLM=smart
RESEARCH_LLM=reasoning
```

`OPENAI_COMPAT_BASE_URL` may be local or remote. The agent normalizes the URL
for OpenAI-compatible clients.

### Ollama and Embeddings

| Variable | Purpose |
|----------|---------|
| `OLLAMA_BASE_URL` | Local or LAN Ollama endpoint for embeddings and compatible model calls. |
| `OLLAMA_API_KEY` | API key for hosted Ollama-compatible endpoints when needed. |
| `OLLAMA_CLOUD_URL` | Hosted Ollama-compatible base URL. |
| `OLLAMA_SSH_HOST` | Optional host used by helper scripts. |
| `EMBED_MODEL` | Embedding model used for general text chunks. |

Optional GPU routing variables:

| Variable | Purpose |
|----------|---------|
| `GPU_LLM_URL` | Local GPU model endpoint. |
| `GPU_LLM_MODEL` | Local GPU model for heavier calls. |
| `GPU_MID_MODEL` | Local GPU model for mid-tier calls. |

## Search and External Data

| Variable | Purpose |
|----------|---------|
| `SEARCH_BACKEND` | `auto`, `searxng`, `tavily`, or `brave`. |
| `SEARXNG_URL` | Local SearXNG endpoint used by the compose stack. |
| `TAVILY_API_KEY` | Tavily search key. |
| `BRAVE_API_KEY` | Brave Search key. |
| `NEWS_API_KEY` | NewsAPI ingestion key. |
| `GITHUB_TOKEN` | GitHub API token for ingestion and tool rate limits. |

## Joplin

| Variable | Purpose |
|----------|---------|
| `JOPLIN_ADMIN_EMAIL` | Admin account used by setup/bootstrap scripts. |
| `JOPLIN_ADMIN_PASSWORD` | Admin password used by setup/bootstrap scripts. |
| `JOPLIN_SERVER_URL` | Internal server URL. |
| `JOPLIN_BASE_URL` | Public/base URL expected by Joplin Server. |
| `JOPLIN_MCP_URL` | Joplin MCP bridge URL used by the agent. |

Joplin Server creates its initial admin account only when its database is empty.
If a database is recreated, run the Joplin admin repair script documented in
`docs/DEPLOYMENT.md`.

## Analysis and Storage

| Variable | Purpose |
|----------|---------|
| `ANALYSIS_URL` | Analysis server endpoint used by agent tools. |
| `GCS_BUCKET` | Optional bucket for backups and artifacts. |
| `GCS_PROJECT_ID` | GCP project for storage clients. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service-account JSON path when using GCS. |
| `BACKUP_DIR` | Optional local output directory for `scripts/backup_kb.py`. |

## Frontend

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_AGENT_URL` | Public agent API URL used by the browser (must be reachable from the user's device). |
| `AGENT_INTERNAL_URL` | Internal agent API URL used by Next.js SSR inside Docker (container-to-container). Defaults to `http://pi_agent_backend:8000` when running in compose. |

Object storage is for backup artifacts and generated outputs. Do not mount GCS
or S3 as a live PostgreSQL data directory.

## Compose Variable Flow

`docker-compose.yml` passes the model aliases, provider settings, search
settings, database URLs, Joplin URLs, and storage settings into the relevant
containers. If a setting must vary by deployment, add it to `.env.example` and
compose rather than embedding it directly in application code.
