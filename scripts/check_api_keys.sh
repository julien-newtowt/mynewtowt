#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# check_api_keys.sh — vérifie la présence des clés / API tokens dans .env.
#
# Lecture seule : ne révèle JAMAIS les valeurs (seulement « renseigné » +
# longueur). Regroupe par fonctionnalité et distingue :
#   - OBLIGATOIRE      : l'app refuse de démarrer si absent / valeur par défaut
#   - CARTO            : au moins un token tuiles (MapTiler préféré, MapBox repli)
#   - INTÉGRATION      : optionnel — l'endpoint/feature renvoie 503 ou se désactive
#
# Usage :
#   ./scripts/check_api_keys.sh                 # vérifie le .env du projet
#   ./scripts/check_api_keys.sh --container     # compare aussi avec le conteneur app
#   ENV_FILE=/chemin/.env ./scripts/check_api_keys.sh
#
# Codes de sortie :
#   0 = clés obligatoires OK
#   2 = clé obligatoire manquante / restée sur sa valeur par défaut
#   (les intégrations optionnelles absentes → avertissement, non bloquant)
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"
APP_SERVICE="${APP_SERVICE:-app}"
CONTAINER_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER_CHECK=1; shift ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Argument inconnu : $1" >&2; exit 1 ;;
  esac
done

if [[ -t 1 ]]; then
  c_green=$'\033[0;32m'; c_yellow=$'\033[0;33m'; c_red=$'\033[0;31m'
  c_blue=$'\033[0;34m'; c_dim=$'\033[2m'; c_reset=$'\033[0m'
else
  c_green=""; c_yellow=""; c_red=""; c_blue=""; c_dim=""; c_reset=""
fi
ok()    { printf '  %s✓%s %-22s %s%s%s\n'  "$c_green"  "$c_reset" "$1" "$c_dim" "${2:-}" "$c_reset"; }
miss()  { printf '  %s✗%s %-22s %s%s%s\n'  "$c_red"    "$c_reset" "$1" "$c_dim" "${2:-}" "$c_reset"; }
warng() { printf '  %s•%s %-22s %s%s%s\n'  "$c_yellow" "$c_reset" "$1" "$c_dim" "${2:-}" "$c_reset"; }
head_() { printf '\n%s== %s ==%s\n' "$c_blue" "$1" "$c_reset"; }

# Valeur d'une clé dans .env (vide si absente / non renseignée).
env_get() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  # Dernière occurrence, en retirant un éventuel commentaire en fin de ligne.
  sed -n "s/^${key}=\(.*\)$/\1/p" "$ENV_FILE" | tail -n1 | sed 's/[[:space:]]*#.*$//' | sed 's/[[:space:]]*$//'
}

# Longueur masquée (jamais la valeur en clair).
masked() { local v="$1"; [[ -n "$v" ]] && printf '(renseigné · %d car.)' "${#v}" || printf ''; }

# Valeur côté conteneur (présence uniquement) pour détecter une dérive host↔conteneur.
container_get() {
  local key="$1"
  command -v docker >/dev/null 2>&1 || { echo "__NODOCKER__"; return; }
  docker compose -f "$COMPOSE_FILE" exec -T "$APP_SERVICE" printenv "$key" 2>/dev/null || echo ""
}

if [[ ! -f "$ENV_FILE" ]]; then
  printf '%s.env introuvable : %s%s\n' "$c_red" "$ENV_FILE" "$c_reset" >&2
  printf 'Créez-le depuis le modèle : cp .env.example .env (puis renseignez les valeurs).\n' >&2
  exit 2
fi
printf '%sVérification des clés API — %s%s\n' "$c_blue" "$ENV_FILE" "$c_reset"

blocking=0
opt_missing=0

# --- 1. Obligatoires (anti-démarrage) : "KEY|motif-faible|conséquence" -------
REQUIRED=(
  "SECRET_KEY|change_me_to_a_random_32_chars_or_more_string_here_please|app refuse de démarrer"
  "DATABASE_URL|change_me_local|app refuse de démarrer"
)
head_ "Obligatoires"
for entry in "${REQUIRED[@]}"; do
  IFS='|' read -r key bad cons <<<"$entry"
  cur="$(env_get "$key")"
  if [[ -z "$cur" ]]; then
    miss "$key" "manquant → $cons"; blocking=1
  elif [[ -n "$bad" && "$cur" == *"$bad"* ]]; then
    miss "$key" "valeur par défaut → $cons"; blocking=1
  else
    ok "$key" "$(masked "$cur")"
  fi
