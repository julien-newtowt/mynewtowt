#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# set_marad_keys.sh — installe/maj MARAD_API_TOKEN et MARAD_SYNC_TOKEN dans .env
#
# - MARAD_API_TOKEN  : clé d'API Marad (lecture seule). Fournie par l'éditeur.
# - MARAD_SYNC_TOKEN : secret X-API-Token du cron interne POST /api/marad/refresh
#                      (à toi de le choisir ; --gen-sync en génère un aléatoire).
#
# Idempotent : remplace la ligne existante (active) ou l'ajoute. Ne révèle
# JAMAIS les valeurs (seulement la longueur). Préserve le reste du .env.
#
# Usage :
#   scripts/set_marad_keys.sh                      # interactif (saisie masquée)
#   scripts/set_marad_keys.sh --api-token "<clé>" --gen-sync
#   scripts/set_marad_keys.sh --api-token "<clé>" --sync-token "<secret>"
#   MARAD_API_TOKEN=... MARAD_SYNC_TOKEN=... scripts/set_marad_keys.sh   # via env
#   ENV_FILE=/chemin/.env scripts/set_marad_keys.sh
#
# Options :
#   --api-token VALUE   valeur de MARAD_API_TOKEN
#   --sync-token VALUE  valeur de MARAD_SYNC_TOKEN
#   --gen-sync          génère un MARAD_SYNC_TOKEN aléatoire si non fourni
#   --env-file PATH     fichier cible (défaut : <repo>/.env)
#   -h, --help
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-$ROOT/.env.example}"

API_TOKEN="${MARAD_API_TOKEN:-}"
SYNC_TOKEN="${MARAD_SYNC_TOKEN:-}"
GEN_SYNC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-token)  API_TOKEN="$2"; shift 2 ;;
    --sync-token) SYNC_TOKEN="$2"; shift 2 ;;
    --gen-sync)   GEN_SYNC=1; shift ;;
    --env-file)   ENV_FILE="$2"; shift 2 ;;
    -h|--help)    sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Argument inconnu : $1" >&2; exit 1 ;;
  esac
done

if [[ -t 1 ]]; then
  c_green=$'\033[0;32m'; c_yellow=$'\033[0;33m'; c_red=$'\033[0;31m'
  c_blue=$'\033[0;34m'; c_dim=$'\033[2m'; c_reset=$'\033[0m'
else
  c_green=""; c_yellow=""; c_red=""; c_blue=""; c_dim=""; c_reset=""
fi
ok()   { printf '  %s✓%s %-18s %s%s%s\n' "$c_green"  "$c_reset" "$1" "$c_dim" "${2:-}" "$c_reset"; }
warn() { printf '  %s•%s %s\n'           "$c_yellow" "$c_reset" "$1"; }
mask() { local v="$1"; [[ -n "$v" ]] && printf '(renseigné · %d car.)' "${#v}" || printf '(vide)'; }

# Génère un secret aléatoire hexadécimal (≥ 32 octets) : openssl sinon python3.
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then python3 -c 'import secrets;print(secrets.token_hex(32))'
  else echo ""; fi
}

# Remplace (1re ligne active "key=") ou ajoute "key=value" dans $file, sans
# jamais interpréter la valeur (pas de sed -> sûr avec / & % etc.).
set_kv() {
  local key="$1" val="$2" file="$3" tmp found=0
  tmp="$(mktemp)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ $found -eq 0 && "$line" =~ ^[[:space:]]*${key}= ]]; then
      printf '%s=%s\n' "$key" "$val" >> "$tmp"; found=1
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$file"
  (( found == 0 )) && printf '%s=%s\n' "$key" "$val" >> "$tmp"
  cat "$tmp" > "$file"   # préserve les permissions du fichier existant
  rm -f "$tmp"
}

printf '%sInstallation des clés Marad → %s%s\n' "$c_blue" "$ENV_FILE" "$c_reset"

# .env absent : proposer de le créer depuis .env.example.
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$EXAMPLE_FILE" ]]; then
    warn ".env absent — création depuis $(basename "$EXAMPLE_FILE")"
    cp "$EXAMPLE_FILE" "$ENV_FILE"
  else
    : > "$ENV_FILE"
    warn ".env absent — création d'un fichier vide"
  fi
fi

# --- MARAD_API_TOKEN ---------------------------------------------------------
if [[ -z "$API_TOKEN" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "Clé d'API Marad (MARAD_API_TOKEN) : " API_TOKEN; echo
  else
    printf '%sMARAD_API_TOKEN non fourni (ni --api-token, ni env, ni interactif)%s\n' \
      "$c_red" "$c_reset" >&2
    exit 2
  fi
fi
if [[ -z "$API_TOKEN" ]]; then
  printf '%sMARAD_API_TOKEN vide — abandon.%s\n' "$c_red" "$c_reset" >&2
  exit 2
fi

# --- MARAD_SYNC_TOKEN --------------------------------------------------------
if [[ -z "$SYNC_TOKEN" ]]; then
  if (( GEN_SYNC == 1 )); then
    SYNC_TOKEN="$(gen_secret)"
    [[ -n "$SYNC_TOKEN" ]] && ok "MARAD_SYNC_TOKEN" "généré aléatoirement"
  elif [[ -t 0 ]]; then
    read -r -s -p "Secret cron (MARAD_SYNC_TOKEN) [vide = générer] : " SYNC_TOKEN; echo
    [[ -z "$SYNC_TOKEN" ]] && { SYNC_TOKEN="$(gen_secret)"; ok "MARAD_SYNC_TOKEN" "généré aléatoirement"; }
  else
    SYNC_TOKEN="$(gen_secret)"
    [[ -n "$SYNC_TOKEN" ]] && ok "MARAD_SYNC_TOKEN" "généré aléatoirement (non fourni)"
  fi
fi
if [[ -z "$SYNC_TOKEN" ]]; then
  printf '%sImpossible de générer MARAD_SYNC_TOKEN (ni openssl ni python3) — fournissez --sync-token.%s\n' \
    "$c_red" "$c_reset" >&2
  exit 2
fi

# --- Écriture ----------------------------------------------------------------
set_kv "MARAD_API_TOKEN"  "$API_TOKEN"  "$ENV_FILE"
set_kv "MARAD_SYNC_TOKEN" "$SYNC_TOKEN" "$ENV_FILE"

echo
ok "MARAD_API_TOKEN"  "$(mask "$API_TOKEN")"
ok "MARAD_SYNC_TOKEN" "$(mask "$SYNC_TOKEN")"
echo
printf '%sClés installées dans %s%s\n' "$c_green" "$ENV_FILE" "$c_reset"
cat <<EOF

Prochaines étapes :
  1. Vérifier      : ./scripts/check_api_keys.sh
  2. Recharger l'app (prod) : docker compose up -d --force-recreate app
     (ou relancer un déploiement : ./scripts/deploy.sh)
  3. Configurer le cron Power Automate sur POST /api/marad/refresh avec le
     header  X-API-Token: <MARAD_SYNC_TOKEN>  (le même secret que ci-dessus).

Le nom du header d'auth Marad est auto-détecté (ApiKey / ApiToken / X-Api-Key) ;
forcez-le si besoin via MARAD_API_KEY_HEADER dans .env.
EOF
