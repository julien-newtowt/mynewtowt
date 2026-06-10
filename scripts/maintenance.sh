#!/usr/bin/env bash
# Toggle maintenance mode on/off without redeploying.
#
# Usage:
#   scripts/maintenance.sh on
#   scripts/maintenance.sh off
#   scripts/maintenance.sh status

set -euo pipefail

APP_CONTAINER="${APP_CONTAINER:-mynewtowt-app}"

case "${1:-status}" in
  on)
    docker exec "${APP_CONTAINER}" sh -c 'touch /tmp/.maintenance' \
      && echo "Maintenance mode: ON"
    ;;
  off)
    docker exec "${APP_CONTAINER}" sh -c 'rm -f /tmp/.maintenance' \
      && echo "Maintenance mode: OFF"
    ;;
  status)
    if docker exec "${APP_CONTAINER}" test -f /tmp/.maintenance 2>/dev/null; then
      echo "Maintenance mode: ON"
    else
      echo "Maintenance mode: OFF"
    fi
    ;;
  *)
    echo "Usage: $(basename "$0") {on|off|status}" >&2
    exit 1
    ;;
esac