done

# --- 2. Cartographie : au moins l'un des deux --------------------------------
head_ "Cartographie (cartes MapLibre)"
mt="$(env_get MAPTILER_TOKEN)"; mb="$(env_get MAPBOX_TOKEN)"
if [[ -n "$mt" ]]; then ok "MAPTILER_TOKEN" "$(masked "$mt")"
elif [[ -n "$mb" ]]; then ok "MAPBOX_TOKEN" "$(masked "$mb") (repli)"
else
  warng "MAPTILER_TOKEN" "absent → carte raster OSM lente (tracking/navigation dégradés)"
  opt_missing=1
fi

# --- 3. Intégrations optionnelles : "KEY|conséquence si absent" --------------
FEATURES=(
  "TRACKING_API_TOKEN|POST /api/tracking/upload → 503 (ingestion positions satcom)"
  "WEATHER_API_TOKEN|POST /api/weather/refresh → 503 (snapshot météo 30 min, page Navigation)"
  "WINDY_API_KEY|météo via Windy indisponible → repli automatique Open-Meteo"
  "TICKETS_SLA_API_TOKEN|POST /api/tickets/escalate-sla → 503 (escalade SLA)"
  "VEILLE_API_TOKEN|POST /api/veille/refresh → 503 (veille actualité)"
  "NEWSDATA_API_KEY|/veille affiche « non configuré »"
  "ANTHROPIC_API_KEY|chatbot Kairos AI désactivé"
  "PIPEDRIVE_API_TOKEN|synchro CRM leads désactivée"
  "MARAD_API_TOKEN|connexion Marad désactivée (crew + plannings) → /crew sans bouton sync"
  "MARAD_SYNC_TOKEN|POST /api/marad/refresh → 503 (cron sync crew/plannings Marad)"
)
head_ "Intégrations (optionnelles)"
for entry in "${FEATURES[@]}"; do
  IFS='|' read -r key cons <<<"$entry"
  cur="$(env_get "$key")"
  if [[ -n "$cur" ]]; then ok "$key" "$(masked "$cur")"
  else warng "$key" "absent → $cons"; opt_missing=1; fi
done

# --- 3a-bis. Vente à bord — paiement carte (Stripe), secure-by-default -------
# Optionnel : sans STRIPE_SECRET_KEY la voie carte renvoie 503 (l'espèce reste
# disponible), donc NON bloquant. Mais on signale les incohérences qui font
# « échouer silencieusement » l'encaissement CB — pièges vécus en prod :
#   - clé de TEST (sk_test_) déployée en PRODUCTION → aucun paiement réel ;
#   - webhook non configuré → POST /webhooks/stripe = 503 : une vente CB payée
#     ne bascule jamais « Payée » automatiquement ;
#   - format de clé inattendu.
# Ne révèle jamais la valeur : uniquement présence + mode (live/test) + longueur.
head_ "Vente à bord — paiement carte (Stripe)"
stripe_sk="$(env_get STRIPE_SECRET_KEY)"
stripe_wh="$(env_get STRIPE_WEBHOOK_SECRET)"
stripe_app_env="$(env_get APP_ENV)"
if [[ -z "$stripe_sk" ]]; then
  warng "STRIPE_SECRET_KEY" "absent → encaissement carte indisponible (503) ; seul l'espèce fonctionne"
  opt_missing=1
