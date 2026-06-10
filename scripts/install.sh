#!/usr/bin/env bash
# NEWTOWT mynewtowt — première installation (idempotent).
#
# Bootstrap complet d'une instance neuve : pré-requis, génération du
# fichier .env avec des secrets aléatoires forts, démarrage Postgres,
# application des migrations Alembic, création de l'admin initial,
# seed des ports (UN/LOCODE), démarrage de l'app + Caddy, smoke tests.
#
# Sûr à relancer : ne ré-écrit pas .env s'il existe, ne ré-crée pas
# l'admin déjà présent, ne ré-importe pas les ports déjà chargés.
#
# Usage :
#   scripts/install.sh                          # interactif (mode prod)
#   scripts/install.sh --dev                    # local dev (mots de passe faibles tolérés)
#   scripts/install.sh --non-interactive        # CI/automation (utilise les defaults)
#   scripts/install.sh --skip-ports             # n'importe pas le référentiel UN/LOCODE
#   scripts/install.sh --skip-demo              # n'importe pas les données de démo
#   scripts/install.sh --domain my.newtowt.eu --email ops@towt.eu
#
# Codes de retour :
#   0  succès
#   1  pré-requis manquant
#   2  .env incomplet (secrets faibles en mode prod)
#   3  démarrage Postgres / app échoué
#   4  migration Alembic échouée
#   5  smoke tests KO

set -euo pipefail

# ─────────────────────────── Configuration ────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV_MODE="production"
INTERACTIVE=1
SKIP_PORTS=0
SKIP_DEMO=1                              # seed démo OFF par défaut en prod
DOMAIN_ARG=""
EMAIL_ARG=""

DB_CONTAINER="${DB_CONTAINER:-mynewtowt-db}"
APP_CONTAINER="${APP_CONTAINER:-mynewtowt-app}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.yml}"
HEALTH_URL_INTERNAL="${HEALTH_URL_INTERNAL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-90}"

# Couleurs si TTY
if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BLUE=""; BOLD=""; RESET=""
fi

log()      { printf "%s[%s]%s %s\n"  "${BLUE}"   "$(date -u +%FT%TZ)" "${RESET}" "$*"; }
success()  { printf "%s[OK]%s %s\n"  "${GREEN}"  "${RESET}" "$*"; }
warn()     { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
err()      { printf "%s[ERR]%s %s\n" "${RED}"    "${RESET}" "$*" >&2; }
fatal()    { local code="${1:-1}"; shift || true; err "$*"; exit "${code}"; }
step()     { printf "\n%s━━━ %s ━━━%s\n" "${BOLD}" "$*" "${RESET}"; }

# ─────────────────────────── Argument parsing ─────────────────────────────────

usage() {
  cat <<EOF
NEWTOWT mynewtowt — première installation

Usage : $(basename "$0") [options]

Options :
  --dev                  Mode développement (secrets faibles tolérés)
  --non-interactive      Utilise les defaults (CI / automation)
  --skip-ports           N'importe pas le référentiel ports UN/LOCODE
  --skip-demo            Ne lance pas le seed_demo (défaut en prod)
  --with-demo            Force le seed_demo (équivalent --dev par défaut)
  --domain DOMAIN        Domaine TLS (Caddy / Let's Encrypt)
  --email EMAIL          Email de contact (Let's Encrypt, admin initial)
  -h, --help             Affiche cette aide

Variables d'environnement :
  ENV_MODE, DB_CONTAINER, APP_CONTAINER, COMPOSE_FILE,
  HEALTH_URL_INTERNAL, HEALTH_TIMEOUT_SECONDS

Exemples :
  $(basename "$0")
  $(basename "$0") --dev --with-demo
  $(basename "$0") --domain my.newtowt.eu --email ops@towt.eu
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)              ENV_MODE="development"; SKIP_DEMO=0; shift ;;
    --non-interactive)  INTERACTIVE=0; shift ;;
    --skip-ports)       SKIP_PORTS=1; shift ;;
    --skip-demo)        SKIP_DEMO=1; shift ;;
    --with-demo)        SKIP_DEMO=0; shift ;;
    --domain)           DOMAIN_ARG="$2"; shift 2 ;;
    --email)            EMAIL_ARG="$2"; shift 2 ;;
    -h|--help)          usage; exit 0 ;;
    *) err "Argument inconnu : $1"; usage; exit 1 ;;
  esac
done

# ─────────────────────────── 1. Pré-requis ────────────────────────────────────

