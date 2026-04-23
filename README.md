# parsnip-ai

<p align="center">
  <img src="docs/branding/logo-primary.png" alt="parsnip-ai logo" width="320">
</p>

<p align="center">
  <b>Self-hosted research infrastructure for grounded retrieval, notebook-grade analysis, and private model routing.</b>
</p>

<p align="center">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white">
  <img alt="pgvector" src="https://img.shields.io/badge/pgvector-334155?logo=postgresql&logoColor=white">
  <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-111827?logo=langchain&logoColor=white">
  <img alt="OpenWebUI" src="https://img.shields.io/badge/OpenWebUI-0F172A?logo=openai&logoColor=white">
  <img alt="Ollama" src="https://img.shields.io/badge/Ollama-000000?logo=ollama&logoColor=white">
  <img alt="Joplin" src="https://img.shields.io/badge/Joplin-1071D3?logo=joplin&logoColor=white">
</p>

## Overview

`parsnip-ai` is a Docker Compose stack for running a private research assistant with persistent retrieval, long-term memory, scheduled ingestion, and a controlled Python/R analysis environment. It is designed for operators who want the convenience of a chat interface without giving up control of data storage, model routing, or analysis artifacts.

The stack combines OpenWebUI, a pipeline adapter, a LangGraph-based agent API, PostgreSQL with pgvector/vectorscale, a Joplin Server integration, SearXNG, and a sandboxed analysis server. It can run fully local for private deployments, or route selected model calls to an OpenAI-compatible endpoint such as Ollama Cloud when higher-capacity reasoning is required.

## What It Provides

- Grounded retrieval over local knowledge sources, including Wikipedia dumps, RSS/news feeds, arXiv, bioRxiv/medRxiv, PDFs, Joplin notes, forex rates, and World Bank data.
- Hybrid search using vector retrieval, full-text search, metadata filters, timeline retrieval, source comparison, and document reconstruction.
- Persistent conversation state and long-term memory stored in PostgreSQL.
- Python, R, notebook, and dashboard execution through a separate analysis service with artifact capture.
- Two-way Joplin workflows for notes, notebooks, resources, reports, and generated research outputs.
- Scheduled ingestion and backups for repeatable operations.
- Model routing that can use local Ollama, Ollama Cloud, OpenRouter, or another OpenAI-compatible backend depending on configuration.

## Architecture

The runtime path is intentionally split into small services:

```text
Browser
  -> assistant-ui (Next.js frontend :3001)
  -> Agent API (/v1/chat/completions)
  -> tools, retrieval, memory, analysis, notebook sync, web search
```

`OpenWebUI :3000` and the pipelines adapter `:9099` remain available for
backward compatibility during the transition.

PostgreSQL is the main durable store. It holds knowledge chunks, embeddings, ingestion jobs, memory records, and LangGraph checkpoint state. Joplin Server uses its own database for notebook data. The analysis service writes generated files to a mounted output volume and can archive artifacts to object storage.

For detailed diagrams, see [docs/ARCHITECTURE_VISUALS.md](docs/ARCHITECTURE_VISUALS.md).

## Core Services

| Service | Default port | Purpose |
| --- | ---: | --- |
| Frontend (assistant-ui) | `3001` | **Primary** browser UI — Next.js + assistant-ui React components |
| OpenWebUI | `3000` | Legacy browser UI (backward compatibility during transition) |
| Pipelines | `9099` | OpenWebUI-compatible adapter (legacy, backward compatibility) |
| Agent API | `8000` | LangGraph orchestration, tools, memory, and streaming chat |
| PostgreSQL | `5432` | Knowledge base, vectors, memories, checkpoints, ingestion state |
| Analysis Server | `8095` | Python/R/notebook execution and artifact serving |
| Joplin Server | `22300` | Notebook storage and user-facing note sync |
| SearXNG | `8080` | Local metasearch provider |
| Scheduler | n/a | News, arXiv, Joplin, forex, World Bank, backup, and Wikipedia jobs |

## Model Routing

