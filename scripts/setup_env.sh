#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup_env.sh — installe / complète les variables d'environnement manquantes
# pour mynewtowt, de façon idempotente, dans le fichier .env.
#
# Cible en priorité les intégrations signalées par l'audit comme manquantes en
# production : tuiles cartographiques (MapTiler/MapBox), boîte commerciale
# (leads), CRM Pipedrive. Vérifie aussi que les variables OBLIGATOIRES ne sont
# pas restées sur leurs valeurs par défaut (refus de démarrage sinon).
#
# Usage :
#   ./scripts/setup_env.sh                 # interactif (Entrée = ignorer une var)
#   ./scripts/setup_env.sh --non-interactive   # ne lit QUE l'environnement shell
#   MAPTILER_TOKEN=xxx ./scripts/setup_env.sh --non-interactive
#
# Après exécution, recharger le conteneur applicatif :
#   docker compose up -d --force-recreate app
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
EXAMPLE_FILE="$ROOT/.env.example"
INTERACTIVE=1
[[ "${1:-}" == "--non-interactive" ]] && INTERACTIVE=0

# Intégrations optionnelles à compléter : "KEY|description"
OPTIONAL_VARS=(
  "MAPTILER_TOKEN|Tuiles vectorielles MapLibre (préféré). Sinon MAPBOX_TOKEN."
  "MAPBOX_TOKEN|Alias historique des tuiles (repli si pas de MAPTILER_TOKEN)."
  "COMMERCIAL_INBOX_EMAIL|Boîte e-mail recevant les leads (/contact, /devis)."
  "PIPEDRIVE_API_TOKEN|Token API Pipedrive pour la synchro des leads (CRM)."
  "SMTP_HOST|Serveur SMTP (requis pour l'envoi d'e-mails leads & clients)."
)

# Variables obligatoires : (KEY, motif-valeur-faible-à-refuser)
REQUIRED_DEFAULTS=(
  "SECRET_KEY|change_me_to_a_random_32_chars_or_more_string_here_please"
  "DATABASE_URL|change_me_local"
)

c_green=$'\033[0;32m'; c_yellow=$'\033[0;33m'; c_red=$'\033[0;31m'; c_reset=$'\033[0m'
info()  { printf '%s%s%s\n' "$c_green" "$1" "$c_reset"; }
warn()  { printf '%s%s%s\n' "$c_yellow" "$1" "$c_reset"; }
err()   { printf '%s%s%s\n' "$c_red" "$1" "$c_reset" >&2; }

# Valeur actuelle d'une clé dans .env (vide si absente/non renseignée).
env_get() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  sed -n "s/^${key}=\(.*\)$/\1/p" "$ENV_FILE" | tail -n1
}

# Pose KEY=VALUE : remplace en place si la clé existe, ajoute sinon.
env_set() {
  local key="$1" val="$2"
  if [[ -f "$ENV_FILE" ]] && grep -q "^${key}=" "$ENV_FILE"; then
    # Échappe les caractères spéciaux sed du remplacement.
    local esc; esc=$(printf '%s' "$val" | sed -e 's/[\/&|]/\\&/g')
    sed -i.bak "s|^${key}=.*|${key}=${esc}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

# --- 0. .env présent ? sinon le créer depuis l'exemple ---------------------
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$EXAMPLE_FILE" ]]; then
    cp "$EXAMPLE_FILE" "$ENV_FILE"
    warn "Aucun .env — créé depuis .env.example. Complétez les valeurs obligatoires."
  else
    err "Ni .env ni .env.example trouvés dans $ROOT"; exit 1
  fi
fi

changed=0

# --- 1. Intégrations optionnelles -----------------------------------------
info "== Intégrations optionnelles =="
for entry in "${OPTIONAL_VARS[@]}"; do
  key="${entry%%|*}"; desc="${entry#*|}"
  current="$(env_get "$key")"
  if [[ -n "$current" ]]; then
    printf '  %-24s déjà renseigné ✓\n' "$key"; continue
  fi
  # Valeur fournie via l'environnement shell ?
  shell_val="${!key:-}"
  if [[ -n "$shell_val" ]]; then
    env_set "$key" "$shell_val"; changed=1
    printf '  %-24s posé depuis l'\''environnement ✓\n' "$key"; continue
  fi
  if [[ "$INTERACTIVE" -eq 1 ]]; then
    printf '  %s — %s\n' "$key" "$desc"
    read -r -p "    valeur (Entrée = ignorer) : " ans || true
    if [[ -n "${ans:-}" ]]; then env_set "$key" "$ans"; changed=1; fi
  else
    printf '  %-24s %sabsent (non-interactif, ignoré)%s\n' "$key" "$c_yellow" "$c_reset"
  fi
done

# --- 2. Cohérence cartographie --------------------------------------------
if [[ -z "$(env_get MAPTILER_TOKEN)" && -z "$(env_get MAPBOX_TOKEN)" ]]; then
  warn "Aucun token carto (MAPTILER_TOKEN/MAPBOX_TOKEN) — la carte utilisera un raster OSM lent."
fi

# --- 3. Variables obligatoires (anti-démarrage refusé) ---------------------
info "== Variables obligatoires =="
blocking=0
for entry in "${REQUIRED_DEFAULTS[@]}"; do
  key="${entry%%|*}"; bad="${entry#*|}"
  current="$(env_get "$key")"
  if [[ -z "$current" ]]; then
    err "  $key manquant — à renseigner avant démarrage."; blocking=1
  elif [[ "$current" == *"$bad"* ]]; then
    err "  $key encore sur sa valeur par défaut ($bad) — l'app refusera de démarrer."; blocking=1
  else
    printf '  %-24s OK ✓\n' "$key"
  fi
done

# --- 4. Bilan --------------------------------------------------------------
echo
if [[ "$changed" -eq 1 ]]; then
  info "Fichier $ENV_FILE mis à jour."
  warn "Recharger le conteneur : docker compose up -d --force-recreate app"
else
  info "Aucune modification — tout était déjà en place."
fi
[[ "$blocking" -eq 1 ]] && { err "Des variables obligatoires manquent — corrigez avant de déployer."; exit 2; }
exit 0
