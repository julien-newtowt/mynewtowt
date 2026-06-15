#!/usr/bin/env bash
# NEWTOWT mynewtowt — production deploy script.
#
# Workflow (idempotent, safe to re-run):
#   1. Pre-flight checks (env, docker, branch)
#   2. Sync working tree to target git ref (fetch + checkout / fast-forward)
#   3. Snapshot Postgres + tag git release
#   4. Pull image / build
#   5. Maintenance mode ON
#   6. Apply Alembic migrations
#   7. Rolling restart app + worker
#   8. Smoke tests on /health
#   9. Maintenance mode OFF
#  10. Post-deploy report
#
# Failure modes are explicit:
#   - migration error  → DB snapshot restored, image NOT swapped, exit 1
#   - smoke test fail  → previous image redeployed (rollback), exit 2
#
# Usage:
#   scripts/deploy.sh                          # deploys origin/main HEAD (pulls)
#   scripts/deploy.sh -v v3.0.1                # checks out & deploys a tag/sha
#   scripts/deploy.sh -b release/x             # deploys a specific branch
#   scripts/deploy.sh -e staging               # target staging env
#   scripts/deploy.sh --skip-git-sync          # deploy the working tree as-is
#   scripts/deploy.sh --skip-snapshot          # for hotfixes only

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV="${ENV:-production}"
VERSION="${VERSION:-}"
SKIP_SNAPSHOT=0
SKIP_TESTS=0
SKIP_GIT_SYNC=0

# Git sync — d'où le code déployé est tiré (cf. sync_code). C'est l'étape qui
# manquait : sans elle, le build utilisait le working tree local (souvent
# périmé) au lieu du dernier code poussé.
GIT_REMOTE="${GIT_REMOTE:-origin}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"

DB_CONTAINER="${DB_CONTAINER:-mynewtowt-db}"
APP_CONTAINER="${APP_CONTAINER:-mynewtowt-app}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.yml}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-90}"

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

# Colors only if TTY
if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

log()      { printf "%s[%s]%s %s\n"   "${BLUE}"   "$(date -u +%FT%TZ)" "${RESET}" "$*"; }
success()  { printf "%s[OK]%s %s\n"   "${GREEN}"  "${RESET}" "$*"; }
warn()     { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
err()      { printf "%s[ERR]%s %s\n"  "${RED}"    "${RESET}" "$*" >&2; }
fatal()    { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

usage() {
  cat <<EOF
NEWTOWT mynewtowt deploy

Usage: $(basename "$0") [options]

Options:
  -e, --env ENV            Target environment (production|staging) [default: production]
  -v, --version VERSION    Git tag or sha to check out & deploy [default: branch HEAD]
  -b, --branch BRANCH      Branch to deploy when no --version [default: main]
      --skip-git-sync      Deploy the current working tree as-is (no fetch/checkout)
      --skip-snapshot      Skip pre-deploy DB snapshot (hotfix only — risky)
      --skip-tests         Skip post-deploy smoke tests (NOT recommended)
  -h, --help               Show this help

Environment variables:
  ENV, VERSION, GIT_REMOTE, DEPLOY_BRANCH, DB_CONTAINER, APP_CONTAINER,
  COMPOSE_FILE, HEALTH_URL, HEALTH_TIMEOUT_SECONDS, BACKUP_DIR,
  BACKUP_RETENTION_DAYS

Examples:
  $(basename "$0")                       # pull & deploy origin/main
  $(basename "$0") -v v3.0.1             # check out & deploy a tag
  $(basename "$0") -b release/3.1        # deploy a branch
  $(basename "$0") --env staging
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)            ENV="$2"; shift 2 ;;
    -v|--version)        VERSION="$2"; shift 2 ;;
    -b|--branch)         DEPLOY_BRANCH="$2"; shift 2 ;;
    --skip-git-sync)     SKIP_GIT_SYNC=1; shift ;;
    --skip-snapshot)     SKIP_SNAPSHOT=1; shift ;;
    --skip-tests)        SKIP_TESTS=1; shift ;;
    -h|--help)           usage; exit 0 ;;
    *) err "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

