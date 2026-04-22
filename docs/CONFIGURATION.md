# Configuration

All runtime behavior is configured via `.env`.

## Required Baseline

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `WEBUI_SECRET_KEY`
- `LLM_PROVIDER`

## LLM Backend Modes

### Ollama (Hybrid Local/Cloud)

The preferred stack for low cost and high reasoning:
- **Local GPU:** `GPU_LLM_URL` and `GPU_LLM_MODEL`.
- **Ollama Cloud:** `OLLAMA_API_KEY` and `OLLAMA_CLOUD_URL`.
- Set `MODEL_ALIASES` in `agent/config.py` to route tiers (e.g. `kimi-k2.6:cloud`).

### OpenRouter

Set:
- `LLM_PROVIDER=openrouter`
- `OPENROUTER_API_KEY`
- `DEFAULT_LLM`
- `RESEARCH_LLM`

### OpenAI-Compatible Endpoint

Set:
- `LLM_PROVIDER=openai_compat`
- `OPENAI_COMPAT_BASE_URL`
- `OPENAI_COMPAT_API_KEY`
- `DEFAULT_LLM`
- `RESEARCH_LLM`

`OPENAI_COMPAT_BASE_URL` may be local or remote. `/v1` is appended automatically when missing.

## Embeddings

- `OLLAMA_BASE_URL` controls embedding endpoint.
- Supports local or remote Ollama hosts.
- `EMBED_MODEL` controls embed model name.

## Search Backends

- `SEARCH_BACKEND=auto|searxng|tavily|brave`
- `SEARXNG_URL`, `TAVILY_API_KEY`, `BRAVE_API_KEY` as applicable.

## Optional Integrations

- Joplin: `JOPLIN_*` variables
- Analysis server: `ANALYSIS_URL`
- Storage: `GCS_BUCKET`, `GCS_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS`
