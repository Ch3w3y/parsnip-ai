# Security Policy

**Last Updated:** April 2026  
**Status:** Experimental software. Not audited for production security use.

Parsnip is a self-hosted research infrastructure stack. You control all data, models, and integrations. This document describes the security model, responsible disclosure process, and operational guidance.

---

## Reporting Vulnerabilities

**Do not open public issues for active security vulnerabilities.**

### How to Report

Send vulnerability reports privately to the project maintainer:

- **GitHub Security Advisory:** Use the "Report a vulnerability" feature in the Security tab
- **Email:** ch3w3y@proton.me (PGP key available on request)

### What to Include

- Affected component(s) and versions
- Step-by-step reproduction details
- Impact assessment (what could an attacker do?)
- Suggested remediation if available
- Your environment (Docker version, OS, deployment configuration)

### Response Timeline

- **Acknowledgment:** Within 7 days
- **Initial assessment:** Within 14 days
- **Fix timeline:** Depends on severity; will be communicated privately

---

## Supported Versions

| Version | Supported | Notes |
|---------|-----------|-------|
| Latest release on `main` | ✅ | Actively maintained |
| Previous minor release | ✅ | Security patches only |
| Older releases | ❌ | Upgrade required |

This project follows rapid iteration. Security fixes are released as patch versions when possible.

---

## Secret Management

### Environment Variables

All secrets are injected via environment variables. The `.env` file is **never committed** to version control.

**Required secrets** (see `.env.example` for full list):

| Variable | Purpose | Risk if Exposed |
|----------|---------|-----------------|
| `POSTGRES_PASSWORD` | Database authentication | Full database access |
| `WEBUI_SECRET_KEY` | OpenWebUI session signing | Session hijacking |
| `OPENROUTER_API_KEY` | LLM provider authentication | Quota exhaustion, data exposure |
| `OLLAMA_API_KEY` | Ollama Cloud authentication | Model access |
| `NEWS_API_KEY`, `TAVILY_API_KEY`, `BRAVE_API_KEY` | External API access | Quota exhaustion |
| `GITHUB_TOKEN` | GitHub API access | Repository access |
| `GCS_BUCKET`, `GCS_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` | Cloud storage | Data access, quota charges |
| `JOPLIN_ADMIN_PASSWORD` | Joplin Server admin | Note access |

### Best Practices

1. **Never commit `.env`** — it is in `.gitignore` by default
2. **Use strong passwords** — minimum 32 characters for `POSTGRES_PASSWORD` and `WEBUI_SECRET_KEY`
3. **Rotate secrets** if they appear in logs, shell history, or exported artifacts
4. **Use secret managers** (HashiCorp Vault, AWS Secrets Manager) for production deployments
5. **Mount GCS credentials as read-only volume** — see `docker-compose.yml` line 139

---

## Docker Security Posture

### Container User Context

All containers run as **root** by default. This is a known limitation for the current release.

**Services and their base images:**

| Service | Base Image | User |
|---------|------------|------|
| `agent` | `python:3.12-slim` | root |
| `analysis` | `rocker/tidyverse:4.4.2` | root |
| `scheduler` | `python:3.13-slim` | root |
| `joplin-mcp` | `python:3.12-slim` | root |
| `postgres` | `timescale/timescaledb-ha:pg16` | postgres (non-root) |
| `searxng` | `searxng/searxng:latest` | non-root (capabilities dropped) |
| `openwebui` | `open-webui:main` | root |
| `joplin-server` | `joplin/server:latest` | root |

### Hardening Recommendations

1. **Read-only mounts** — Sensitive volumes (GCS keys, `.env`) are mounted read-only (`:ro`)
2. **Capability dropping** — SearXNG drops all capabilities except `CHOWN`, `SETGID`, `SETUID`
3. **Network isolation** — All services use a private bridge network (`pi_agent_net`)
4. **No privileged mode** — Containers run without `--privileged`
5. **Health checks** — PostgreSQL and Agent API have health checks to detect compromise

