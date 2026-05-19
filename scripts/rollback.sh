#!/usr/bin/env bash
# NEWTOWT mynewtowt — rollback script.
#
# Usage:
#   scripts/rollback.sh                          # rollback to the latest snapshot
#   scripts/rollback.sh <snapshot.dump>          # rollback to a specific snapshot
#   scripts/rollback.sh --list                   # list available snapshots
#
# What it does:
#   1. Verify the target snapshot exists.
#   2. Enable maintenance mode.
#   3. Restore the snapshot via pg_restore --clean --if-exists.
#   4. Restart the app container.
#   5. Wait for /health and report.
#
# Objective: MTTR < 15 minutes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
DB_CONTAINER="${DB_CONTAINER:-mynewtowt-db}"
APP_CONTAINER="${APP_CONTAINER:-mynewtowt-app}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.yml}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-90}"

if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

log()     { printf "%s[%s]%s %s\n"   "${BLUE}"   "$(date -u +%FT%TZ)" "${RESET}" "$*"; }
success() { printf "%s[OK]%s %s\n"   "${GREEN}"  "${RESET}" "$*"; }
warn()    { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
err()     { printf "%s[ERR]%s %s\n"  "${RED}"    "${RESET}" "$*" >&2; }
fatal()   { err "$*"; exit 1; }

usage() {
  cat <<EOF
NEWTOWT mynewtowt rollback

Usage: $(basename "$0") [snapshot.dump | --list | --help]

Without arguments: restores the snapshot referenced by ${BACKUP_DIR}/.last-snapshot
EOF
}

case "${1:-}" in
  -h|--help)   usage; exit 0 ;;
  --list)
    log "Available snapshots in ${BACKUP_DIR}"
    ls -lh "${BACKUP_DIR}"/pre-*.dump 2>/dev/null || echo "(none)"
    exit 0
    ;;
esac

if [[ $# -ge 1 ]]; then
  SNAPSHOT="$1"
elif [[ -f "${BACKUP_DIR}/.last-snapshot" ]]; then
  SNAPSHOT="$(cat "${BACKUP_DIR}/.last-snapshot")"
else
  fatal "No snapshot specified and ${BACKUP_DIR}/.last-snapshot is missing."
fi

[[ -f "${SNAPSHOT}" ]] || fatal "Snapshot file not found: ${SNAPSHOT}"
log "Rolling back using snapshot: ${SNAPSHOT}"

# Verify .env present (for DB credentials)
[[ -f "${PROJECT_ROOT}/.env" ]] || fatal ".env not found at ${PROJECT_ROOT}/.env"

# Maintenance mode
if docker ps --format '{{.Names}}' | grep -qx "${APP_CONTAINER}"; then
  docker exec "${APP_CONTAINER}" sh -c 'touch /tmp/.maintenance' 2>/dev/null \
    || warn "Could not set maintenance flag (will continue)"
fi

# DB restore
log "Restoring database (this may take a few minutes)"
# shellcheck disable=SC1091
source "${PROJECT_ROOT}/.env"
docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" \
  pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists \
  < "${SNAPSHOT}" || warn "pg_restore reported errors (some may be benign)"
success "DB restore completed"

# Restart app
log "Restarting app"
cd "${PROJECT_ROOT}"
docker compose -f "${COMPOSE_FILE}" up -d --no-deps --force-recreate app

# Maintenance off
docker exec "${APP_CONTAINER}" sh -c 'rm -f /tmp/.maintenance' 2>/dev/null || true

# Health
log "Waiting for /health (timeout ${HEALTH_TIMEOUT_SECONDS}s)"
deadline=$(( SECONDS + HEALTH_TIMEOUT_SECONDS ))
while (( SECONDS < deadline )); do
  if curl -fsS -m 5 "${HEALTH_URL}" | grep -q '"status":"ok"'; then
    success "Rollback complete — application is healthy"
    exit 0
  fi
  sleep 2
done

err "Health check failed after rollback. Application may need manual intervention."
exit 1
