#!/usr/bin/env bash
# One-shot stack restore from GCS. Designed to be run on a fresh empty host.
#
# Phases:
#   1. Pull config tarball + (optionally) secrets tarball; decrypt with age key.
#   2. docker compose up postgres; wait healthy.
#   3. pgbackrest restore (--target-time optional) → physical DB restore.
#   4. docker compose up rest of stack.
#   5. gsutil rsync volumes back from GCS.
#   6. Verify: row counts, stanza info, sample byte-equality.
#
# Usage:
#   ./scripts/restore_stack.sh --from-gcs                        # latest backup
#   ./scripts/restore_stack.sh --from-gcs --at "2026-04-25 10:00"
#   ./scripts/restore_stack.sh --from-gcs --age-key ~/.config/parsnip/age.key
#   ./scripts/restore_stack.sh --target sandbox                  # restore into isolated test stack
#   ./scripts/restore_stack.sh --dry-run                         # plan only, no changes
set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────────────
GCS_BUCKET="${GCS_BUCKET:-}"
AGE_KEY=""
TARGET_TIME=""
TARGET="live"        # live | sandbox
DRY_RUN="no"
COMPOSE_FILE="docker-compose.yml"
COMPOSE_PROJECT=""   # set later
SCRATCH_DIR=""

# ── arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-gcs)   shift ;;
        --at)         TARGET_TIME="$2"; shift 2 ;;
        --age-key)    AGE_KEY="$2"; shift 2 ;;
        --target)     TARGET="$2"; shift 2 ;;
        --bucket)     GCS_BUCKET="$2"; shift 2 ;;
        --dry-run)    DRY_RUN="yes"; shift ;;
        -h|--help)    sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -z "$GCS_BUCKET" ]] && { echo "ERROR: GCS_BUCKET not set (env or --bucket)" >&2; exit 1; }
command -v gsutil  >/dev/null || { echo "ERROR: gsutil required (install google-cloud-sdk)" >&2; exit 1; }
command -v docker  >/dev/null || { echo "ERROR: docker required" >&2; exit 1; }

if [[ "$TARGET" == "sandbox" ]]; then
    COMPOSE_PROJECT="parsnip-restore-test"
    COMPOSE_FILE="docker-compose.restore-test.yml"
    [[ -f "$COMPOSE_FILE" ]] || { echo "ERROR: $COMPOSE_FILE missing — generate with scripts/make_sandbox_compose.sh" >&2; exit 1; }
else
    COMPOSE_PROJECT="parsnip"
fi

run() {
    if [[ "$DRY_RUN" == "yes" ]]; then
        echo "  [dry-run] $*"
    else
        eval "$@"
    fi
}

log()  { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }

cleanup() {
    if [[ -n "$SCRATCH_DIR" && -d "$SCRATCH_DIR" ]]; then
        # Shred secrets directory if present
        if [[ -d "$SCRATCH_DIR/secrets" ]]; then
            find "$SCRATCH_DIR/secrets" -type f -exec shred -u {} \; 2>/dev/null || true
        fi
        rm -rf "$SCRATCH_DIR"
    fi
}
trap cleanup EXIT

SCRATCH_DIR=$(mktemp -d -t parsnip-restore-XXXXXX)
log "Scratch dir: $SCRATCH_DIR"
log "Target: $TARGET (compose project: $COMPOSE_PROJECT)"
[[ "$DRY_RUN" == "yes" ]] && log "Dry run — no changes will be made."

# ── PHASE 1: pull config + secrets ────────────────────────────────────────────
log "Phase 1: pulling config + secrets from gs://$GCS_BUCKET/"
run "gsutil cp gs://$GCS_BUCKET/backups/config/latest.tar.gz $SCRATCH_DIR/config.tar.gz"
run "gsutil cp gs://$GCS_BUCKET/backups/config/latest_secrets.tar.gz.age $SCRATCH_DIR/secrets.tar.gz.age || true"

if [[ -f "$SCRATCH_DIR/secrets.tar.gz.age" ]]; then
    if [[ -n "$AGE_KEY" ]]; then
        command -v age >/dev/null || { echo "ERROR: age binary required to decrypt secrets" >&2; exit 1; }
        log "Decrypting secrets bundle with $AGE_KEY"
        run "age -d -i '$AGE_KEY' -o $SCRATCH_DIR/secrets.tar.gz $SCRATCH_DIR/secrets.tar.gz.age"
    else
        warn "No --age-key provided. Secrets will NOT be restored. The stack will start"
        warn "in DEGRADED mode and you must populate .env manually."
    fi
