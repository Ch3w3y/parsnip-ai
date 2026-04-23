# Troubleshooting

This guide covers the most common operational problems with the Parsnip stack, with verified commands and error messages drawn directly from the codebase.

For background on architecture and configuration, see:
- [Deployment Guide](DEPLOYMENT.md)
- [Configuration Reference](CONFIGURATION.md)

---

## Quick Reference — Essential Commands

```bash
# Overall status
./pi-ctl.sh status

# Stack services (excludes scheduler)
./pi-ctl.sh stack start|stop|restart|status

# Scheduled ingestion (arXiv, news, Joplin, etc.)
./pi-ctl.sh ingest start|stop|status

# Wikipedia bulk ingest
./pi-ctl.sh wiki start|stop|status

# Health endpoints
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/stats
curl -sS http://localhost:8000/ingestion/status

# Container logs
docker compose logs --tail 50 <service>
```

| Service | Container name | Port | Compose service name |
|---------|----------------|------|----------------------|
| Frontend (assistant-ui) | `pi_agent_frontend` | `3001` | `frontend` |
| Agent API | `pi_agent_backend` | `8000` | `agent` |
| PostgreSQL | `pi_agent_postgres` | `5432` | `postgres` |
| Joplin Server | `pi_agent_joplin` | `22300` | `joplin-server` |
| SearXNG | `pi_agent_searxng` | `8080` | `searxng` |
| Joplin MCP | `pi_agent_joplin_mcp` | `8090` | `joplin-mcp` |
| Analysis | `pi_agent_analysis` | — | `analysis` |
| Pipelines | `pi_agent_pipelines` | `9099` | `pipelines` |
| OpenWebUI | `pi_agent_openwebui` | `3000` | `openwebui` |
| Scheduler | `pi_agent_scheduler` | — | `scheduler` |

---

## 1. Joplin Admin Issues

### Symptom
- Joplin login fails with "Invalid email or password" after recreating the PostgreSQL container.
- Or: admin account uses `admin@localhost` instead of the email you set in `.env`.

### Cause
`joplin-server` reads `JOPLIN_SERVER_ADMIN_EMAIL` **only on first startup when its database (`joplin`) is completely empty**. If you recreate the `postgres` container, the Joplin database may be recreated and the admin account can be initialised with defaults (e.g. `admin@localhost`) instead of your `.env` values.

### Fix

Run the verified repair script:

```bash
./scripts/fix-joplin-admin.sh
```