### Future Improvements

- Add `USER` directives to Dockerfiles for non-root execution
- Implement Pod Security Policies for Kubernetes deployments
- Add seccomp and AppArmor profiles

---

## Dependency Security

### Python Dependencies

All Python services use pinned versions in `requirements.txt` files:

- `agent/requirements.txt` — LangGraph, FastAPI, pgvector
- `analysis/requirements.txt` — R/Python data science stack
- `scheduler/requirements.txt` — APScheduler, ingestion scripts
- `joplin-mcp/requirements.txt` — MCP server dependencies

### Scanning for Vulnerabilities

Run dependency checks before deployment:

```bash
# Check for known vulnerabilities in installed packages
pip install pip-audit
pip-audit -r agent/requirements.txt
pip-audit -r analysis/requirements.txt
pip-audit -r scheduler/requirements.txt

# Alternative: pip check for dependency conflicts
pip check
```

### Dependency Update Policy

1. Review `dependabot` or `renovate` PRs within 14 days
2. Test against staging environment before merging
3. Pin all transitive dependencies after verification

---

## Data Privacy

### Self-Hosted by Default

Parsnip stores all data locally:

- **PostgreSQL** — Conversation history, embeddings, memories, ingestion state
- **Local volumes** — Docker volumes for `pgdata`, `owui_data`, `pipelines_data`, `analysis_output`
- **No telemetry** — No outbound data sharing except for configured LLM providers

### External Integrations

Data leaves your infrastructure only when explicitly configured:

| Integration | Data Sent | Purpose |
|-------------|-----------|---------|
| Ollama Cloud / OpenRouter | Prompts, context | LLM inference |
| News API, Tavily, Brave | Search queries | Web search |
| GitHub API | Repository queries | Code retrieval |
| Google Cloud Storage | Backups (if configured) | Archive storage |

### Data Retention

- **Conversation state** — Persistent until manually deleted via API or UI
- **Ingestion data** — Stored in `ingestion/data/` (git-ignored)
- **Backups** — User-configured via GCS or local scripts

---

## Operational Security

### Pre-Deployment Checklist

- [ ] Change all default passwords in `.env`
- [ ] Generate random `WEBUI_SECRET_KEY` (e.g., `openssl rand -hex 32`)
- [ ] Review exposed ports in `docker-compose.yml`
- [ ] Enable firewall rules for non-public services
- [ ] Run `pip-audit` on all requirements files
- [ ] Scan Docker images with `trivy` or `grype`

### Runtime Monitoring

```bash
# Check service health
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/stats

# Review container logs for anomalies
docker compose logs --tail=100 agent
docker compose logs --tail=100 postgres
```

### Incident Response

If you suspect a security incident:

1. **Isolate** — Stop affected containers: `docker compose stop <service>`
2. **Preserve** — Export logs: `docker compose logs > incident-logs.txt`
3. **Rotate** — Change all exposed secrets in `.env`
4. **Report** — Follow vulnerability disclosure process above

---

## Known Limitations

- Containers run as root (except PostgreSQL and SearXNG)
- No built-in audit logging for API access
- No rate limiting on local endpoints
- Secrets stored in environment variables (not encrypted at rest)
- No multi-factor authentication for admin interfaces

---

## Security Research Guidance

### Safe Testing Practices

- Test on isolated networks
- Use synthetic data, not production datasets
- Document all findings with reproduction steps
- Coordinate disclosure before publishing

### Out of Scope

- Vulnerabilities requiring physical access
- Attacks requiring access to `.env` or container internals
- Theoretical attacks without proof-of-concept

---

## License and Disclaimer

This software is provided "as is" under the Apache License 2.0. It is experimental research infrastructure and has not undergone formal security auditing. Deploy at your own risk.

For production use, implement additional controls:

- Network segmentation
- Intrusion detection
- Regular penetration testing
- Formal security review

---

**Acknowledgment:** We appreciate responsible disclosure and will credit researchers who report valid security issues (with permission).
