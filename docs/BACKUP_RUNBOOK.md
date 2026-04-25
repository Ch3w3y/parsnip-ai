# Backup Runbook

> **Warning:** Backups are snapshot artifacts. They are not replacements for the live database volume. Test recovery from backup artifacts before relying on them for production operations.

## 1. Overview

`parsnip-ai` uses a layered defense-in-depth backup strategy with four independent mechanisms:

- **RPO ≤ 5 minutes** — WAL archives are pushed at most 5 minutes behind real time.
- **Fresh-to-working restore in < 30 minutes** — on a clean host, the restore script pulls, decrypts, restores the database, starts the stack, and verifies row counts in a single command.

| Layer | Type | What it protects | Frequency |
|-------|------|------------------|-----------|
| pgBackRest | Physical | Entire PostgreSQL cluster + WAL | Weekly full + daily diff + continuous WAL |
| Parquet logical | Logical | All KB tables, notes, memories, checkpoints | Hourly incremental + weekly full |
| Config | Archive | Code, configs, secrets, volume manifest | Daily |
| Volume sync | File | Docker volumes (`analysis_output`, `owui_data`, `pipelines_data`) | Daily |

If one layer is corrupted or incomplete, the others provide independent recovery paths.

---

## 2. Backup Inventory

### 2.1 pgBackRest (physical)

pgBackRest runs inside the `postgres` container and archives WAL asynchronously to GCS.

| Setting | Value |
|---------|-------|
| Repo | GCS bucket (`repo1-gcs-bucket=${GCS_BUCKET}`) |
| Compression | zstd level 3 |
| Archive timeout | 5 minutes (`archive_timeout=300`) |
| Retention | 4 weekly full backups (`repo1-retention-full=4`) |

Scheduled from `/etc/cron.d/pgbackrest` inside the container:

- **Full backup** — Sunday 03:00 UTC
- **Differential backup** — Monday–Saturday 03:00 UTC
- **Expire old backups** — Monday 04:00 UTC
- **Health check / info** — every 6 hours

Check status:

```bash
docker compose exec postgres pgbackrest --stanza=parsnip info
```

### 2.2 Parquet logical (`backup_kb.py`)

Logical exports of individual tables to Parquet partitions on GCS.

**Tables covered:**

- `knowledge_chunks` (content + embeddings)
- `agent_memories`
- `notes`, `notebooks`, `note_resources`, `note_tags`, `tags`
- `hitl_sessions`, `thread_metadata`
- `forex_rates`, `world_bank_data`
- LangGraph: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`
- Joplin legacy: `items` (if applicable)

Modes:

```bash
python scripts/backup_kb.py                    # incremental → GCS, all tables
python scripts/backup_kb.py --mode full        # full snapshot
python scripts/backup_kb.py --local            # local only
python scripts/backup_kb.py --table notes      # single table incremental
```

A JSON manifest at `gs://<bucket>/backups/parquet/_manifest.json` tracks the `last_updated_at` cutoff per table and mode. Restore reads all partitions and deduplicates with `ON CONFLICT DO NOTHING`.

### 2.3 Config (`backup_config.py`)

Splits project state into three artifacts to limit blast radius:

| Artifact | Encryption | Contents |
|----------|------------|----------|
| `config.tar.gz` | Plain | Docker Compose, scripts, plain configs, manifest |
| `secrets.tar.gz.age` | age-encrypted | `.env`, `gcs-key.json` |
| `volume_manifest.json` | Plain | Docker volume list + sizes |

The `AGE_RECIPIENT` public key (from `.env`) is used for encryption. If the env var or `age` binary is missing, the secrets bundle is **skipped with a clear warning** — never silently embedded in the plain tarball.

```bash
python scripts/backup_config.py                # full split, upload to GCS
python scripts/backup_config.py --local        # local only
python scripts/backup_config.py --no-secrets   # skip secrets bundle
```

### 2.4 Volume sync (`sync_volumes.py`)

Syncs named Docker volumes to GCS for disaster recovery.

Volumes: `analysis_output`, `owui_data`, `pipelines_data`.

Strategy: file-by-file upload, skip if the GCS blob already exists with a matching MD5 hash. Additive-only — objects are never deleted by this script.

```bash
python scripts/sync_volumes.py                       # all volumes
python scripts/sync_volumes.py --volume analysis_output
python scripts/sync_volumes.py --dry-run              # list what would change
```

---

## 3. Schedule

All backup jobs are defined in `scheduler/scheduler.py` and run in the scheduler container. pgBackRest jobs use the container's internal cron daemon.

| Job | Frequency | UTC Time | Script |
|-----|-----------|----------|--------|
| KB incremental | Hourly | :15 | `backup_kb.py` |
| KB full | Weekly | Sun 02:30 | `backup_kb.py --mode full` |
| Config backup | Daily | 01:00 | `backup_config.py` |
| Volume sync | Daily | 04:30 | `sync_volumes.py` |
| pgBackRest full | Weekly | Sun 03:00 | `pgbackrest --type=full backup` |
| pgBackRest diff | Daily | Mon–Sat 03:00 | `pgbackrest --type=diff backup` |
| pgBackRest expire | Weekly | Mon 04:00 | `pgbackrest expire` |
| pgBackRest info | Every 6h | 00:00, 06:00, … | `pgbackrest info` |

The KB full (Sun 02:30) is intentionally scheduled **before** the pgBackRest full (Sun 03:00).

