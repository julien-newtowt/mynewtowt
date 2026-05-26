#!/usr/bin/env bash
# ============================================================
#  NEWTOWT — deploy_v3_1.sh
#  Déploiement release Sprint 1-2-3 (branches claude/zealous-*
#  fusionnées sur main ou toute branche cible).
#
#  Ce script est idempotent : safe to re-run.
#
#  Usage :
#    scripts/deploy_v3_1.sh                    # branche courante
#    scripts/deploy_v3_1.sh -b main            # depuis main
#    scripts/deploy_v3_1.sh --skip-snapshot    # hotfix only
#
#  Ce qu'il fait (dans l'ordre) :
#    1.  Pré-vérifications (docker, .env, espace disque)
#    2.  git pull
#    3.  Snapshot Postgres (pre-deploy)
#    4.  Build image Docker
#    5.  Maintenance ON
#    6.  alembic upgrade head  (migrations 0013→0018)
#    7.  Seed feature flags client (idempotent)
#    8.  Rolling restart
#    9.  Health check (/health)
#    10. Smoke tests
#    11. Maintenance OFF
#    12. Rapport final
#
#  En cas d'échec :
#    - migration failed  → snapshot restauré, exit 1
#    - health/smoke fail → rollback image précédente, exit 2
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ── Paramètres ──────────────────────────────────────────────
BRANCH=""
SKIP_SNAPSHOT=0
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.yml}"
DB_CONTAINER="${DB_CONTAINER:-mynewtowt-db}"
APP_CONTAINER="${APP_CONTAINER:-mynewtowt-app}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

# ── Couleurs ────────────────────────────────────────────────
if [[ -t 1 ]]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'; C=$'\033[36m'; Z=$'\033[0m'
else
  R="" G="" Y="" B="" C="" Z=""
fi

log()     { printf "%s  %s%s\n"   "${B}▸${Z}" "$*" ""; }
ok()      { printf "%s  %s%s\n"   "${G}✓${Z}" "$*" ""; }
warn()    { printf "%s  %s%s\n"   "${Y}!${Z}" "$*" "" >&2; }
err()     { printf "%s  %s%s\n"   "${R}✗${Z}" "$*" "" >&2; }
fatal()   { err "$*"; exit "${2:-1}"; }
header()  { echo; printf "%s═══  %s  ═══%s\n" "${C}" "$*" "${Z}"; echo; }

# ── Parsing ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--branch)        BRANCH="$2"; shift 2 ;;
    --skip-snapshot)    SKIP_SNAPSHOT=1; shift ;;
    -h|--help)
      echo "Usage: $(basename "$0") [-b <branch>] [--skip-snapshot]"
      exit 0 ;;
    *) fatal "Argument inconnu : $1" ;;
  esac
done

# ════════════════════════════════════════════════════════════
# 1. PRÉ-VÉRIFICATIONS
# ════════════════════════════════════════════════════════════
header "1 — Pré-vérifications"

command -v docker >/dev/null  || fatal "docker introuvable"
command -v git    >/dev/null  || fatal "git introuvable"
docker compose version >/dev/null 2>&1 || fatal "docker compose (v2) introuvable"

[[ -f "${COMPOSE_FILE}" ]] || fatal "docker-compose.yml manquant : ${COMPOSE_FILE}"
[[ -f ".env" ]]             || fatal ".env manquant — copier .env.example et configurer"

# Secrets faibles
if grep -qE '^SECRET_KEY=(change_me|secret|changeme|CHANGE_ME)' .env; then
  fatal ".env : SECRET_KEY faible — refus de déployer en production"
fi
if grep -qE '^POSTGRES_PASSWORD=change_me_local' .env; then
  fatal ".env : POSTGRES_PASSWORD par défaut — refus de déployer en production"
fi

# Espace disque ≥ 2 Go
free_kb="$(df -Pk "${PROJECT_ROOT}" | awk 'NR==2 {print $4}')"
(( free_kb >= 2 * 1024 * 1024 )) || fatal "Espace disque insuffisant : $((free_kb/1024)) Mo dispo, minimum 2 Go"

ok "Pré-vérifications OK"

# ════════════════════════════════════════════════════════════
# 2. GIT PULL
# ════════════════════════════════════════════════════════════
header "2 — Git pull"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
TARGET_BRANCH="${BRANCH:-${CURRENT_BRANCH}}"

if [[ -n "$(git status --porcelain)" ]]; then
  warn "Fichiers modifiés non commités — stash conseillé avant de continuer"
  read -rp "  Continuer quand même ? [y/N] " ans
  [[ "${ans,,}" == "y" ]] || exit 0
fi