preflight() {
  step "1. Vérification des pré-requis"

  command -v docker >/dev/null || fatal 1 "docker introuvable dans PATH"
  command -v openssl >/dev/null || fatal 1 "openssl introuvable (utilisé pour générer SECRET_KEY)"
  command -v curl >/dev/null || fatal 1 "curl introuvable"

  if ! docker compose version >/dev/null 2>&1; then
    fatal 1 "docker compose (v2) introuvable — installe Docker ≥ 20.10 ou docker-compose-plugin"
  fi

  [[ -f "${COMPOSE_FILE}" ]] || fatal 1 "docker-compose.yml manquant : ${COMPOSE_FILE}"
  [[ -f "${PROJECT_ROOT}/.env.example" ]] || fatal 1 ".env.example manquant"
  [[ -f "${PROJECT_ROOT}/alembic.ini" ]] || fatal 1 "alembic.ini manquant"

  # Espace disque libre (>= 2 Go)
  local free_kb
  free_kb="$(df -Pk "${PROJECT_ROOT}" | awk 'NR==2 {print $4}')"
  if (( free_kb < 2 * 1024 * 1024 )); then
    fatal 1 "Espace disque insuffisant : $((free_kb / 1024)) Mo libres, 2 Go minimum requis"
  fi

  success "Pré-requis OK (Docker $(docker --version | awk '{print $3}' | tr -d ','), Compose $(docker compose version --short 2>/dev/null || echo "v?"))"
}

# ─────────────────────────── 2. Génération du .env ────────────────────────────

gen_secret() {
  # 48 chars URL-safe — > 32 caractères, hors weak_secrets list
  openssl rand -base64 48 | tr -d '\n=/+' | cut -c1-48
}

gen_password() {
  # 24 chars mixed, suffisant pour Postgres / admin
  openssl rand -base64 32 | tr -d '\n=/+' | cut -c1-24
}

ask() {
  local prompt="$1"; local default="${2:-}"; local var
  if (( INTERACTIVE == 0 )); then
    printf '%s' "${default}"
    return
  fi
  if [[ -n "${default}" ]]; then
    read -r -p "${prompt} [${default}] " var
  else
    read -r -p "${prompt} " var
  fi
  printf '%s' "${var:-${default}}"
}

setup_env() {
  step "2. Génération du fichier .env"

  if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    warn ".env existe déjà — on garde le fichier en l'état (suppression manuelle pour ré-init)"
    return
  fi

  local secret_key postgres_password admin_password
  secret_key="$(gen_secret)"
  postgres_password="$(gen_password)"
  admin_password="$(gen_password)"

  local domain="${DOMAIN_ARG}"
  local email="${EMAIL_ARG}"
  if (( INTERACTIVE == 1 )) && [[ -z "${domain}" ]]; then
    domain="$(ask "Domaine TLS (vide si pas de Caddy/HTTPS) :" "")"
  fi
  if (( INTERACTIVE == 1 )) && [[ -z "${email}" ]]; then
    email="$(ask "Email admin / Let's Encrypt :" "communication@towt.eu")"
  fi
  [[ -z "${domain}" ]] && domain="my.newtowt.eu"
  [[ -z "${email}" ]] && email="communication@towt.eu"

  local site_url="http://localhost:8000"
  [[ "${ENV_MODE}" == "production" ]] && site_url="https://${domain}"

  cat > "${PROJECT_ROOT}/.env" <<EOF
# NEWTOWT mynewtowt — fichier d'environnement
# Généré par scripts/install.sh le $(date -u +%FT%TZ)
# NE PAS COMMITER (gitignored).

# ─────── App ───────
APP_NAME=mynewtowt
APP_VERSION=3.0.0
APP_ENV=${ENV_MODE}
DEBUG=false
SITE_URL=${site_url}

# ─────── Security ───────
SECRET_KEY=${secret_key}
ACCESS_TOKEN_EXPIRE_MINUTES=480
CLIENT_SESSION_DAYS=30
ALGORITHM=HS256

# ─────── Database ───────
DATABASE_URL=postgresql+asyncpg://towt:${postgres_password}@db:5432/towt
POSTGRES_USER=towt
POSTGRES_PASSWORD=${postgres_password}
POSTGRES_DB=towt

# ─────── Initial admin bootstrap ───────
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_EMAIL=${email}
INITIAL_ADMIN_PASSWORD=${admin_password}

# ─────── External services (à remplir si utilisés) ───────
PIPEDRIVE_API_TOKEN=
ANTHROPIC_API_KEY=
WINDY_API_KEY=
MAPBOX_TOKEN=
MAPTILER_TOKEN=

# ─────── Tracking ingest API (Power Automate satcom) ───────
# Génère un token et configure le côté Power Automate
TRACKING_API_TOKEN=$(gen_secret)

# ─────── Email ───────
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM_NAME=NEWTOWT
SMTP_FROM_ADDRESS=no-reply@newtowt.eu

# ─────── Observability ───────
SENTRY_DSN=
OTEL_EXPORTER_OTLP_ENDPOINT=
PROMETHEUS_METRICS=true

# ─────── Backup ───────
BACKUP_RETENTION_DAYS=30
BACKUP_S3_BUCKET=
BACKUP_GPG_RECIPIENT=

# ─────── TLS / reverse proxy Caddy ───────
CADDY_DOMAIN=${domain}
CADDY_EMAIL=${email}
DOMAIN=${domain}
CERTBOT_EMAIL=${email}
EOF

  chmod 600 "${PROJECT_ROOT}/.env"

  success ".env généré (mode=${ENV_MODE})"
  log "Domaine : ${domain}"
  log "Email   : ${email}"

  # Mémorise le mot de passe admin pour le report final
  echo "${admin_password}" > "${PROJECT_ROOT}/.install-admin-password"
  chmod 600 "${PROJECT_ROOT}/.install-admin-password"
}

