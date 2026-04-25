# Autopilot Plan — Backup & Recovery Overhaul

**Goal:** physical PITR via pgBackRest + incremental logical Parquet + volume sync + encrypted secrets + one-shot restore orchestrator + frontend admin UI. End state: a fresh empty host can be reseeded to working stack from GCS in <30 min with ≤5 min RPO.

## Architecture Decisions (locked)
1. **pgBackRest** (sidecar) → GCS, weekly full / daily diff / 5-min WAL
2. **Keep Parquet logical exports** but rewrite as **incremental partitioned** with manifest
3. **age-encrypt secrets subset** (`.env`, `gcs-key.json`) → `secrets.tar.gz.age`
4. **Joplin gap → backup `agent_kb` normalized tables** (notes/notebooks/note_resources/note_tags/tags/hitl_sessions/thread_metadata) + LangGraph checkpoint tables + legacy `joplin.items` fallback
5. **age key location**: `~/.config/parsnip/age.key` (chmod 600)
6. **Restore script flag-driven**: `restore_stack.sh --from-gcs --at <ts> --age-key <path>`
7. **Live drill required** before validation — sandbox restore + content parity check
8. **Frontend admin** at `/admin` (env-gated `ADMIN_ENABLED=true` + `ADMIN_TOKEN` header for v1)

## Build Order (dependency-respecting)

### Unit A — DR Infrastructure (CLI-functional)
A1. `infra/pgbackrest/{Dockerfile,pgbackrest.conf,entrypoint.sh}` — sidecar image with crond
A2. `db/postgresql.conf.d/wal_archiving.conf` — `wal_level=replica`, `archive_mode=on`, `archive_command='pgbackrest --stanza=parsnip archive-push %p'`
A3. `docker-compose.yml`:
    - `pgbackrest` sidecar: shares `pgdata` RW, mounts `gcs-key.json`, network `pi_agent_net`
    - `postgres` service: add `command:` overlay applying WAL config, mount conf