else
  case "$stripe_sk" in
    sk_live_*|rk_live_*)
      ok "STRIPE_SECRET_KEY" "$(masked "$stripe_sk") · live" ;;
    sk_test_*|rk_test_*)
      if [[ "$stripe_app_env" == "production" ]]; then
        warng "STRIPE_SECRET_KEY" "clé de TEST en PRODUCTION → aucun paiement réel encaissé"
        opt_missing=1
      else
        ok "STRIPE_SECRET_KEY" "$(masked "$stripe_sk") · test"
      fi ;;
    *)
      warng "STRIPE_SECRET_KEY" "$(masked "$stripe_sk") — format inattendu (attendu sk_live_/sk_test_)"
      opt_missing=1 ;;
  esac
  # Le webhook de règlement n'a de sens que si la voie carte est active.
  if [[ -z "$stripe_wh" ]]; then
    warng "STRIPE_WEBHOOK_SECRET" "absent → POST /webhooks/stripe = 503 : la vente CB payée ne bascule pas « Payée » (endpoint = /webhooks/stripe, événement checkout.session.completed)"
    opt_missing=1
  elif [[ "$stripe_wh" != whsec_* ]]; then
    warng "STRIPE_WEBHOOK_SECRET" "$(masked "$stripe_wh") — format inattendu (attendu whsec_…)"
    opt_missing=1
  else
    ok "STRIPE_WEBHOOK_SECRET" "$(masked "$stripe_wh")"
  fi
fi

# --- 3b. Exhaustivité : toute clé ACTIVE de .env.example doit exister dans .env
# Garantit qu'aucune clé nouvellement déclarée (ex. MARAD_API_TOKEN /
# MARAD_SYNC_TOKEN) n'est oubliée au déploiement, même si elle n'est pas listée
# explicitement plus haut. Non bloquant (avertissement) — mais affiché à chaque
# run de deploy.sh. Seules les lignes NON commentées de .env.example comptent.
EXAMPLE_FILE="${EXAMPLE_FILE:-$ROOT/.env.example}"
if [[ -f "$EXAMPLE_FILE" ]]; then
  head_ "Exhaustivité (.env vs .env.example)"
  exhaustive_ok=1
  while IFS= read -r key; do
    [[ -z "$key" ]] && continue
    if ! grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
      warng "$key" "déclaré dans .env.example mais ABSENT de .env → à installer"
      opt_missing=1; exhaustive_ok=0
    fi
  done < <(sed -nE 's/^([A-Z][A-Z0-9_]+)=.*/\1/p' "$EXAMPLE_FILE" | sort -u)
  (( exhaustive_ok == 1 )) && ok ".env" "exhaustif (toutes les clés de .env.example présentes)"
fi

# --- 4. Dérive host ↔ conteneur (optionnelle) --------------------------------
if (( CONTAINER_CHECK == 1 )); then
  head_ "Cohérence host ↔ conteneur ${APP_SERVICE}"
  drift=0
  for key in SECRET_KEY WEATHER_API_TOKEN WINDY_API_KEY TRACKING_API_TOKEN \
             VEILLE_API_TOKEN MAPTILER_TOKEN; do
    host_v="$(env_get "$key")"
    cont_v="$(container_get "$key")"
    if [[ "$cont_v" == "__NODOCKER__" ]]; then
      warng "docker" "indisponible — comparaison ignorée"; break
    fi
    host_set=$([[ -n "$host_v" ]] && echo 1 || echo 0)
    cont_set=$([[ -n "$cont_v" ]] && echo 1 || echo 0)
    if [[ "$host_set" != "$cont_set" ]]; then
      miss "$key" "dérive : .env=$host_set conteneur=$cont_set → recréer le conteneur"; drift=1
    elif [[ -n "$host_v" && "$host_v" != "$cont_v" ]]; then
      miss "$key" "valeur .env ≠ conteneur → docker compose up -d --force-recreate ${APP_SERVICE}"; drift=1
    else
      ok "$key" "host = conteneur"
    fi
  done
  (( drift == 1 )) && opt_missing=1
fi

# --- 5. Bilan ----------------------------------------------------------------
echo
if (( blocking == 1 )); then
  printf '%sÉCHEC : une clé obligatoire manque ou est restée par défaut.%s\n' "$c_red" "$c_reset" >&2
  printf 'Renseignez le .env (cf. .env.example) avant de déployer.\n' >&2
  exit 2
fi
if (( opt_missing == 1 )); then
  printf '%sOK (obligatoires présentes) — intégrations optionnelles incomplètes (voir •).%s\n' "$c_yellow" "$c_reset"
else
  printf '%sOK — toutes les clés vérifiées sont présentes.%s\n' "$c_green" "$c_reset"
fi
exit 0
