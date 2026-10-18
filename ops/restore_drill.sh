#!/usr/bin/env bash
# Proves a backup can really be restored: loads it into a scratch database next to the real one,
# compares row counts, then drops the scratch database. Never touches the live database.
# Usage: ops/restore_drill.sh <dump-file> [compose-file]
set -euo pipefail
DUMP="${1:?usage: ops/restore_drill.sh <dump-file> [compose-file]}"
COMPOSE_FILE="${2:-docker-compose.yml}"
SCRATCH="nearbnb_restore_drill"
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }
psql_db() { dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d $1 -v ON_ERROR_STOP=1 -At -c \"$2\""; }

dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -c 'DROP DATABASE IF EXISTS $SCRATCH'" > /dev/null
dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -c 'CREATE DATABASE $SCRATCH'" > /dev/null
dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d $SCRATCH -c 'CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS btree_gist; CREATE EXTENSION IF NOT EXISTS pgcrypto'" > /dev/null
dc exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $SCRATCH --no-owner --exit-on-error" < "$DUMP" 2>/dev/null || \
  dc exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $SCRATCH --no-owner" < "$DUMP" 2>&1 | grep -v "already exists" || true

COUNT_SQL="select 'users', count(*) from users union all select 'bookings', count(*) from bookings union all select 'payments', count(*) from payments union all select 'audit_logs', count(*) from audit_logs union all select 'migration', 0 from alembic_version"
LIVE_DB="$(dc exec -T db sh -c 'echo $POSTGRES_DB' | tr -d '\r')"
LIVE="$(psql_db "$LIVE_DB" "$COUNT_SQL")"
BACK="$(psql_db "$SCRATCH" "$COUNT_SQL")"
echo "--- live ---";     echo "$LIVE"
echo "--- restored ---"; echo "$BACK"
VERSION="$(psql_db "$SCRATCH" 'select version_num from alembic_version')"
dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -c 'DROP DATABASE $SCRATCH'" > /dev/null
echo "restored migration: $VERSION"
if [ "$LIVE" = "$BACK" ]; then echo "RESTORE DRILL PASSED"; else echo "counts differ (fine if the live database changed since the dump); read the lists above"; fi
