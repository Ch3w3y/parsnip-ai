# Deployment

## Local Docker Deployment (Baseline)

### Option A: Build locally

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

### Option B: Pull pre-built images from GHCR

```bash
cp .env.example .env
IMAGE_TAG=0.1.0 docker compose up -d --no-build
docker compose ps
```

Pre-built images are published to `ghcr.io/ch3w3y/parsnip-ai/*` for every release.
Available tags: `latest`, `0.1.0`, `0.1`, `0`, plus short-SHA.

> **Note:** The `analysis` image is `linux/amd64` only because the upstream
> `rocker/tidyverse` base does not publish an `arm64` manifest. All other
> services support both `amd64` and `arm64`.

Verify:
- OpenWebUI: `http://localhost:3000`
- Agent: `http://localhost:8000/health`
- Pipelines: `http://localhost:9099/`

## Production Guidance

### GCP (recommended first full path)

1. Provision VM (Docker-capable) and managed PostgreSQL (or self-host Postgres).
2. Configure environment variables and secret manager integration.
3. Set ingress/reverse proxy with TLS for `3000/8000/9099`.
4. Configure object storage credentials if using external artifact storage.
5. Run compose stack and health checks.
6. Add uptime monitoring + log aggregation.

### AWS / Azure

Use same service split:
- Compute: VM/container host.
- Database: managed PostgreSQL.
- Object storage: S3 / Blob.
- Secret management: platform secret store.
- TLS edge: managed load balancer + certificates.

### VM + Managed DB + SaaS Hybrids

Supported practical patterns:
- Single VM + managed Postgres.
- VM + Neon-like serverless Postgres.
- VM + object storage + CI-driven updates.

## Operational Baselines

- Keep `.env` out of git; inject via secure secret channel.
- Enforce regular backup + restore drills.
- Pin image tags for reproducible upgrades (e.g. `IMAGE_TAG=0.1.0`).
- Pull from GHCR in production instead of building on the host.
- Run secret scan before every release.