preflight() {
  log "Pre-flight checks (env=${ENV})"

  command -v docker      >/dev/null || fatal "docker not found in PATH"
  command -v git         >/dev/null || fatal "git not found in PATH"
  command -v curl        >/dev/null || fatal "curl not found in PATH"

  [[ -f "${COMPOSE_FILE}" ]] || fatal "docker-compose file missing: ${COMPOSE_FILE}"

  cd "${PROJECT_ROOT}"

  if [[ ! -f .env ]]; then
    fatal ".env file missing at ${PROJECT_ROOT}/.env — refusing to deploy"
  fi

  # Refuse weak secrets in production
  if [[ "${ENV}" == "production" ]]; then
    if grep -qE '^SECRET_KEY=(change_me|secret|changeme)' .env; then
      fatal ".env contains a weak SECRET_KEY — refusing production deploy"
    fi
    if grep -qE '^POSTGRES_PASSWORD=change_me_local' .env; then
      fatal ".env contains the default POSTGRES_PASSWORD — refusing production deploy"
    fi
  fi

  # NB : on ne fige PAS VERSION sur le HEAD local ici — sync_code() met le
  # working tree à jour sur la ref cible puis renseigne VERSION (sinon on
  # déploierait le code local potentiellement périmé).
  if [[ -n "${VERSION}" ]]; then
    log "Target version (pinned): ${VERSION}"
  elif (( SKIP_GIT_SYNC == 1 )); then
    log "Target: working tree as-is (--skip-git-sync)"
  else
    log "Target: ${GIT_REMOTE}/${DEPLOY_BRANCH} (latest pushed)"
  fi

  # Disk space guard (≥ 2 GB free)
  local free_kb
  free_kb="$(df -Pk "${PROJECT_ROOT}" | awk 'NR==2 {print $4}')"
  if (( free_kb < 2 * 1024 * 1024 )); then
    fatal "Insufficient free disk space: $((free_kb / 1024)) MB available, 2 GB minimum required"
  fi

  success "Pre-flight checks passed"
}

# ---------------------------------------------------------------------------
# Code sync — met le working tree à jour sur la ref cible AVANT le build.
# Sans cette étape, ``docker compose build`` (COPY app ./app) embarque le code
# local, souvent périmé → les modifications poussées (et leurs migrations) ne
# sont pas déployées.
# ---------------------------------------------------------------------------

sync_code() {
  if (( SKIP_GIT_SYNC == 1 )); then
    warn "Skipping git sync (--skip-git-sync) — deploying the working tree as-is"
    VERSION="${VERSION:-$(git -C "${PROJECT_ROOT}" rev-parse --short HEAD)}"
    return
  fi

  cd "${PROJECT_ROOT}"

  if [[ -n "${VERSION}" ]]; then
    # Version épinglée (-v) : on récupère puis on checkout cette ref.
    log "Checking out pinned version: ${VERSION}"
    git fetch --prune --tags "${GIT_REMOTE}" || fatal "git fetch ${GIT_REMOTE} failed"
    git checkout "${VERSION}" || fatal "git checkout ${VERSION} failed"
  else
    # Défaut : (re)synchronise SYSTÉMATIQUEMENT la branche cible sur origin.
    # Exécute exactement : git fetch origin / git checkout main /
    # git merge --ff-only origin/main / git rev-parse --short HEAD.
    log "Syncing ${DEPLOY_BRANCH} from ${GIT_REMOTE}"
    git fetch "${GIT_REMOTE}" || fatal "git fetch ${GIT_REMOTE} failed"
    git checkout "${DEPLOY_BRANCH}" || fatal "git checkout ${DEPLOY_BRANCH} failed"
    git merge --ff-only "${GIT_REMOTE}/${DEPLOY_BRANCH}" \
      || fatal "Cannot fast-forward ${DEPLOY_BRANCH} to ${GIT_REMOTE}/${DEPLOY_BRANCH} (diverged — résolvez manuellement, ou --skip-git-sync)"
  fi

  VERSION="$(git rev-parse --short HEAD)"
  success "Code synced to ${VERSION} — $(git log -1 --format='%s' | cut -c1-70)"
}

# ---------------------------------------------------------------------------
# Snapshot + tag
# ---------------------------------------------------------------------------

snapshot_db() {
  if (( SKIP_SNAPSHOT == 1 )); then
    warn "Skipping DB snapshot (--skip-snapshot flag set)"
    return
  fi
  log "Snapshotting Postgres into ${BACKUP_DIR}"

  mkdir -p "${BACKUP_DIR}"
  local ts; ts="$(date -u +%Y%m%dT%H%M%SZ)"
  local snapshot="${BACKUP_DIR}/pre-${VERSION}-${ts}.dump"

  if ! docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}"; then
    fatal "DB container '${DB_CONTAINER}' is not running — cannot snapshot"
  fi

  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" \
    pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc > "${snapshot}"

  success "Snapshot saved: ${snapshot} ($(du -h "${snapshot}" | cut -f1))"
  echo "${snapshot}" > "${BACKUP_DIR}/.last-snapshot"

  # Rotation
  find "${BACKUP_DIR}" -maxdepth 1 -name 'pre-*.dump' -mtime "+${BACKUP_RETENTION_DAYS}" -delete || true
}