What it does:
1. Waits up to 60 seconds for the Joplin schema to be ready.
2. Verifies an admin row exists in the `users` table.
3. Generates a bcrypt hash inside the running Joplin container (matching Joplin's own library).
4. Updates the admin email and password to match `JOPLIN_ADMIN_EMAIL` and `JOPLIN_ADMIN_PASSWORD` from `.env`.

If the script outputs:
- `ERROR: POSTGRES_PASSWORD not set` — export or define `POSTGRES_PASSWORD` in `.env`.
- `ERROR: JOPLIN_ADMIN_PASSWORD not set` — define `JOPLIN_ADMIN_PASSWORD` in `.env`.
- `WARNING: No admin user found. Joplin Server may not have finished initialisation.` — ensure the `joplin-server` container is running and has had time to create its schema.
- `ERROR: Failed to generate bcrypt hash. Is the Joplin container running?` — start the `joplin-server` container first: `docker compose up -d joplin-server`.

If there is no admin at all (e.g. you truncated the `joplin` database), you must let Joplin bootstrap from an empty DB again:

```bash
# Ensure the joplin database is empty, then restart joplin-server
docker exec pi_agent_postgres psql -U agent -d joplin -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose restart joplin-server
# Wait ~30s for first-run schema creation, then run:
./scripts/fix-joplin-admin.sh
```

See also: `scripts/bootstrap_joplin_admin.sh` for an alternative bootstrap path.

### Prevention
- Do not delete the `pgdata` Docker volume unless necessary.
- Before recreating the PostgreSQL container, back up the `joplin` database.
- Keep `JOPLIN_ADMIN_EMAIL` and `JOPLIN_ADMIN_PASSWORD` stable in `.env`.

---

## 2. Database Persistence: PGDATA Path Warning

### Symptom
- All data is lost after `docker compose down` followed by `docker compose up -d`.
- PostgreSQL starts with a completely fresh `agent_kb` and `joplin` database.

### Cause
The `timescale/timescaledb-ha:pg16` image uses `PGDATA=/home/postgres/pgdata/data`, **not** `/var/lib/postgresql/data`. If the volume mount points to the wrong path, the database files are written to an unmounted directory inside the container and are destroyed on recreation.

In `docker-compose.yml` the correct mount is:

```yaml
volumes:
  - pgdata:/home/postgres/pgdata/data
```

### Fix

Verify the mount is correct before trusting any deployment:

```bash
docker inspect pi_agent_postgres --format '{{json .Mounts}}'
```

You must see an entry like:

```json
{"Source":"parsnip_pgdata","Destination":"/home/postgres/pgdata/data",...}
```

If `Destination` is `/var/lib/postgresql/data`, data loss will occur on every container recreation.

To correct a misconfigured stack:
1. Stop the stack: `./pi-ctl.sh stack stop`
2. Back up any existing data via `pg_dump` or `scripts/backup_kb.py`.
3. Update `docker-compose.yml` to mount `pgdata:/home/postgres/pgdata/data`.
4. Remove the old (wrong) volume if it exists: `docker volume rm parsnip_pgdata_old`
5. Start the stack: `./pi-ctl.sh stack start`

### Prevention
- Never override the Postgres volume mount to `/var/lib/postgresql/data`.
- Run `docker inspect pi_agent_postgres --format '{{json .Mounts}}'` after the first deployment to confirm.
- Schedule regular backups (see [Storage and Backup](STORAGE_AND_BACKUP.md)).

---

## 3. Port Conflicts

### Symptom
- `docker compose up` fails with messages about a port already being in use.
- Services start but a local process (not Docker) is answering on `3001`, `8000`, or `5432`.
- One of the health checks (`curl http://localhost:8000/health`) returns unexpected HTML or connection refused.

### Cause
Another process on the host is bound to the same port that a Parsnip service is trying to expose.

Common conflicts:
| Port | Service | Common conflicting process |
|------|---------|---------------------------|
| `3001` | assistant-ui (frontend) | Another Next.js dev server, Node app |
| `8000` | Agent API | Another Python/FastAPI service, local Ollama reverse proxy |
| `5432` | PostgreSQL | Local PostgreSQL installed via package manager |
| `3000` | OpenWebUI | Another Next.js app, VS Code tunnel |
| `22300` | Joplin Server | — |
| `8080` | SearXNG | Local Apache, Nginx, Tomcat |

### Fix

Identify what is using the port:

```bash
# Linux
ss -tlnp | grep -E ':3001|:8000|:5432'

# macOS
lsof -i :3001 -i :8000 -i :5432

# Cross-platform fallback
sudo netstat -tlnp 2>/dev/null | grep -E '3001|8000|5432' || \
  sudo ss -tlnp | grep -E '3001|8000|5432'
```

If a non-Docker process is using the port, you have three options:

**Option A — Stop the conflicting process**

```bash
# If local PostgreSQL is running:
sudo systemctl stop postgresql

# If a local dev server is running:
kill $(lsof -t -i :3001) 2>/dev/null
```

**Option B — Change the host-side port in `docker-compose.yml`**

Change the left-hand (host) side of the mapping, for example:

```yaml
services:
  frontend:
    ports:
      - "3002:3000"   # was 3001:3000
```

**Option C — Use `docker-compose.override.yml`** (recommended for local tweaks)

Create `docker-compose.override.yml` in the project root:

```yaml
services:
  postgres:
    ports:
      - "5433:5432"
  frontend:
    ports:
      - "3002:3000"
```

Then run `docker compose up -d` as normal; overrides are merged automatically.

Remember to update any URL references in `.env` (e.g. `DATABASE_URL`, `NEXT_PUBLIC_AGENT_URL`) if you change the postgres or agent ports.

### Prevention
- Before deploying, audit the host: `ss -tlnp` or `lsof -i`.
- Reserve `3001`, `8000`, and `5432` for Parsnip in your local environment.
- Use override files rather than editing `docker-compose.yml` directly.

---

## 4. Analysis Container (amd64-only)

### Symptom
- `docker compose up -d analysis` fails with `no matching manifest for linux/arm64`.
- On macOS with Apple Silicon the container exits immediately or the image build fails.

### Cause
The `analysis` service uses `rocker/tidyverse` as its upstream base image, which **does not publish an `arm64` manifest**. The compose file explicitly pins `platform: linux/amd64`, so Docker refuses to run it on an `arm64` host unless emulation is available.

### Fix

**Check your host architecture:**

```bash
uname -m
# x86_64   -> amd64, should run natively
# aarch64 / arm64 -> ARM, will need emulation or remote build
```

**If you are on ARM (Apple Silicon, Raspberry Pi, Graviton):**

1. **Enable QEMU user-mode emulation** (allows amd64 containers to run on ARM):

```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker compose up -d analysis
```

2. **Or build on an amd64 machine** and push a pre-built image:

```bash
# On an amd64 host
IMAGE_TAG=0.1.0 docker compose build analysis
docker push ghcr.io/ch3w3y/parsnip-ai/analysis:0.1.0
# Then on the ARM host, pull instead of build:
IMAGE_TAG=0.1.0 docker compose up -d --no-build analysis
```

3. **Or skip the analysis service** if you do not need Python/R notebook execution:

```bash
# Start everything except analysis
./pi-ctl.sh stack start
```

The `analysis` service is optional for chat and retrieval; it is only required for agent tool calls that run code, generate plots, or access R.

### Prevention
- On ARM hosts, pre-pull or pre-build the analysis image; do not rely on local build.
- Pin `IMAGE_TAG` and pull from GHCR on non-amd64 hosts (see [Deployment Guide](DEPLOYMENT.md)).

---

## 5. Stuck Ingestion Jobs

### Symptom
- `python scripts/ingestion_status.py` shows jobs stuck in `running` for hours.
- The scheduler container (`pi_agent_scheduler`) has not produced new logs.
- New scheduled ingestion jobs never start because the scheduler thinks a prior job is still active.

### Cause
Jobs are inserted into the `ingestion_jobs` table with `status = 'running'`. If a container crashes, a process is killed (`kill -9`), or the host reboots while a job is active, the row remains `running` indefinitely. The scheduler will not start a new job for the same source until the old one is cleared.

### Fix

**Step 1 — Check current job status**

```bash
# Via CLI helper
python scripts/ingestion_status.py

# Or via the agent API
curl -sS http://localhost:8000/ingestion/status

# Or via pi-ctl
./pi-ctl.sh ingest status
```

**Step 2 — Recover stuck jobs**

Jobs are considered stuck if they have been `running` for longer than `INGESTION_JOB_TIMEOUT_HOURS` (default: 2 hours). The `recover_stuck_jobs` function in `ingestion/utils.py` marks them as `failed`.

You can trigger recovery via the API (if the agent is running) or manually in the database:

```bash
# Direct database recovery — mark all jobs running > 2h as failed
docker exec pi_agent_postgres psql -U agent -d agent_kb -c "
UPDATE ingestion_jobs
SET status = 'failed', finished_at = NOW()
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '2 hours';
"
```

If you want to use a shorter timeout (e.g. 30 minutes), override the env var and run a Python recovery call.

**Step 3 — Restart the scheduler if needed**

```bash
./pi-ctl.sh ingest stop
./pi-ctl.sh ingest start
```

**Step 4 — Check scheduler logs for root cause**

```bash
docker compose logs --tail 100 pi_agent_scheduler
```

### Prevention
- Monitor with `./pi-ctl.sh status` or `python scripts/ingestion_status.py --watch`.
- Set `INGESTION_JOB_TIMEOUT_HOURS` in `.env` if your jobs regularly exceed 2 hours.
- Ensure the scheduler container has a restart policy (`restart: unless-stopped` in `docker-compose.yml`).

---

## 6. Out of Memory (VRAM) During Wikipedia Ingestion

### Symptom
- Wikipedia bulk ingest (`./pi-ctl.sh wiki start`) aborts with GPU out-of-memory errors.
- `rocm-smi` or `nvidia-smi` shows VRAM near 100 %.
- Host desktop becomes sluggish or the display driver crashes.
- Log file `/tmp/wiki_ingest.log` contains embedding errors or process death messages.

### Cause
Wikipedia bulk ingestion embeds batches of text using `mxbai-embed-large` via Ollama. On a typical workflow this occupies **~6 GB of VRAM**. If another GPU workload is running (e.g. local Ollama LLM inference, the scheduler doing news/arXiv embedding, or a game), VRAM can be exhausted and the embedding process will fail or the GPU driver may reset.

### Fix

**Check current VRAM usage before starting:**

```bash
# AMD / ROCm
rocm-smi --showmeminfo vram

# NVIDIA / CUDA
nvidia-smi

# Or use pi-ctl (shows VRAM automatically on wiki status)
./pi-ctl.sh wiki status
```

**Stop competing GPU workloads:**

```bash
# Stop the scheduler (it also runs embed jobs)
./pi-ctl.sh ingest stop

# Stop local Ollama if it is running LLM inference
systemctl stop ollama.service 2>/dev/null || true

# Stop the wiki ingest if you need VRAM for something else
./pi-ctl.sh wiki stop
```

**Resume safely:**

```bash
# Ensure scheduler is stopped
./pi-ctl.sh ingest stop

# Start wiki ingest (auto-resumes from last checkpoint)
./pi-ctl.sh wiki start
```

The script will warn you if the scheduler is running:

```
! Scheduler is running — stopping it to avoid VRAM conflicts...
```

**If you need to run both scheduler and wiki simultaneously**

This is not recommended on a single GPU. Options:
- Offload the scheduler to a CPU-only embedding endpoint (slower but no VRAM conflict).
- Run Wikipedia ingestion on a separate host or during off-hours.
- Reduce `BATCH_SIZE` in the ingestion script (default 64); smaller batches use less VRAM but are slower.

### Prevention
- Run `./pi-ctl.sh wiki status` to monitor VRAM before gaming or other GPU tasks.
- The `pi-ctl.sh wiki start` command prints: `VRAM note: ~6GB occupied while running. Stop before gaming: ./pi-ctl.sh wiki stop`
- Keep `kill -0` monitoring reliable; the PID file is `/tmp/pi-agent-wiki.pid`.

---

## 7. Network / DNS: Docker Bridge Issues

### Symptom
- Containers cannot reach each other by service name (e.g. `pi_agent_backend` cannot connect to `pi_agent_postgres`).
- `curl http://pi_agent_joplin:22300` from inside the agent container returns `Could not resolve host`.
- External API calls from containers time out, but the same call works from the host.
- The frontend (`pi_agent_frontend`) intermittently fails to reach the agent API.

### Cause
The compose stack uses a dedicated Docker bridge network called `pi_agent_net`. If the network is down, misconfigured, or DNS resolution inside the bridge is failing, container-to-container communication breaks.

Known triggers:
- Docker daemon was restarted while the stack was running.
- Network `pi_agent_net` was manually removed (`docker network rm`).
- Host firewall or VPN software is intercepting Docker bridge traffic.
- The frontend container uses custom DNS (`8.8.8.8`, `1.1.1.1` in `docker-compose.yml`); if these are unreachable, external resolution fails.

### Fix

**Verify the network exists:**

```bash
docker network ls | grep pi_agent
```

You should see:

```
pi_agent_net   bridge   local
```

**Inspect the network and attached containers:**

```bash
docker network inspect pi_agent_net
```

All containers must have an `IPv4Address` assigned. If any service is missing, restart it:

```bash
docker compose restart <service>
```

**If the network is missing entirely:**

```bash
docker compose down
docker network create pi_agent_net
docker compose up -d
```

(Using `docker compose up -d` should recreate the network automatically, but `docker compose down` first ensures a clean state.)

**Test DNS from inside a container:**

```bash
# Test internal resolution
docker exec pi_agent_backend nslookup pi_agent_postgres

# Test external resolution
docker exec pi_agent_frontend nslookup google.com
```

If internal resolution fails but external works, the bridge network is likely damaged; recreate it.

If external resolution fails from the frontend but works from the host, the firewall may be blocking UDP 53 outbound from the Docker bridge. Allow that or remove the custom DNS override from `docker-compose.yml`:

```yaml
# Remove or comment out the dns block:
# dns:
#   - 8.8.8.8
#   - 1.1.1.1
```

**Check firewall / VPN**

Some VPN clients (WireGuard, OpenVPN, certain corporate tools) route all traffic through a tunnel interface and can break Docker bridge routing. Temporarily disable the VPN and retest container-to-container pings.

### Prevention
- Do not manually remove `pi_agent_net` while services are running.
- After Docker daemon restarts, run `./pi-ctl.sh stack restart` to restore network state.
- If you run a VPN full-time, whitelist the `172.16.0.0/12` Docker subnet or use `docker network create` with a subnet that does not conflict.

---

## 8. Circuit Breaker Tripped

### Symptom
- Agent API returns errors like `Model circuit is open and all fallback options (including GPU) failed.`
- OpenRouter requests consistently fail with 429, 403, or 402, and no fallback succeeds.
- Logs show: `OpenRouter circuit breaker TRIPPED — rotating to fallback model`

### Cause
The circuit breaker in `agent/graph_guardrails.py` trips when OpenRouter returns quota or rate-limit errors (HTTP 403, 429, 402, or error messages containing `key limit exceeded`, `rate limit`, `quota`, `insufficient_quota`, `payment_required`).

When tripped:
1. A state file is written to `/tmp/parsnip_circuit_breaker.json`.
2. The agent skips the primary model and cascades through fallbacks (`mid-tier` → `low-tier` → GPU Ollama).
3. If all fallbacks also fail, the request is rejected.
4. The circuit **auto-resets after 5 minutes** (300 seconds) of cooldown.

### Fix

**Check the circuit breaker state:**

```bash
cat /tmp/parsnip_circuit_breaker.json 2>/dev/null || echo "Circuit breaker file not found — circuit is closed"
```

If the file exists, it contains JSON like:

```json
{"tripped": true, "tripped_at": 1714500000.0}
```

**Wait for auto-reset** (up to 5 minutes from `tripped_at`), **or force reset immediately:**

```bash
rm -f /tmp/parsnip_circuit_breaker.json
```

After removing the file, the next request will try the primary model again.

**Verify OpenRouter status independently:**

```bash
curl -sS https://openrouter.ai/api/v1/auth/key \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" | jq .
```

If your key is invalid, expired, or over quota, renew or rotate it in `.env` and restart the agent:

```bash
docker compose restart agent
```

**Check fallback tier configuration:**

If the circuit tripped because your account hit a quota, ensure you have fallbacks configured that do **not** rely on the same quota pool:

```ini
# .env — ensure fast model is distinct from smart/reasoning
FAST_MODEL=llama3.1:cloud
SMART_MODEL=kimi-k2.6:cloud
REASONING_MODEL=kimi-k2.6:cloud
```

If you have a local GPU Ollama, ensure `GPU_LLM_URL` and `GPU_LLM_MODEL` are set so the final safety net can serve low-tier requests without touching OpenRouter at all.

### Prevention
- Monitor OpenRouter usage and billing; set up alerts before quotas are exhausted.
- Configure `GUARDRAIL_MODE` in `.env`:
  - `strict` — trips immediately, no fallbacks (avoid unless debugging).
  - `balanced` (default) — 5-minute cooldown, cascading fallback.
  - `lenient` — extended cooldown, favours continued operation.
- Set a distinct low-tier or local model for `FAST_MODEL` so the fallback chain can escape rate-limit pools.
- See [Configuration Reference](CONFIGURATION.md) for guardrail mode details.

---

## 9. Missing Environment Variables

### Symptom
- Services fail to start with obscure PostgreSQL authentication errors.
- Agent container exits immediately with no logs or a Python traceback ending in `KeyError` or `pydantic.ValidationError`.
- Joplin admin fixes report `ERROR: POSTGRES_PASSWORD not set`.
- Frontend shows a blank page or fails to connect to `http://localhost:8000`.

### Cause
`.env` is out of date, missing required keys, or was copied from an old version of `.env.example` that did not include newer variables.

### Fix

**Step 1 — Audit `.env` against the current contract**

```bash
diff -u .env.example .env | grep "^-[A-Z]" | head -30
```

(Variables that exist in `.env.example` but are missing or different in `.env` will appear with `-` prefixes; review carefully.)

**Step 2 — Required baseline checklist**

| Variable | Required when | Symptom if missing |
|----------|---------------|---------------------|
| `POSTGRES_PASSWORD` | Always | PostgreSQL container rejects connections; `fix-joplin-admin.sh` fails |
| `DATABASE_URL` | Always | Agent cannot connect to knowledge base; ingestion fails |
| `WEBUI_SECRET_KEY` | Always | OpenWebUI refuses to start or sessions break |
| `LLM_PROVIDER` | Always | Agent defaults to `openrouter`; if unset and no key present, requests fail |
| `OPENROUTER_API_KEY` | `LLM_PROVIDER=openrouter` | 401 / 403 from OpenRouter; circuit breaker trips |
| `OPENAI_COMPAT_BASE_URL` + `OPENAI_COMPAT_API_KEY` | `LLM_PROVIDER=openai_compat` | Agent cannot reach LLM endpoint |
| `OLLAMA_BASE_URL` | Always (for embeddings) | Embedding failures; ingestion produces no vectors |
| `JOPLIN_ADMIN_EMAIL` | If using Joplin | Admin login may use wrong default email |
| `JOPLIN_ADMIN_PASSWORD` | If using Joplin | `fix-joplin-admin.sh` fails with "not set" error |
| `NEXT_PUBLIC_AGENT_URL` | If using frontend | Browser cannot reach agent API (blank UI / CORS errors) |

**Step 3 — Regenerate `.env` safely**

```bash
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
cp .env.example .env
```

Now edit `.env` and fill in your real secrets. Do **not** commit `.env` to git.

**Step 4 — Restart the stack**

```bash
./pi-ctl.sh stack restart
./pi-ctl.sh ingest restart
```

**Step 5 — Verify**

```bash
curl -sS http://localhost:8000/health
docker compose ps
```

### Prevention
- After every pull / upgrade, run `diff -u .env.example .env` to spot new required variables.
- Keep `.env` in `.gitignore` and back it up securely (e.g. password manager or encrypted store).
- Use `scripts/setup.sh` for first-time environment checks.

---

## Appendix A: Evidence and Sources

Every command, error message, and file path in this document was verified against the codebase and saved to:

```
.sisyphus/evidence/task-11-troubleshoot-cmds.txt
```

Key source files consulted:
- `docker-compose.yml` — service names, ports, volumes, network configuration
- `pi-ctl.sh` — stack control commands, VRAM checks, wiki scheduler logic
- `scripts/fix-joplin-admin.sh` — admin repair steps and error messages
- `agent/graph_guardrails.py` — circuit breaker implementation and error strings
- `tests/test_circuit_breaker.py` — circuit breaker validation
- `ingestion/utils.py` — stuck job recovery (`recover_stuck_jobs`)
- `agent/tools/system.py` — system status checks
- `.env.example` — required variable contract
- `docs/DEPLOYMENT.md` — Joplin fix script reference, PGDATA verification, backup schedule
- `docs/CONFIGURATION.md` — circuit breaker env var, guardrail modes, model aliases

---

## Appendix B: One-Page Emergency Cheat Sheet

```bash
# 1. What is actually running?
./pi-ctl.sh status

# 2. Is the agent healthy?
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/stats

# 3. Is the DB reachable and correct?
docker exec pi_agent_postgres pg_isready -U agent -d agent_kb
docker inspect pi_agent_postgres --format '{{json .Mounts}}'

# 4. Any stuck jobs?
python scripts/ingestion_status.py
curl -sS http://localhost:8000/ingestion/status

# 5. Reset stuck jobs (running > 2h)
docker exec pi_agent_postgres psql -U agent -d agent_kb -c "
UPDATE ingestion_jobs
SET status='failed', finished_at=NOW()
WHERE status='running' AND started_at < NOW() - INTERVAL '2 hours';"

# 6. Joplin admin broken?
./scripts/fix-joplin-admin.sh

# 7. Circuit breaker tripped?
cat /tmp/parsnip_circuit_breaker.json 2>/dev/null || echo "OK — circuit closed"
rm -f /tmp/parsnip_circuit_breaker.json   # force reset

# 8. Out of VRAM?
./pi-ctl.sh wiki stop
./pi-ctl.sh ingest stop

# 9. Port conflict?
ss -tlnp | grep -E ':3001|:8000|:5432'

# 10. Network broken?
docker network ls | grep pi_agent
docker network inspect pi_agent_net
docker exec pi_agent_backend nslookup pi_agent_postgres
```