---

## 4. Quick Start

### 4.1 Check backup status

```bash
# pgBackRest
docker compose exec postgres pgbackrest --stanza=parsnip info

# KB manifest
gsutil cat gs://$GCS_BUCKET/backups/parquet/_manifest.json | jq .

# Config latest
gsutil ls gs://$GCS_BUCKET/backups/config/

# Volume sync state
gsutil ls gs://$GCS_BUCKET/volumes/
```

### 4.2 Trigger a manual backup

```bash
python scripts/backup_kb.py
python scripts/backup_config.py
```

### 4.3 Decrypt secrets

```bash
age -d -i ~/.config/parsnip/age.key secrets.tar.gz.age
```

If the key is lost, encrypted secrets in GCS are **unrecoverable**.

---

## 5. Restore Procedures

### 5.1 Point-in-time restore

```bash
./scripts/restore_stack.sh --from-gcs --at "2026-04-25 10:00"
```

Phases executed:

1. Pull `config.tar.gz` + `secrets.tar.gz.age` from GCS; decrypt secrets with the age key.
2. Extract config and secrets into the current directory.
3. Start the `postgres` container and wait for health.
4. Run `pgbackrest restore` (with `--type=time --target="..."` if specified).
5. Start the rest of the stack.
6. Rsync volume contents from GCS into Docker volumes.
7. Verify row counts for key tables.

### 5.2 Sandbox restore (dry run)

Test a restore without affecting the live stack:

```bash
./scripts/restore_stack.sh --from-gcs --target sandbox --dry-run
```

This uses `docker-compose.restore-test.yml` (generated by `scripts/make_sandbox_compose.sh`) and prints what would happen without making changes.

### 5.3 Post-restore verification

After any restore, verify:

1. **Row counts** — the restore script prints counts for `knowledge_chunks`, `agent_memories`, `notes`, `notebooks`, and `note_resources`. Compare against the last `RESTORE_RECEIPT_*.md` or baseline.
2. **Sample byte-equality** — compare known file hashes or checksums in `analysis_output` against the source GCS objects.
3. **Embedding cosine similarity** — for vector tables, spot-check random embeddings and confirm `cosine_similarity >= 0.9999` against a known reference.

---

## 6. Troubleshooting

### 6.1 WAL archive failures

**Symptoms:** `pgbackrest info` shows missing WAL segments or `archive-async` queue warnings.

**Checks:**

```bash
docker compose logs postgres | grep -i "archive"
docker compose exec postgres pgbackrest --stanza=parsnip check
```

**Fixes:**
- Verify GCS credentials (`gcs-key.json`) are mounted and valid.
- Check disk space inside the container (`/var/spool/pgbackrest`).
- Ensure the container is not restarted during active archiving.

### 6.2 Age key lost

**Impact:** `secrets.tar.gz.age` is unrecoverable without the private key.

**Mitigation:**
- Keep the private key (`~/.config/parsnip/age.key`) in a password manager.
- Rotate the key with `./scripts/setup_age_key.sh --rotate` (keeps the old key as `.key.bak.*`).

### 6.3 pgBackRest `stanza-create` requires DB restart

Creating or reinitializing a stanza needs exclusive `PGDATA` access:

```bash
docker compose stop postgres
docker compose run --rm --user postgres --entrypoint '' postgres pgbackrest --stanza=parsnip stanza-create
docker compose up -d postgres
```

### 6.4 Sandbox restore competes for resources

The sandbox stack uses the same default ports as the live stack. Generate a sandbox compose with port overrides before restoring:

```bash
./scripts/make_sandbox_compose.sh  # creates docker-compose.restore-test.yml
./scripts/restore_stack.sh --target sandbox --dry-run
```

---

## 7. Reference

### 7.1 Scripts

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `scripts/backup_kb.py` | Logical Parquet backup | `--mode full`, `--local`, `--table <table>` |
| `scripts/backup_config.py` | Config + secrets + manifest | `--local`, `--no-secrets` |
| `scripts/sync_volumes.py` | Volume sync to GCS | `--volume <vol>`, `--dry-run` |
| `scripts/restore_stack.sh` | One-shot stack restore | `--from-gcs`, `--at "..."`, `--target sandbox`, `--dry-run`, `--age-key <path>` |
| `scripts/setup_age_key.sh` | Generate / rotate age keypair | `--print-public`, `--rotate` |

### 7.2 Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `GCS_BUCKET` | All backup scripts | Target GCS bucket for all artifacts |
| `AGE_RECIPIENT` | `backup_config.py` | Public age key for encrypting secrets |
| `DATABASE_URL` | `backup_kb.py` | PostgreSQL connection string |

### 7.3 Cron schedule reference

- `0  3  *   * 0` — pgBackRest full
- `0  3  *   * 1-6` — pgBackRest diff
- `0  4  *   * 1` — pgBackRest expire
- `0 */6  *   * *` — pgBackRest info check
- `15 *  *   * *` — KB incremental (`backup_kb.py`)
- `30 2  *   * 0` — KB full (`backup_kb.py --mode full`)
- `0  1  *   * *` — Config backup (`backup_config.py`)
- `30 4  *   * *` — Volume sync (`sync_volumes.py`)

---

*Keep this runbook up to date. When backup scripts or schedules change, update the corresponding sections and verify the cron table matches `scheduler/scheduler.py` and `infra/postgres/crontab`.*