Model selection is configured in `.env`. The agent accepts stable aliases such as `fast`, `smart`, `reasoning`, and `classifier`, then resolves them to provider-specific model IDs through env-backed alias variables such as `FAST_MODEL`, `SMART_MODEL`, and `REASONING_MODEL`.

Supported routing patterns:

- Local Ollama for private low-latency inference.
- Ollama Cloud or another OpenAI-compatible endpoint for larger models.
- OpenRouter as a fallback provider when configured.
- Local embeddings through `mxbai-embed-large` by default.

The fallback path is explicit: if a primary model is unavailable or rate-limited, the agent cascades through configured alternatives before failing the request.

## Data and Ingestion

The ingestion layer stores raw or structured source data first, then chunks and embeds content into `knowledge_chunks`. This keeps retrieval rebuilds separate from external API fetches and allows schema repairs without re-downloading upstream data.

Important tables:

- `knowledge_chunks`: content, metadata, embeddings, source IDs, and chunk indexes.
- `ingestion_jobs`: job state and resumability for scheduled and bulk ingestion.
- `agent_memories`: durable long-term memory records.
- `forex_rates` and `world_bank_data`: structured datasets for direct analysis queries.

Large datasets should live under `ingestion/data/`, which is intentionally ignored by git.

## Quick Start

Prerequisites:

- Docker and Docker Compose.
- A configured `.env` file.
- Local Ollama or a compatible remote model endpoint.
- Optional: Google Cloud Storage credentials for backup/archive workflows.

Configure the environment:

```bash
cp .env.example .env
```

Minimum useful settings:

```ini
POSTGRES_PASSWORD=replace-with-a-strong-password
DATABASE_URL=postgresql://agent:${POSTGRES_PASSWORD}@localhost:5432/agent_kb

LLM_PROVIDER=openai_compat
DEFAULT_LLM=smart
RESEARCH_LLM=reasoning
FAST_MODEL=provider/fast-model
SMART_MODEL=provider/smart-model
REASONING_MODEL=provider/reasoning-model
GRAPH_MODEL=provider/smart-model
CLASSIFIER_MODEL=provider/classifier-model

OLLAMA_BASE_URL=http://localhost:11434
EMBED_MODEL=mxbai-embed-large

WEBUI_SECRET_KEY=replace-with-a-random-secret
```

Start the stack:

```bash
docker compose up -d --build
```

Open:

- Frontend (assistant-ui): `http://localhost:3001`
- Agent API docs: `http://localhost:8000/docs`
- OpenWebUI: `http://localhost:3000` (legacy)

## Operations

Useful checks:

```bash
./pi-ctl.sh status
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/stats
curl -sS http://localhost:8000/ingestion/status
curl -sS http://localhost:3000/api/config
```

Common workflows:

- Start or stop Wikipedia ingestion with `./pi-ctl.sh wiki start` and `./pi-ctl.sh wiki stop`.
- Check ingestion / migration health with `curl -sS http://localhost:8000/ingestion/status` or `python scripts/ingestion_status.py`.
- Run a knowledge base report with `python scripts/kb_report.py`.
- Back up KB data with `python scripts/backup_kb.py`.
- Back up project configuration with `python scripts/backup_config.py`.

## Security Notes

- Keep `.env`, service credentials, API keys, database dumps, and generated backups out of git.
- Do not mount object storage directly as a live PostgreSQL or Joplin database volume. Use local block storage for databases and object storage for backups.
- Treat generated analysis outputs as user data. Review before sharing or publishing.
- Rotate secrets if they appear in logs, shell history, commits, or exported artifacts.

## Documentation

- [Architecture overview](ARCHITECTURE.md)
- [Architecture diagrams](docs/ARCHITECTURE_VISUALS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Storage and backup guidance](docs/STORAGE_AND_BACKUP.md)
- [Extension guide](docs/EXTENDING.md)
- [Hybrid RAG showcase](docs/HYBRID_RAG_SHOWCASE.md)
- [Branding assets](docs/branding/README.md)

## License

Apache License 2.0. See [LICENSE](LICENSE).