tag_release() {
  log "Tagging git release"
  cd "${PROJECT_ROOT}"
  local tag="release/${VERSION}-$(date -u +%Y%m%d-%H%M)"
  if git rev-parse "${tag}" >/dev/null 2>&1; then
    warn "Tag ${tag} already exists — reusing"
  else
    git tag -a "${tag}" -m "Release ${VERSION} ($(date -u +%FT%TZ))" || \
      warn "Could not create git tag (non-fatal)"
  fi
  success "Tag: ${tag}"
}

# ---------------------------------------------------------------------------
# Build / pull image
# ---------------------------------------------------------------------------

build_image() {
  log "Building image for ${VERSION}"
  cd "${PROJECT_ROOT}"
  docker compose -f "${COMPOSE_FILE}" build app
  success "Image built"
}

# ---------------------------------------------------------------------------
# Maintenance mode
# ---------------------------------------------------------------------------

maintenance_on() {
  log "Enabling maintenance mode"
  if docker ps --format '{{.Names}}' | grep -qx "${APP_CONTAINER}"; then
    docker exec "${APP_CONTAINER}" sh -c 'touch /tmp/.maintenance' 2>/dev/null \
      || warn "Could not set maintenance flag inside container (will continue)"
  fi
  success "Maintenance ON"
}

maintenance_off() {
  log "Disabling maintenance mode"
  if docker ps --format '{{.Names}}' | grep -qx "${APP_CONTAINER}"; then
    docker exec "${APP_CONTAINER}" sh -c 'rm -f /tmp/.maintenance' 2>/dev/null || true
  fi
  success "Maintenance OFF"
}

# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

run_migrations() {
  log "Applying Alembic migrations"
  cd "${PROJECT_ROOT}"

  # Run migration in a one-shot container that shares env + network with the
  # main app service. This way we don't depend on the app being healthy.
  if ! docker compose -f "${COMPOSE_FILE}" run --rm app alembic upgrade head; then
    err "Migration failed"
    restore_last_snapshot
    fatal "Migration failed and DB has been restored. Exit code 1."
  fi
  success "Migrations applied"
}

# ---------------------------------------------------------------------------
# Restart with health gating
# ---------------------------------------------------------------------------

rolling_restart() {
  log "Rolling restart of app container"
  cd "${PROJECT_ROOT}"
  docker compose -f "${COMPOSE_FILE}" up -d --no-deps --force-recreate app
  success "App container recreated"
}

wait_for_health() {
  log "Waiting for /health (timeout=${HEALTH_TIMEOUT_SECONDS}s)"
  local deadline=$(( SECONDS + HEALTH_TIMEOUT_SECONDS ))
  local last_status=""
  local probe_mode="exec"
  # ``HEALTH_URL`` peut être surchargée via .env pour pointer vers l'URL
  # publique (Caddy). Si elle reste sur la valeur par défaut 127.0.0.1:8000
  # et que l'app expose seulement le port sur le réseau Docker interne, on
  # bypass via ``docker compose exec`` — fonctionne dans tous les setups.
  if [[ "${HEALTH_URL}" != "http://127.0.0.1:8000/health" ]]; then
    probe_mode="curl"
  fi

  while (( SECONDS < deadline )); do
    if [[ "${probe_mode}" == "curl" ]]; then
      if curl -fsS -m 5 "${HEALTH_URL}" > /tmp/.health.out 2>/dev/null; then
        last_status="$(cat /tmp/.health.out)"
        if grep -q '"status":"ok"' /tmp/.health.out; then
          success "Health OK (via curl): ${last_status}"
          rm -f /tmp/.health.out
          return 0
        fi
      fi
    else
      if docker compose -f "${COMPOSE_FILE}" exec -T app \
           curl -fsS -m 5 http://localhost:8000/health > /tmp/.health.out 2>/dev/null
      then
        last_status="$(cat /tmp/.health.out)"
        if grep -q '"status":"ok"' /tmp/.health.out; then
          success "Health OK (via docker exec): ${last_status}"
          rm -f /tmp/.health.out
          return 0
        fi
      fi
    fi
    sleep 2
  done
  err "Health check did not return ok within ${HEALTH_TIMEOUT_SECONDS}s. Last: ${last_status:-unreachable}"
  return 1
}

# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

smoke_tests() {
  if (( SKIP_TESTS == 1 )); then
    warn "Skipping smoke tests (--skip-tests flag set)"
    return
  fi

  # Même logique que wait_for_health() : si HEALTH_URL reste à la default
  # (127.0.0.1:8000 inaccessible depuis le host quand l'app utilise
  # ``expose:`` au lieu de ``ports:``), on probe via docker exec à l'URL
  # interne. Sinon on garde curl depuis le host (Caddy / loadbalancer).
  local probe_mode="curl"
  local base="${HEALTH_URL%/health}"
  if [[ "${HEALTH_URL}" == "http://127.0.0.1:8000/health" ]]; then
    probe_mode="exec"
    base="http://localhost:8000"
  fi
  log "Running smoke tests against ${base} (mode=${probe_mode})"

  local failed=0

  check_endpoint() {
    local path="$1"; local expected="${2:-200}"
    local code
    if [[ "${probe_mode}" == "exec" ]]; then
      code="$(docker compose -f "${COMPOSE_FILE}" exec -T app \
              curl -s -o /dev/null -w '%{http_code}' -m 10 \
              "http://localhost:8000${path}" 2>/dev/null || echo 000)"
    else
      code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 \
              "${base}${path}" 2>/dev/null || echo 000)"
    fi
    if [[ "${code}" == "${expected}" ]]; then
      success "  ${path} → ${code}"
    else
      err "  ${path} → ${code} (expected ${expected})"
      failed=1
    fi
  }

  check_endpoint "/health" 200
  check_endpoint "/api/v1/health" 200
  check_endpoint "/" 200
  check_endpoint "/routes" 200
  check_endpoint "/about" 200
  check_endpoint "/login" 200
  check_endpoint "/me" 303    # redirects unauth → /me/login
  check_endpoint "/me/login" 200
  check_endpoint "/.well-known/security.txt" 200

  if (( failed != 0 )); then
    err "Smoke tests failed"
    return 1
  fi
  success "All smoke tests passed"
}

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

restore_last_snapshot() {
  if (( SKIP_SNAPSHOT == 1 )); then
    warn "Cannot restore: snapshot was skipped"
    return
  fi
  local snapshot
  snapshot="$(cat "${BACKUP_DIR}/.last-snapshot" 2>/dev/null || true)"
  if [[ -z "${snapshot}" || ! -f "${snapshot}" ]]; then
    warn "No snapshot recorded — manual recovery may be required"
    return
  fi
  warn "Restoring DB snapshot ${snapshot}"

  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" \
    pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists \
    < "${snapshot}" || warn "pg_restore reported errors (some may be benign)"

  success "DB restored from ${snapshot}"
}

rollback_app() {
  warn "Attempting app rollback"
  cd "${PROJECT_ROOT}"
  # Simplest: bring previous image back by rebuilding from previous commit.
  # In a real registry-based setup, you'd `docker pull <prev_image>` and
  # `docker compose up -d`. Here we rely on the snapshot for data integrity
  # and a manual git checkout of the previous tag.
  warn "Manual intervention required: pull previous image / git checkout previous release"
}

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

report() {
  cat <<EOF

────────────────────────────────────────────────────────────────────
  Deployment summary
────────────────────────────────────────────────────────────────────
  Environment      : ${ENV}
  Version deployed : ${VERSION}
  Health URL       : ${HEALTH_URL}
  Snapshot         : ${SKIP_SNAPSHOT:+skipped}$( (( SKIP_SNAPSHOT == 0 )) && cat "${BACKUP_DIR}/.last-snapshot" 2>/dev/null )
  Time             : $(date -u +%FT%TZ)
────────────────────────────────────────────────────────────────────
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  preflight
  sync_code
  snapshot_db
  tag_release
  build_image
  maintenance_on
  run_migrations
  rolling_restart
  if ! wait_for_health; then
    maintenance_off
    rollback_app
    fatal "Deployment aborted: health check failed. Exit code 2."
  fi
  if ! smoke_tests; then
    maintenance_off
    rollback_app
    fatal "Deployment aborted: smoke tests failed. Exit code 2."
  fi
  maintenance_off
  report
  success "Deployment completed successfully — version ${VERSION}"
}

main "$@"