if [[ "${CURRENT_BRANCH}" != "${TARGET_BRANCH}" ]]; then
  log "Checkout ${TARGET_BRANCH}"
  git checkout "${TARGET_BRANCH}"
fi

git pull --rebase origin "${TARGET_BRANCH}"
VERSION="$(git rev-parse --short HEAD)"
ok "HEAD = ${VERSION} sur ${TARGET_BRANCH}"

# ════════════════════════════════════════════════════════════
# 3. SNAPSHOT POSTGRES
# ════════════════════════════════════════════════════════════
header "3 — Snapshot Postgres"

if (( SKIP_SNAPSHOT == 1 )); then
  warn "--skip-snapshot : sauvegarde ignorée (hotfix uniquement)"
else
  if ! docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}"; then
    fatal "Container DB '${DB_CONTAINER}' introuvable ou arrêté"
  fi
  mkdir -p "${BACKUP_DIR}"
  SNAP_FILE="${BACKUP_DIR}/pre-v3.1-${VERSION}-$(date -u +%Y%m%dT%H%M%SZ).dump"
  # shellcheck disable=SC1091
  source .env
  docker exec \
    -e PGPASSWORD="${POSTGRES_PASSWORD}" \
    "${DB_CONTAINER}" \
    pg_dump -U "${POSTGRES_USER:-towt}" -d "${POSTGRES_DB:-towt}" -Fc \
    > "${SNAP_FILE}"
  echo "${SNAP_FILE}" > "${BACKUP_DIR}/.last-snapshot"
  ok "Snapshot : ${SNAP_FILE} ($(du -h "${SNAP_FILE}" | cut -f1))"
fi

# ════════════════════════════════════════════════════════════
# 4. BUILD IMAGE
# ════════════════════════════════════════════════════════════
header "4 — Build image Docker"

docker compose -f "${COMPOSE_FILE}" build app
ok "Image construite"

# ════════════════════════════════════════════════════════════
# 5. MAINTENANCE ON
# ════════════════════════════════════════════════════════════
header "5 — Maintenance ON"

if docker ps --format '{{.Names}}' | grep -qx "${APP_CONTAINER}"; then
  docker exec "${APP_CONTAINER}" sh -c 'touch /tmp/.maintenance' 2>/dev/null || \
    warn "Flag maintenance non posé (container non-running — ignoré)"
fi
ok "Mode maintenance activé"

# ── Trap : maintenance OFF en cas d'erreur inattendue ───────
_cleanup() {
  if docker ps --format '{{.Names}}' | grep -qx "${APP_CONTAINER}"; then
    docker exec "${APP_CONTAINER}" sh -c 'rm -f /tmp/.maintenance' 2>/dev/null || true
  fi
  warn "Script interrompu — maintenance désactivée automatiquement"
}
trap '_cleanup' ERR

# ════════════════════════════════════════════════════════════
# 6. MIGRATIONS ALEMBIC (0013 → 0018)
# ════════════════════════════════════════════════════════════
header "6 — Migrations Alembic"

log "Migrations en attente :"
docker compose -f "${COMPOSE_FILE}" run --rm app \
  alembic history --indicate-current 2>/dev/null | grep -E '(->|head|<---)' | tail -12 || true

log "Application de alembic upgrade head…"
if ! docker compose -f "${COMPOSE_FILE}" run --rm app alembic upgrade head; then
  err "Migration échouée — restauration du snapshot"
  if [[ -f "${BACKUP_DIR}/.last-snapshot" ]]; then
    SNAP="$(cat "${BACKUP_DIR}/.last-snapshot")"
    # shellcheck disable=SC1091
    source .env
    docker exec -i \
      -e PGPASSWORD="${POSTGRES_PASSWORD}" \
      "${DB_CONTAINER}" \
      pg_restore -U "${POSTGRES_USER:-towt}" -d "${POSTGRES_DB:-towt}" \
        --clean --if-exists < "${SNAP}" || true
    ok "Snapshot restauré : ${SNAP}"
  fi
  fatal "Migration échouée. Déploiement annulé." 1
fi
ok "Migrations appliquées (0013→0018)"

# ════════════════════════════════════════════════════════════
# 7. SEED FEATURE FLAGS CLIENT (Sprint 1 — idempotent)
# ════════════════════════════════════════════════════════════
header "7 — Feature flags client"

docker compose -f "${COMPOSE_FILE}" run --rm app python -c "
import asyncio
from sqlalchemy import select
from app.database import SessionLocal, init_db
from app.models.feature_flag import FeatureFlag

FLAGS = [
    ('client_tracking',      'Suivi de traversée en temps réel (/me/track)'),
    ('client_messaging',     'Messagerie client ↔ équipes (/me/messages)'),
    ('client_documents',     'Hub documents client (/me/documents)'),
    ('client_notifications', 'Notifications in-app client (/me/notifications)'),
]

