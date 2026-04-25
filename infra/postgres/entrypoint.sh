#!/bin/bash
# Wrapper around the upstream timescale/timescaledb-ha entrypoint.
# Responsibilities:
#   1. Substitute env vars into pgbackrest.conf (only on first start).
#   2. Start cron in the background as root, dropping privs in cron entries.
#   3. Exec the original entrypoint (which becomes PID 1's child via tini).
set -euo pipefail

CONFIG=/etc/pgbackrest/pgbackrest.conf

# pgbackrest.conf has ${GCS_BUCKET} placeholder — substitute on every boot
# (idempotent if the value is unchanged)
if [[ -n "${GCS_BUCKET:-}" ]]; then
    # Use sudo because the file is owned by postgres but env-vars may have changed
    # since image build. We're already running as postgres (USER postgres in Dockerfile).
    if [[ -w "$CONFIG" ]]; then
        sed -i "s|\${GCS_BUCKET}|${GCS_BUCKET}|g" "$CONFIG"
    fi
fi

# Start cron as root in background — needs sudo because we run as postgres now.
# The /etc/cron.d/pgbackrest file declares 'postgres' user per entry.
sudo -n /usr/sbin/cron 2>/dev/null || \
    echo "[parsnip-entrypoint] cron not started (sudo unavailable). Backups will rely on external scheduler." >&2

# Locate the upstream entrypoint and exec it. timescale/timescaledb-ha uses
# /docker-entrypoint.sh from the official postgres base.
if [[ -x /docker-entrypoint.sh ]]; then
    exec /docker-entrypoint.sh "$@"
fi

# Fallback: direct postgres exec if no upstream entrypoint found.
echo "[parsnip-entrypoint] WARNING: /docker-entrypoint.sh not found, exec'ing directly." >&2
exec "$@"
