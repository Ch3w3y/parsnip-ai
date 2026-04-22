# Storage and Backup Guidance

This project separates live state from backup artifacts.

Live databases require block storage. Object storage such as GCS or S3 is used
for exported Parquet files, configuration archives, and optional generated
artifacts. It must not be mounted as the PostgreSQL data directory.

## Live State

| State | Current location |
|-------|------------------|
| PostgreSQL data | Docker volume `pgdata` mounted at `/home/postgres/pgdata/data`. |
| Agent KB, memories, checkpoints | PostgreSQL database `agent_kb`. |
| Joplin application data | PostgreSQL database `joplin`. |
| OpenWebUI state | Docker volume `owui_data`. |
| Pipelines state | Docker volume `pipelines_data`. |
| Analysis outputs | Docker volume `analysis_output`. |
| Raw ingestion landing files | `ingestion/data/` bind mount. |

The Timescale/PostgreSQL image used by the stack expects its data directory at
`/home/postgres/pgdata/data`. Mounting the volume elsewhere can make data appear
to disappear after container recreation.

## Backup Artifacts

`scripts/backup_kb.py` exports database data to dated local directories and, when
`GCS_BUCKET` is configured, uploads the same files to GCS:

```text
gs://<bucket>/backups/YYYY-MM-DD/knowledge_chunks.parquet
gs://<bucket>/backups/YYYY-MM-DD/agent_memories.parquet
gs://<bucket>/backups/YYYY-MM-DD/joplin_items.parquet
gs://<bucket>/backups/YYYY-MM-DD/metadata.json
gs://<bucket>/backups/latest/...
```

`scripts/backup_config.py` creates a compressed archive of selected project
configuration, code, docs, scheduler scripts, ingestion scripts, and the
OpenWebUI SQLite database when present:

```text
gs://<bucket>/backups/config/parsnip_config_<timestamp>.tar.gz
gs://<bucket>/backups/config/latest.tar.gz
```

The scheduler runs both backups on startup, then runs KB backups four times a day
and config backups daily. See `docs/DEPLOYMENT.md` for the exact schedule.

## Object Storage Boundary

Do not use `gcsfuse`, `s3fs`, or similar object-store mounts for live
PostgreSQL, SQLite, Joplin, or OpenWebUI database files. Object stores do not
provide the latency, locking, overwrite, or `fsync` behavior required by
databases.

Acceptable object-storage uses:

- Parquet backup exports.
- Compressed configuration archives.
- Analysis artifacts that are written as complete files.
- Long-term archival copies of raw ingestion data.

## Production Patterns

For a single VM, use Docker volumes backed by the VM disk or attached persistent
block storage, then ship backups to object storage.

For a managed-cloud deployment, point `DATABASE_URL` and `JOPLIN_DATABASE_URL` at
managed PostgreSQL and keep the application containers stateless where practical.
The same backup scripts can still export to object storage.

For a clustered deployment, use a proper block-storage layer for PostgreSQL and
application state. Object storage remains the disaster-recovery target, not the
live filesystem.
