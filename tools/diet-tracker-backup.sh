#!/usr/bin/env bash
set -euo pipefail

PROJECT="diet-tracker"
SERVICE="backend"
BACKUP_DIR="/home/diettracker/backups/diet-tracker"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR"

# Prevent overlapping runs
exec 9>/var/lock/diet-tracker-backup.lock
flock -n 9 || exit 0

CID="$(docker ps -q \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter "label=com.docker.compose.service=${SERVICE}" | head -n1)"

if [[ -z "${CID}" ]]; then
  echo "No running backend container found"
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_IN_CONTAINER="/tmp/diet_tracker_${STAMP}.db"
OUT="${BACKUP_DIR}/diet_tracker_${STAMP}.db"

docker exec \
  -e BACKUP_SRC=/app/data/diet_tracker.db \
  -e BACKUP_DST="${TMP_IN_CONTAINER}" \
  "${CID}" \
  python -c "import os,sqlite3; s=os.environ['BACKUP_SRC']; d=os.environ['BACKUP_DST']; src=sqlite3.connect(s); dst=sqlite3.connect(d); src.backup(dst); dst.close(); src.close()"

docker cp "${CID}:${TMP_IN_CONTAINER}" "${OUT}"
docker exec "${CID}" rm -f "${TMP_IN_CONTAINER}"

gzip -f "${OUT}"
find "${BACKUP_DIR}" -type f -name 'diet_tracker_*.db.gz' -mtime +${RETENTION_DAYS} -delete

echo "Backup complete: ${OUT}.gz"