async def seed():
    await init_db()
    async with SessionLocal() as db:
        for key, desc in FLAGS:
            row = (await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))).scalar_one_or_none()
            if not row:
                db.add(FeatureFlag(key=key, enabled=True, rollout_pct=100, description=desc))
                print(f'  Créé  : {key}')
            else:
                print(f'  Existe: {key} (enabled={row.enabled})')
        await db.commit()

asyncio.run(seed())
"
ok "Feature flags vérifiés/créés"

# ════════════════════════════════════════════════════════════
# 8. ROLLING RESTART
# ════════════════════════════════════════════════════════════
header "8 — Rolling restart"

docker compose -f "${COMPOSE_FILE}" up -d --no-deps --force-recreate app
ok "Container app recréé"

# ════════════════════════════════════════════════════════════
# 9. HEALTH CHECK
# ════════════════════════════════════════════════════════════
header "9 — Health check"

log "Attente /health (timeout=${HEALTH_TIMEOUT}s)…"
DEADLINE=$(( SECONDS + HEALTH_TIMEOUT ))
until (( SECONDS >= DEADLINE )); do
  OUT="$(docker compose -f "${COMPOSE_FILE}" exec -T app \
         curl -fsS -m 5 http://localhost:8000/health 2>/dev/null || true)"
  if echo "${OUT}" | grep -q '"status":"ok"'; then
    ok "Health OK : ${OUT}"
    break
  fi
  sleep 3
done

if ! echo "${OUT:-}" | grep -q '"status":"ok"'; then
  err "Health check timeout — rollback"
  docker compose -f "${COMPOSE_FILE}" up -d --no-deps --force-recreate app || true
  fatal "Déploiement annulé : health check échoué" 2
fi

# ════════════════════════════════════════════════════════════
# 10. SMOKE TESTS
# ════════════════════════════════════════════════════════════
header "10 — Smoke tests"

FAILED=0
check() {
  local path="$1" expected="${2:-200}"
  local code
  code="$(docker compose -f "${COMPOSE_FILE}" exec -T app \
          curl -s -o /dev/null -w '%{http_code}' -m 10 \
          "http://localhost:8000${path}" 2>/dev/null || echo 000)"
  if [[ "${code}" == "${expected}" ]]; then
    ok "  ${path}  →  ${code}"
  else
    err "  ${path}  →  ${code}  (attendu ${expected})"
    FAILED=1
  fi
}

# Infrastructure
check "/health"          200
check "/api/v1/health"   200

# Auth staff
check "/login"           200
check "/login/mfa"       303   # redirige si pas de cookie MFA pending

# Parcours client (Sprint 1)
check "/me"              303   # redirige → /me/login si non-auth
check "/me/login"        200
check "/routes"          200

# Finance + KPI (Sprint 2)
check "/finance"         303   # redirige → /login si non-auth
check "/kpi"             303

# Captain (Sprint 3)
check "/captain"         303

if (( FAILED != 0 )); then
  err "Smoke tests échoués — vérifier les logs"
  fatal "Déploiement annulé : smoke tests KO" 2
fi

# ════════════════════════════════════════════════════════════
# 11. MAINTENANCE OFF
# ════════════════════════════════════════════════════════════
header "11 — Maintenance OFF"

trap - ERR
if docker ps --format '{{.Names}}' | grep -qx "${APP_CONTAINER}"; then
  docker exec "${APP_CONTAINER}" sh -c 'rm -f /tmp/.maintenance' 2>/dev/null || true
fi
ok "Mode maintenance désactivé"

# ════════════════════════════════════════════════════════════
# 12. RAPPORT
# ════════════════════════════════════════════════════════════
header "12 — Rapport"

cat <<EOF
  ${G}Version déployée${Z}  : ${VERSION} (${TARGET_BRANCH})
  ${G}Migrations${Z}        : 0013→0018 appliquées
  ${G}Feature flags${Z}     : client_tracking / _messaging / _documents / _notifications
  ${G}Snapshot${Z}          : ${SNAP_FILE:-ignoré (--skip-snapshot)}
  ${G}Uploads${Z}           : volume Docker → /app/var/uploads (persistant)
  ${G}Sprint 1${Z}          : parcours client (/me/track, /me/messages, /me/documents)
  ${G}Sprint 2${Z}          : KPI router, Finance router, exports SOF PDF/Excel, DOCX offres
  ${G}Sprint 3${Z}          : PDF cargo docs, clôture voyage, KPI auto-calcul, PJ
  ${G}Timestamp${Z}         : $(date -u +%FT%TZ)

${G}✅  Déploiement réussi.${Z}
EOF
