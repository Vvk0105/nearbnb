#!/usr/bin/env bash
# Daily database backup. Run from the project folder on the server (cron example in docs/05_OPERATIONS.md).
# Usage: ops/backup.sh [compose-file] [backup-dir]
# Keeps 14 daily dumps. Copy the folder off the server too (see the runbook): a backup on the same disk is not a backup.
set -euo pipefail
COMPOSE_FILE="${1:-docker-compose.prod.yml}"
DIR="${2:-./backups}"
KEEP="${KEEP_DAYS:-14}"
mkdir -p "$DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DIR/nearbnb-$STAMP.dump"
docker compose -f "$COMPOSE_FILE" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' > "$OUT.partial"
# A truncated dump is worse than none: make sure it can be listed before keeping it.
docker compose -f "$COMPOSE_FILE" exec -T db pg_restore --list < "$OUT.partial" > /dev/null
mv "$OUT.partial" "$OUT"
find "$DIR" -name 'nearbnb-*.dump' -mtime +"$KEEP" -delete
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