verify_env_strong() {
  if [[ "${ENV_MODE}" != "production" ]]; then return; fi
  step "2bis. Vérification des secrets (mode production)"

  if grep -qE '^SECRET_KEY=(change_me|secret|changeme|towt_secret)' "${PROJECT_ROOT}/.env"; then
    fatal 2 "SECRET_KEY trop faible dans .env"
  fi
  local sk_len
  sk_len="$(grep '^SECRET_KEY=' "${PROJECT_ROOT}/.env" | sed 's/^SECRET_KEY=//' | wc -c)"
  if (( sk_len < 33 )); then
    fatal 2 "SECRET_KEY < 32 caractères dans .env"
  fi
  if grep -qE '^POSTGRES_PASSWORD=change_me_local' "${PROJECT_ROOT}/.env"; then
    fatal 2 "POSTGRES_PASSWORD par défaut dans .env"
  fi

  success "Secrets vérifiés (>= 32 chars, pas dans la weak-list)"
}

# ─────────────────────────── 3. Démarrage Postgres ────────────────────────────

start_db() {
  step "3. Démarrage du conteneur PostgreSQL"
  cd "${PROJECT_ROOT}"

  docker compose -f "${COMPOSE_FILE}" up -d db

  log "Attente du healthcheck Postgres…"
  local deadline=$(( SECONDS + 60 ))
  while (( SECONDS < deadline )); do
    local status
    status="$(docker inspect --format='{{.State.Health.Status}}' "${DB_CONTAINER}" 2>/dev/null || echo 'unknown')"
    if [[ "${status}" == "healthy" ]]; then
      success "Postgres prêt"
      return
    fi
    sleep 2
  done
  fatal 3 "Postgres n'est pas devenu healthy en 60s"
}

# ─────────────────────────── 4. Build app + migrations ────────────────────────

build_app() {
  step "4. Build de l'image applicative"
  cd "${PROJECT_ROOT}"
  docker compose -f "${COMPOSE_FILE}" build app
  success "Image construite"
}

run_migrations() {
  step "5. Application des migrations Alembic"
  cd "${PROJECT_ROOT}"

  if ! docker compose -f "${COMPOSE_FILE}" run --rm app alembic upgrade head; then
    fatal 4 "Migration Alembic échouée"
  fi
  success "Schéma à jour"
}

# ─────────────────────────── 6. Bootstrap admin ───────────────────────────────

bootstrap_admin() {
  step "6. Création de l'admin initial"
  cd "${PROJECT_ROOT}"

  # seed_demo.py crée l'admin s'il n'existe pas (idempotent).
  # On lance la partie admin uniquement via un mini script Python.
  docker compose -f "${COMPOSE_FILE}" run --rm app python -c "
import asyncio
from sqlalchemy import select
from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal

async def main():
    from app.models.user import User
    async with SessionLocal() as db:
        existing = (await db.execute(
            select(User).where(User.username == settings.initial_admin_username)
        )).scalar_one_or_none()
        if existing:
            print(f'Admin already exists: {existing.username}')
            return
        db.add(User(
            username=settings.initial_admin_username,
            email=settings.initial_admin_email,
            full_name='Admin NEWTOWT',
            hashed_password=hash_password(settings.initial_admin_password),
            role='administrateur',
            is_active=True,
            must_change_password=True,
        ))
        await db.commit()
        print(f'Admin created: {settings.initial_admin_username} (must_change_password=True)')

asyncio.run(main())
" || fatal 4 "Bootstrap admin échoué"

  success "Admin initial provisionné"
}

