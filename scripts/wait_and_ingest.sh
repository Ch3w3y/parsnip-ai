#!/usr/bin/env bash
# wait_and_ingest.sh
# Monitors the Wikipedia source_id migration, starts ingest, and monitors for 30 mins

LOG_FILE="${LOG_FILE:-/tmp/post_migration_monitor.log}"
MIGRATION_PATTERN="${MIGRATION_PATTERN:-scripts/migrate_wiki_source_ids.py}"
POLL_SECONDS="${POLL_SECONDS:-30}"
STALL_LIMIT="${STALL_LIMIT:-10}"
exec > >(tee -a "$LOG_FILE") 2>&1

query_legacy_count() {
    sg docker -c "docker exec pi_agent_postgres psql -U agent -d agent_kb -t -c \"SELECT COUNT(*) FROM knowledge_chunks WHERE source = 'wikipedia' AND source_id ~ '::[0-9]+$';\"" | tr -d '[:space:]'
}

migration_pids() {
    pgrep -f "$MIGRATION_PATTERN" || true
}

echo "[$(date)] Waiting for Wikipedia source_id migration to finish..."
echo "[$(date)] Watching migration process pattern: $MIGRATION_PATTERN"

last_remaining=""
unchanged_samples=0
while true; do
    # Count rows left to migrate
    remaining=$(query_legacy_count)

    if ! [[ "$remaining" =~ ^[0-9]+$ ]]; then
        echo "[$(date)] ERROR: Could not read migration progress. Got: '$remaining'"
        exit 1
    fi
    
    if [ "$remaining" -eq "0" ]; then
        echo "[$(date)] Migration complete! 0 rows remaining."
        break
    fi

    pids=$(migration_pids)
    if [ -z "$pids" ]; then
        echo "[$(date)] ERROR: $remaining rows remain, but migration process is not running."
        echo "[$(date)] Start it with: nohup python scripts/migrate_wiki_source_ids.py >> /tmp/migrate_wiki.log 2>&1 &"
        exit 1
    fi

    if [ "$remaining" = "$last_remaining" ]; then
        unchanged_samples=$((unchanged_samples + 1))
    else
        unchanged_samples=0
        last_remaining="$remaining"
    fi

    if [ "$unchanged_samples" -ge "$STALL_LIMIT" ]; then
        echo "[$(date)] ERROR: Migration process is running ($pids), but remaining rows stayed at $remaining for $STALL_LIMIT samples."
        exit 1
    fi

    echo "[$(date)] Migration ongoing... $remaining rows remaining. Migration PID(s): ${pids//$'\n'/,}. Waiting ${POLL_SECONDS}s."
    sleep "$POLL_SECONDS"
done

echo "[$(date)] Resuming Wikipedia ingestion..."
./pi-ctl.sh wiki start

echo "[$(date)] Ingestion started. Will monitor for 30 minutes..."

# Monitor every 5 minutes for 30 minutes
for i in {1..6}; do
    sleep 300
    
    # Check ingestion progress
    stats=$(curl -s http://localhost:8000/stats | jq -c '.knowledge_base[] | select(.source == "wikipedia")')
    
    # Check for anomalous 1:1 chunk/article ratio or old schema
    anomalous_rows=$(query_legacy_count)
    
    echo "[$(date)] Update $i/6 (+$((i * 5)) mins) | Stats: $stats | Anomalous source_ids: $anomalous_rows"
    
    if [ "$anomalous_rows" -gt "0" ]; then
        echo "[$(date)] WARNING: Newly ingested data has anomalous source_ids! Stopping ingestion."
        ./pi-ctl.sh wiki stop
        exit 1
    fi
done

echo "[$(date)] 30 minutes elapsed. Ingestion appears healthy and data schema is correct."
