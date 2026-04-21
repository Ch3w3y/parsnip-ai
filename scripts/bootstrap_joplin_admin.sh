#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
    echo ".env not found" >&2
    exit 1
fi

# shellcheck disable=SC1091
set -a && source .env && set +a

: "${JOPLIN_ADMIN_EMAIL:?JOPLIN_ADMIN_EMAIL must be set in .env}"
: "${JOPLIN_ADMIN_PASSWORD:?JOPLIN_ADMIN_PASSWORD must be set in .env}"

HASH=$(
    sg docker -c "docker exec pi_agent_joplin node -e 'const b=require(\"bcryptjs\"); console.log(b.hashSync(process.argv[1], 10));' \"$JOPLIN_ADMIN_PASSWORD\""
)
HASH_ESCAPED=${HASH//$/\\$}

NOW_MS=$(python - <<'PY'
import time
print(int(time.time() * 1000))
PY
)

sg docker -c "docker exec pi_agent_postgres psql -U agent -d joplin -c \"UPDATE users SET email='${JOPLIN_ADMIN_EMAIL}', password='${HASH_ESCAPED}', updated_time=${NOW_MS}, must_set_password=0, enabled=1 WHERE is_admin=1;\""

echo "Joplin admin credentials updated to ${JOPLIN_ADMIN_EMAIL}"
