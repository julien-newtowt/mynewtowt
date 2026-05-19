#!/usr/bin/env bash
# ═══ my_TOWT — Database Backup Script ═══
# Usage: ./scripts/backup_db.sh
# Runs pg_dump inside the towt-db container and saves to ./backups/
# Retention: removes backups older than BACKUP_RETENTION_DAYS (default 30)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

# Load .env if present
if [ -f "${PROJECT_DIR}/.env" ]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

DB_CONTAINER="${DB_CONTAINER:-towt-db}"
POSTGRES_USER="${POSTGRES_USER:-towt_admin}"
POSTGRES_DB="${POSTGRES_DB:-towt_planning}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/towt_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Sauvegarde de la base ${POSTGRES_DB}..."

docker exec "$DB_CONTAINER" pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --no-owner \
    --no-privileges \
    --format=plain \
    | gzip > "$BACKUP_FILE"

FILESIZE=$(stat -f%z "$BACKUP_FILE" 2>/dev/null || stat -c%s "$BACKUP_FILE" 2>/dev/null || echo "?")
echo "[$(date)] Sauvegarde terminee: ${BACKUP_FILE} (${FILESIZE} octets)"

# Purge des anciennes sauvegardes
DELETED=$(find "$BACKUP_DIR" -name "towt_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "[$(date)] ${DELETED} sauvegarde(s) de plus de ${RETENTION_DAYS} jours supprimee(s)"
fi

echo "[$(date)] Sauvegarde terminee avec succes."
