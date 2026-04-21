#!/bin/sh
# Joplin Server admin account fixer — ensures admin email/password matches .env
# Run this after postgres is healthy but before other services depend on Joplin
#
# NOTE: Joplin Server creates the admin account on first startup only, using
# JOPLIN_SERVER_ADMIN_EMAIL / JOPLIN_SERVER_ADMIN_PASSWORD. If you recreate
# the postgres database, Joplin may create the user with defaults or cached
# values. This script updates the existing admin user to match .env.

set -e

DB_HOST="${POSTGRES_HOST:-pi_agent_postgres}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DATABASE:-joplin}"
DB_USER="${POSTGRES_USER:-agent}"
DB_PASS="${POSTGRES_PASSWORD}"
ADMIN_EMAIL="${JOPLIN_ADMIN_EMAIL:-admin@pi-agent.local}"
ADMIN_PASS="${JOPLIN_ADMIN_PASSWORD}"

if [ -z "$DB_PASS" ]; then
    echo "ERROR: POSTGRES_PASSWORD not set"
    exit 1
fi
if [ -z "$ADMIN_PASS" ]; then
    echo "ERROR: JOPLIN_ADMIN_PASSWORD not set"
    exit 1
fi

echo "Waiting for Joplin schema to be ready..."
for i in $(seq 1 30); do
    if PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1 FROM users LIMIT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# Check if admin user exists
ADMIN_COUNT=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM users WHERE is_admin = 1;" | xargs)

if [ "$ADMIN_COUNT" = "0" ]; then
    echo "WARNING: No admin user found. Joplin Server may not have finished initialisation."
    echo "Restart Joplin Server with an empty joplin database to trigger admin creation."
    exit 1
fi

# Generate bcrypt hash using Node.js bcryptjs (same library Joplin Server uses)
# We run this inside the Joplin container to guarantee library availability
BCRYPT_HASH=$(docker exec pi_agent_joplin node -e "const bcrypt = require('bcryptjs'); console.log(bcrypt.hashSync('$ADMIN_PASS', 10));" 2>/dev/null)

if [ -z "$BCRYPT_HASH" ]; then
    echo "ERROR: Failed to generate bcrypt hash. Is the Joplin container running?"
    exit 1
fi

# Update admin email and password
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" <<EOF
UPDATE users
SET email = '$ADMIN_EMAIL',
    password = '$BCRYPT_HASH'
WHERE is_admin = 1;
EOF

echo "Joplin admin updated:"
echo "  Email: $ADMIN_EMAIL"
echo "  Password: [updated from JOPLIN_ADMIN_PASSWORD]"