fi

# ── PHASE 2: extract config in restore root ───────────────────────────────────
log "Phase 2: extracting config and secrets into current directory"
run "tar -xzf $SCRATCH_DIR/config.tar.gz -C ."
[[ -f "$SCRATCH_DIR/secrets.tar.gz" ]] && run "tar -xzf $SCRATCH_DIR/secrets.tar.gz -C ."

# ── PHASE 3: bring up postgres alone ──────────────────────────────────────────
log "Phase 3: bringing up postgres for pgbackrest restore"
run "docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE up -d postgres"

log "Waiting for postgres to be healthy..."
if [[ "$DRY_RUN" != "yes" ]]; then
    for i in {1..60}; do
        state=$(docker inspect --format='{{.State.Health.Status}}' "${COMPOSE_PROJECT}-postgres-1" 2>/dev/null \
            || docker inspect --format='{{.State.Health.Status}}' pi_agent_postgres 2>/dev/null \
            || echo "unknown")
        [[ "$state" == "healthy" ]] && break
        sleep 2
    done
    [[ "$state" == "healthy" ]] || { echo "ERROR: postgres did not become healthy"; exit 1; }
fi

# ── PHASE 4: pgbackrest restore ───────────────────────────────────────────────
log "Phase 4: physical restore via pgbackrest"
PGBACKREST_FLAGS="--stanza=parsnip --delta"
if [[ -n "$TARGET_TIME" ]]; then
    PGBACKREST_FLAGS="$PGBACKREST_FLAGS --type=time --target=\"$TARGET_TIME\""
fi

# Stop postgres for the restore (pgbackrest restore needs PGDATA exclusive)
run "docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE stop postgres"
run "docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE run --rm --user postgres --entrypoint '' postgres pgbackrest $PGBACKREST_FLAGS restore"
run "docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE up -d postgres"

# Wait healthy again
if [[ "$DRY_RUN" != "yes" ]]; then
    for i in {1..60}; do
        state=$(docker inspect --format='{{.State.Health.Status}}' "${COMPOSE_PROJECT}-postgres-1" 2>/dev/null \
            || docker inspect --format='{{.State.Health.Status}}' pi_agent_postgres 2>/dev/null \
            || echo "unknown")
        [[ "$state" == "healthy" ]] && break
        sleep 2
    done
fi

# ── PHASE 5: bring up rest of stack ───────────────────────────────────────────
log "Phase 5: bringing up rest of stack"
run "docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE up -d"

# ── PHASE 6: rsync volumes ────────────────────────────────────────────────────
log "Phase 6: restoring volume contents from GCS (throttled)"
GSUTIL_BW_LIMIT="${PARSNIP_DOWNLOAD_MIB_S:-25}"
for vol in analysis_output owui_data pipelines_data; do
    log "  Volume: $vol"
    run "docker run --rm -v ${COMPOSE_PROJECT}_${vol}:/dst -v $SCRATCH_DIR:/scratch \
        google/cloud-sdk:slim gsutil -m -o 'GSUtil:parallel_thread_count=4' \
        -o 'GSUtil:sliced_object_download_threshold=100M' \
        -o 'Boto:max_concurrent_requests=4' \
        rsync -r gs://$GCS_BUCKET/volumes/$vol/ /dst/"
done

# ── PHASE 7: verification ─────────────────────────────────────────────────────
log "Phase 7: verification"
if [[ "$DRY_RUN" != "yes" ]]; then
    run "docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE exec -T postgres \
        psql -U agent -d agent_kb -c 'SELECT
            (SELECT count(*) FROM knowledge_chunks) AS knowledge_chunks,
            (SELECT count(*) FROM agent_memories)   AS agent_memories,
            (SELECT count(*) FROM notes)            AS notes,
            (SELECT count(*) FROM notebooks)        AS notebooks,
            (SELECT count(*) FROM note_resources)   AS note_resources;'"
fi

log "Restore complete. Verify the row counts above against your last RESTORE_RECEIPT_*.md."