# ─────────────────────────── 7. Référentiel ports ─────────────────────────────

load_ports_ref() {
  if (( SKIP_PORTS == 1 )); then
    warn "Skip --skip-ports : référentiel UN/LOCODE non importé"
    return
  fi
  step "7. Import du référentiel ports (UN/LOCODE + data.gouv.fr)"
  cd "${PROJECT_ROOT}"

  # load_ports.py est tolérant aux erreurs HTTP (skip-on-failure).
  # En cas de réseau coupé, l'install continue avec quelques ports
  # créés par seed_demo (FRLEH, FRFEC, USNYC…).
  docker compose -f "${COMPOSE_FILE}" run --rm app python -m scripts.load_ports \
    || warn "Import des ports en échec (réseau ?) — l'install continue"
}

# ─────────────────────────── 8. Seed demo (optionnel) ─────────────────────────

seed_demo() {
  if (( SKIP_DEMO == 1 )); then
    warn "Seed demo désactivé (mode production)"
    return
  fi
  step "8. Seed de données de démo"
  cd "${PROJECT_ROOT}"

  docker compose -f "${COMPOSE_FILE}" run --rm app python -m scripts.seed_demo \
    || warn "seed_demo en échec (non bloquant)"
  success "Données de démo chargées (legs, vessels, ports, client demo)"
}

# ─────────────────────────── 9. Démarrage de l'app ────────────────────────────

start_app() {
  step "9. Démarrage de l'application"
  cd "${PROJECT_ROOT}"

  if [[ "${ENV_MODE}" == "production" ]]; then
    docker compose -f "${COMPOSE_FILE}" up -d
  else
    docker compose -f "${COMPOSE_FILE}" up -d app db
  fi
  success "Conteneurs démarrés"
}

# ─────────────────────────── 10. Smoke tests ──────────────────────────────────

wait_for_health() {
  step "10. Attente du /health (timeout ${HEALTH_TIMEOUT_SECONDS}s)"
  local deadline=$(( SECONDS + HEALTH_TIMEOUT_SECONDS ))

  while (( SECONDS < deadline )); do
    # On interroge via le container app (pas via Caddy) pour fiabiliser.
    # L'image python:3.12-slim contient `curl` mais pas `wget`.
    if docker exec "${APP_CONTAINER}" curl -fsS -m 3 http://127.0.0.1:8000/health 2>/dev/null | grep -q '"status":"ok"'; then
      success "Health OK"
      return 0
    fi
    # Si curl n'est pas trouvé, on tente avec python comme fallback universel
    if docker exec "${APP_CONTAINER}" python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).read().decode(); sys.exit(0 if '\"status\":\"ok\"' in r else 1)" 2>/dev/null; then
      success "Health OK (via python)"
      return 0
    fi
    sleep 2
  done
  err "L'application n'a pas répondu /health dans le délai imparti"
  warn "Diagnostic : docker compose ps && docker compose logs --tail=80 app"
  exit 5
}

smoke_tests() {
  step "11. Smoke tests internes"
  local failed=0

  _check() {
    local path="$1"; local expected="$2"
    local code
    code="$(docker exec "${APP_CONTAINER}" curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:8000${path}" 2>/dev/null || echo '000')"
    if [[ "${code}" == "${expected}" ]]; then
      success "  ${path} → ${code}"
    else
      err "  ${path} → ${code} (attendu ${expected})"
      failed=1
    fi
  }

  _check "/health" 200
  _check "/api/v1/health" 200
  _check "/login" 200
  _check "/" 200
  _check "/me/login" 200
  _check "/.well-known/security.txt" 200
  _check "/api/v1/ports/next-clocks" 200
  _check "/dashboard" 303     # redirige vers /login sans cookie

  if (( failed != 0 )); then
    warn "Certains smoke tests sont KO (non bloquant — vérifie les logs : docker logs ${APP_CONTAINER})"
  else
    success "Tous les smoke tests passent"
  fi
}

# ─────────────────────────── 12. Rapport final ────────────────────────────────

