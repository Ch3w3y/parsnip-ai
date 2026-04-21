#!/bin/sh
# Joplin Server admin account fixer — ensures admin email matches env vars
# Run this after postgres is healthy but before other services depend on Joplin

set -e

DB_HOST="${POSTGRES_HOST:-pi_agent_postgres}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DATABASE:-joplin}"
DB_USER="${POSTGRES_USER:-agent}"
DB_PASS="${POSTGRES_PASSWORD}"
ADMIN_EMAIL="${JOPLIN_ADMIN_EMAIL:-admin@localhost}"
ADMIN_PASS="${JOPLIN_ADMIN_PASSWORD}"

if [ -z "$DB_PASS" ]; then
    echo "ERROR: POSTGRES_PASSWORD not set"
    exit 1
fi

echo "Waiting for Joplin schema to be ready..."
for i in $(seq 1 30); do
    if PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1 FROM users LIMIT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# Update admin email if it doesn't match env var
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" <<EOF
UPDATE users
SET email = '$ADMIN_EMAIL'
WHERE is_admin = 1
  AND email != '$ADMIN_EMAIL';
EOF

echo "Joplin admin email synced to: $ADMIN_EMAIL"
