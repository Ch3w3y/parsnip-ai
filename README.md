# parsnip-ai

<p align="center">
  <img src="docs/branding/logo-primary.png" alt="parsnip-ai logo" width="320">
</p>

<p align="center">
  <b>A fully open-source, air-gappable agentic research stack designed for privacy, grounded retrieval, and notebook-grade analysis.</b>
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

---

## 📖 Overview

`parsnip-ai` is an industrial-grade, self-hostable research assistant. It combines a LangGraph-powered orchestration engine, PostgreSQL/pgvector for long-term memory and retrieval, and a secure Python/R analysis sandbox. 

Built with **privacy and data sovereignty** in mind, it is the ideal architecture for homelabs, enterprise environments, and public sector organizations that require strict control over their data and LLM routing.

## ✨ Key Features

- **Hybrid Ollama Stack:** Execute low-complexity tasks on local GPUs (e.g., `gemma4`, `qwen3.5`) and seamlessly route deep-reasoning tasks to Ollama Cloud (`kimi-k2.6`), eliminating per-token costs while maintaining speed.
- **Grounded Generation (G-RAG):** Synthesizes live web search with curated, internal knowledge base grounding (Wikipedia, PDFs, News, arXiv).
- **Agentic Memory:** Long-term persistence and session-aware context (L1-L4 memory architecture).
- **Code Execution Sandbox:** A highly secure, isolated container for executing Python and R data science scripts, complete with artifact generation (charts, CSVs) and GCS backup.
- **Joplin Integration:** Native, two-way sync with Joplin Server for practical, end-user notebook workflows.

---

## 🏗️ Architecture

Parsnip relies on a decoupled, service-oriented architecture to ensure high availability and security.

For detailed sequence diagrams, network topologies, and data flows, please see our [Architectural Visualizations](docs/ARCHITECTURE_VISUALS.md).

```text
[ OpenWebUI (Frontend) ] <--> [ Pipelines (Middleware) ] <--> [ Agent API (LangGraph) ]
                                                                    |
                                     +------------------------------+------------------------------+
                                     |                              |                              |
                            [ PostgreSQL (pgvector) ]     [ Analysis Server (Sandbox) ]   [ Ingestion Scheduler ]
```

---

## 🚀 Quick Start & Deployment

### 1. Configuration
Copy the environment template and configure your keys and endpoints.

```bash
cp .env.example .env
```

**Example `.env` Configuration:**
```ini
# Core Database
POSTGRES_PASSWORD=your_secure_password
DATABASE_URL=postgresql://agent:${POSTGRES_PASSWORD}@localhost:5432/agent_kb

# Hybrid Ollama Routing
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=your_ollama_cloud_key
OLLAMA_CLOUD_URL=https://ollama.com/v1
EMBED_MODEL=mxbai-embed-large

# Local GPU Routing (Optional)
GPU_LLM_URL=http://your-local-gpu-ip:11434
GPU_LLM_MODEL=gemma4:e4b

# UI Security
WEBUI_SECRET_KEY=generate_a_random_secure_string
```

### 2. Deployment
Parsnip is packaged via Docker Compose for easy deployment.

```bash
# Build locally for development
docker compose up -d --build

# Or use pre-built GHCR images for production
IMAGE_TAG=0.1.0 docker compose up -d --no-build
```

**Access Points:**
- **OpenWebUI:** `http://localhost:3000`
- **Agent API Docs:** `http://localhost:8000/docs`

For advanced deployments (Kubernetes, GCP Cloud Run, AWS), refer to [DEPLOYMENT.md](docs/DEPLOYMENT.md) and [CLOUD_STORAGE_PLAN.md](docs/CLOUD_STORAGE_PLAN.md).

---

## 🛡️ Security, Privacy & Enterprise Readiness

Parsnip is designed to be **air-gappable**. By leveraging local Ollama instances for embeddings and LLM generation, no sensitive document data ever leaves your network. 

**Guardrails in Place:**
- **Aggressive Cost Control:** Built-in tool budgets, loop prevention, and context pruning ensure that runaway agent tasks are terminated before consuming excessive compute.
- **Sandboxed Execution:** The Analysis server runs in a strictly isolated container to prevent malicious code execution from affecting the host or database.
- **Persistent Disaster Recovery:** Automated, chron-triggered Parquet backups of the knowledge base and tarballs of the configuration to local disk or GCS.

---

## 📚 Documentation Directory

- **Architecture & Visuals:** [ARCHITECTURE.md](ARCHITECTURE.md) | [ARCHITECTURE_VISUALS.md](docs/ARCHITECTURE_VISUALS.md) | [CLOUD_STORAGE_PLAN.md](docs/CLOUD_STORAGE_PLAN.md)
- **Configuration & Setup:** [CONFIGURATION.md](docs/CONFIGURATION.md) | [DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **Extensibility:** [EXTENDING.md](docs/EXTENDING.md) | [HYBRID_RAG_SHOWCASE.md](docs/HYBRID_RAG_SHOWCASE.md)
- **Future Roadmap:** [ROADMAP.md](docs/ROADMAP.md) | [future-dev.md](docs/future-dev-daryn.md)

## License
Apache License 2.0. See [LICENSE](LICENSE).