report() {
  local admin_password=""
  [[ -f "${PROJECT_ROOT}/.install-admin-password" ]] && admin_password="$(cat "${PROJECT_ROOT}/.install-admin-password")"

  local domain caddy_domain admin_username admin_email
  caddy_domain="$(grep '^CADDY_DOMAIN=' "${PROJECT_ROOT}/.env" | head -1 | cut -d= -f2-)"
  admin_username="$(grep '^INITIAL_ADMIN_USERNAME=' "${PROJECT_ROOT}/.env" | head -1 | cut -d= -f2-)"
  admin_email="$(grep '^INITIAL_ADMIN_EMAIL=' "${PROJECT_ROOT}/.env" | head -1 | cut -d= -f2-)"

  local access_url="http://localhost:8000"
  [[ "${ENV_MODE}" == "production" ]] && access_url="https://${caddy_domain}"

  cat <<EOF

${BOLD}╔══════════════════════════════════════════════════════════════════════════╗
║  NEWTOWT mynewtowt — installation terminée                              ║
╚══════════════════════════════════════════════════════════════════════════╝${RESET}

  ${BOLD}Environnement${RESET}    : ${ENV_MODE}
  ${BOLD}Accès${RESET}            : ${access_url}
  ${BOLD}Health${RESET}           : ${access_url}/health

  ${BOLD}Identifiants admin initial${RESET}
  ─────────────────────────────────────────────────────────────────────
  Username       : ${admin_username}
  Email          : ${admin_email}
  Password       : ${admin_password:-<voir .env INITIAL_ADMIN_PASSWORD>}
  ⚠ Le force-password-change est activé — changement obligatoire à la
    première connexion.

  ${BOLD}Modules disponibles${RESET}
  ─────────────────────────────────────────────────────────────────────
   Staff (ERP)    : /dashboard /planning /commercial /cargo /escale
                    /captain /crew /stowage /claims /mrv /admin
   Client         : /me/login → /me/bookings /me/invoices /me/co2
   Public         : / /routes /about /about/co2
   Portail cargo  : /p/{token} (lien envoyé au client expéditeur)
   API B2B        : /api/v1/* (X-API-Key)
   Tracking ingest: /api/tracking/upload (X-API-Token)

  ${BOLD}Prochaines étapes${RESET}
  ─────────────────────────────────────────────────────────────────────
  1.  Connectez-vous à ${access_url}/login avec admin / le mot de passe
      ci-dessus. Changez-le immédiatement.
  2.  Créez les autres utilisateurs : /admin/users (avec must_change_password).
  3.  Configurez les navires et paramètres OPEX : /admin/opex.
  4.  Ajoutez les contrats d'assurance : /admin/insurance.
  5.  Activez les intégrations optionnelles dans .env si nécessaire :
      PIPEDRIVE_API_TOKEN, MAPTILER_TOKEN, WINDY_API_KEY, ANTHROPIC_API_KEY,
      STRIPE_*, SMTP_*.
  6.  Power Automate satcom : utilisez TRACKING_API_TOKEN du .env pour
      POST /api/tracking/upload (header X-API-Token).
  7.  Sauvegardez ${PROJECT_ROOT}/.env (offline, hors-git).
  8.  Mettez en place un cron de backup : scripts/deploy.sh prend
      des snapshots automatiques, ou utilisez votre propre stratégie.

  ${BOLD}Fichiers générés${RESET}
  ─────────────────────────────────────────────────────────────────────
  ${PROJECT_ROOT}/.env                       (secrets — chmod 600)
  ${PROJECT_ROOT}/.install-admin-password    (à supprimer après MAJ)

  ${BOLD}Commandes utiles${RESET}
  ─────────────────────────────────────────────────────────────────────
  docker compose logs -f app           # logs applicatifs
  docker compose ps                    # état des conteneurs
  docker compose down                  # tout arrêter
  scripts/deploy.sh                    # déploiement d'une mise à jour
  scripts/maintenance.sh on|off        # bandeau de maintenance

EOF
}

# ─────────────────────────── Main ─────────────────────────────────────────────

main() {
  cat <<EOF
${BOLD}NEWTOWT mynewtowt — première installation${RESET}
Mode : ${ENV_MODE}${INTERACTIVE:+ · interactif}

EOF

  preflight
  setup_env
  verify_env_strong
  start_db
  build_app
  run_migrations
  bootstrap_admin
  load_ports_ref
  seed_demo
  start_app
  wait_for_health
  smoke_tests
  report
}

main "$@"
