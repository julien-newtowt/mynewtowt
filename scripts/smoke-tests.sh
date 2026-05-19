#!/usr/bin/env bash
# NEWTOWT mynewtowt — post-deploy smoke tests.
#
# Usage:
#   scripts/smoke-tests.sh https://staging.my.newtowt.eu
#   scripts/smoke-tests.sh           # defaults to http://127.0.0.1:8000
#
# Runs a handful of HTTP probes against the running app and asserts the
# expected response codes. Exits non-zero on any failure (for CI gating).

set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
TIMEOUT="${TIMEOUT:-10}"

if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; RESET=""
fi

pass()  { printf "%sPASS%s  %s\n" "${GREEN}" "${RESET}" "$*"; }
fail()  { printf "%sFAIL%s  %s\n" "${RED}"   "${RESET}" "$*" >&2; }

declare -i failures=0

check() {
  local path="$1" expected="$2" desc="${3:-$1}"
  local code body
  code="$(curl -s -o /dev/null -w '%{http_code}' -m "${TIMEOUT}" -L --max-redirs 0 "${BASE_URL}${path}" || echo 000)"
  if [[ "${code}" == "${expected}" ]]; then
    pass "${desc} → ${code}"
  else
    fail "${desc} → ${code} (expected ${expected})"
    failures+=1
  fi
}

check_contains() {
  local path="$1" needle="$2" desc="${3:-$1 contains '${needle}'}"
  if curl -fsS -m "${TIMEOUT}" "${BASE_URL}${path}" 2>/dev/null | grep -qF "${needle}"; then
    pass "${desc}"
  else
    fail "${desc} (body did not contain '${needle}')"
    failures+=1
  fi
}

echo "Running smoke tests against ${BASE_URL}"
echo

# Health
check "/health" 200 "GET /health"
check_contains "/health" '"status":"ok"' 'GET /health returns status=ok'

# API v1
check "/api/v1/health" 200 "GET /api/v1/health"
check "/api/v1/spec" 200 "GET /api/v1/spec"
check "/api/v1/routes" 200 "GET /api/v1/routes"

# Public pages
check "/" 200 "GET /"
check "/routes" 200 "GET /routes"
check "/about" 200 "GET /about"
check "/about/co2" 200 "GET /about/co2"
check "/about/legal" 200 "GET /about/legal"
check "/about/privacy" 200 "GET /about/privacy"
check "/about/terms" 200 "GET /about/terms"

# Security
check "/.well-known/security.txt" 200 "GET security.txt"
check_contains "/.well-known/security.txt" "security@newtowt.eu" "security.txt advertises contact"

# Auth gating
check "/login" 200 "GET /login (form)"
check "/me/login" 200 "GET /me/login (form)"
check "/me/register" 200 "GET /me/register (form)"
check "/me" 303 "GET /me redirects unauthenticated"
check "/dashboard" 303 "GET /dashboard redirects unauthenticated"

# 404s
check "/this-route-does-not-exist" 404 "Unknown route → 404"

echo
if (( failures > 0 )); then
  printf "%s%d test(s) failed%s\n" "${RED}" "${failures}" "${RESET}"
  exit 1
fi
printf "%sAll smoke tests passed%s\n" "${GREEN}" "${RESET}"