A4. `scripts/sync_volumes.py` — GCS rsync for `analysis_output`, `owui_data`, `pipelines_data`
A5. `scripts/backup_kb.py` — INCREMENTAL rewrite:
    - manifest at `gs://.../parquet/_manifest.json`, tracks `(table, last_updated_at)` per source
    - daily incremental: `WHERE updated_at > last_cutoff`
    - weekly Sunday full snapshot
    - **add tables**: `notes`, `notebooks`, `note_resources`, `note_tags`, `tags`, `hitl_sessions`, `thread_metadata`
    - **add tables**: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` (LangGraph)
    - **add tables**: `forex_rates`, `world_bank_data` (structured)
    - keep `joplin.items` as legacy fallback (low priority)
A6. `scripts/backup_config.py` — split:
    - `config.tar.gz` (plain): docker-compose, code subset, ARCHITECTURE.md
    - `secrets.tar.gz.age` (age-encrypted): `.env`, `gcs-key.json`
    - `volume_manifest.json` (plain): which volumes exist, sizes
A7. `scripts/restore_stack.sh` — orchestrator:
    - phase 1: pull config tarball + secrets, decrypt
    - phase 2: `docker compose up -d postgres`, wait healthy
    - phase 3: `pgbackrest restore` (stanza), `--target-time` if requested
    - phase 4: `docker compose up -d` (rest)
    - phase 5: `gsutil rsync` volumes back
    - phase 6: verification — counts, sample byte-equality
A8. `scripts/setup_age_key.sh` — generate keypair, store privkey at `~/.config/parsnip/age.key`, public key in `.env`
A9. `scheduler/scheduler.py` — replace 4×/day full with hourly incremental, add weekly full Sunday 03:00, add volume sync daily 04:00, add stanza-create one-shot

### Unit A Tests
T1. `tests/backup/test_backup_kb_incremental.py` — manifest lifecycle, cutoff logic
T2. `tests/backup/test_backup_config_secrets.py` — age round-trip, secrets isolation
T3. `tests/backup/test_sync_volumes.py` — rsync diff logic, deletion safety
T4. `tests/backup/test_restore_stack_orchestration.py` — phase ordering, dry-run mode

### Unit A Live Drill (Phase 3.5 — BLOCKS completion)
D1. Run `pgbackrest stanza-create` + first full → GCS (background, monitor)
D2. Run `backup_kb.py` once → verify Parquet partition + manifest in GCS
D3. Run `sync_volumes.py` once → verify all three volumes uploaded
D4. Run `backup_config.py` once → verify `secrets.tar.gz.age` decrypts cleanly
D5. **Sandbox restore**:
    - Compose project `parsnip-restore-test` on isolated network, port 15432
    - Fresh `pgdata-test` volume
    - `restore_stack.sh --from-gcs --target sandbox`
    - Compare row counts: knowledge_chunks, agent_memories, notes, notebooks, note_resources
    - Sample 10 random chunks: byte-equal `content`, cosine ≥0.9999 on `embedding`
    - Tear down, leave receipt
D6. Commit `docs/RESTORE_RECEIPT_<ts>.md` with all metrics

### Unit B — Frontend Admin UI (after Unit A drill green)
B1. `agent/main.py` admin endpoints (gated by `ADMIN_ENABLED` env + `X-Admin-Token` header):
    - `GET /admin/stack/health` — query Docker socket OR ping each service /health
    - `GET /admin/backups/list?type=pg|parquet|volumes|config`
    - `POST /admin/backups/trigger` — body: `{type}` → kicks scheduler job
    - `GET /admin/restore/preview?backup_id=...` — show what would be restored
    - `POST /admin/restore/execute` — requires `confirm_token` matching server-generated nonce
2. `frontend/src/app/admin/page.tsx` + tabs:
    - Stack Health (container grid, uptime, /health badges)
    - Knowledge Base (move existing KB stats here)
    - Backups (list, trigger, restore wizard with type-to-confirm)
3. `frontend/src/lib/admin-api.ts` — typed client wrappers

### Phase 5 — Validation
- `oh-my-claudecode:architect` — completeness review
- `oh-my-claudecode:security-reviewer` — age key handling, admin auth, secret leakage
- `oh-my-claudecode:code-reviewer` — quality, conventions, edge cases

### Phase 6 — Docs & Cleanup
- Rewrite `docs/ARCHITECTURE_VISUALS.md` section 7 (Backup and Recovery Flow)
- Add `docs/BACKUP_RUNBOOK.md` — operator quickstart
- Delete `.omc/state/autopilot-state.json` etc.

## Risk Register
| Risk | Mitigation |
|---|---|
| WAL archive failures fill pg disk | `archive_command` returns non-zero only on hard fail; pgBackRest async push to GCS; monitor `pg_stat_archiver` |
| age key lost = secrets unrecoverable | Setup script prints key once with bold "STORE IN PASSWORD MANAGER" warning; `--age-key` flag accepts file path |
| pgBackRest first stanza-create requires DB restart | Documented in runbook + restore script; one-time cost |
| Sandbox restore competes for resources with live stack | Restore project uses isolated volume + port + network namespace |
| Parquet manifest race if backup runs concurrent | File lock via `fcntl.flock` on local manifest before GCS upload |

## Success Criteria
- [ ] `pgbackrest info` shows ≥1 full backup in GCS
- [ ] `backup_kb.py` incremental run completes in <60s for current DB size
- [ ] `secrets.tar.gz.age` decrypts cleanly with stored key
- [ ] Sandbox restore: row counts match within ±0; sample content byte-equal
- [ ] `RESTORE_RECEIPT_<ts>.md` committed
- [ ] All tests pass; ruff clean
- [ ] Validators approve
- [ ] `docs/ARCHITECTURE_VISUALS.md` section 7 reflects new flow
