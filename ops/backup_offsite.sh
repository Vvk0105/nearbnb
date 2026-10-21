#!/usr/bin/env bash
# Nightly backup straight to cloud storage. Nothing is written to this server's disk (it is small).
# Needs rclone with a remote set up (see docs/05_OPERATIONS.md).
# Usage: ops/backup_offsite.sh <rclone-remote-and-bucket> [compose-file]
#   e.g. ops/backup_offsite.sh r2:nearbnb-backups docker-compose.prod.yml
# Keeps 30 days of database dumps (KEEP_DAYS to change). Photos and documents are copied and never deleted remotely.
set -euo pipefail
REMOTE="${1:?usage: ops/backup_offsite.sh <remote:bucket> [compose-file]}"
COMPOSE_FILE="${2:-docker-compose.prod.yml}"
KEEP_DAYS="${KEEP_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OBJECT="$REMOTE/db/nearbnb-$STAMP.dump"

# 1. Database: pg_dump is piped into the upload, so there is no local file. pipefail stops us if either side fails.
docker compose -f "$COMPOSE_FILE" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' | rclone rcat "$OBJECT"

# 2. Prove it is real: it must have some size and pg_restore must be able to read it back.
BYTES="$(rclone size --json "$OBJECT" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')"
if [ "${BYTES:-0}" -lt 1000 ]; then echo "backup too small ($BYTES bytes), something is wrong" >&2; exit 1; fi
rclone cat "$OBJECT" | docker compose -f "$COMPOSE_FILE" exec -T db pg_restore --list > /dev/null

# 3. Photos and ID documents (a docker volume). copy only adds and updates; it never deletes anything in the cloud.
STORAGE_VOLUME="${STORAGE_VOLUME:-nearbnb_storage}"   # check with: docker volume ls
VOLUME_PATH="$(docker volume inspect -f '{{.Mountpoint}}' "$STORAGE_VOLUME")"
rclone copy "$VOLUME_PATH" "$REMOTE/storage" --quiet

# 4. Remove old database dumps from the cloud.
rclone delete "$REMOTE/db" --min-age "${KEEP_DAYS}d" --quiet

echo "offsite backup ok: $OBJECT ($BYTES bytes)"
