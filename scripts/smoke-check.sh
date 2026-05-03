#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1}"
USERNAME="${USERNAME:-admin}"
PASSWORD="${PASSWORD:-}"
TOOLKIT_USER="${TOOLKIT_USER:-${MTX_UI_USER:-admin}}"
TOOLKIT_PASSWORD="${TOOLKIT_PASSWORD:-${MTX_UI_PASSWORD:-}}"
TOOLKIT_API_URL="${TOOLKIT_API_URL:-http://127.0.0.1:5002}"

if [[ -z "$PASSWORD" ]]; then
  echo "PASSWORD is required for /api/auth/login" >&2
  exit 1
fi

if [[ -z "$TOOLKIT_PASSWORD" ]]; then
  echo "TOOLKIT_PASSWORD is required for MTX Toolkit smoke checks" >&2
  exit 1
fi

echo "== Login and get JWT =="
LOGIN_JSON="$(curl -fsS -X POST "${BASE_URL}/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\"}")"
TOKEN="$(python -c 'import json,sys; print(json.load(sys.stdin)["token"])' <<<"$LOGIN_JSON")"
echo "login ok for ${USERNAME}"

echo
echo "== /api/auth/me =="
curl -fsS "${BASE_URL}/api/auth/me" \
  -H "Authorization: Bearer ${TOKEN}" | python -m json.tool

echo
echo "== /api/system/stack =="
curl -fsS "${BASE_URL}/api/system/stack" \
  -H "Authorization: Bearer ${TOKEN}" | python -m json.tool

echo
echo "== MTX Toolkit health =="
curl -fsS "${TOOLKIT_API_URL}/api/health/" | python -m json.tool

echo
echo "== MTX Toolkit fleet nodes =="
curl -fsS -u "${TOOLKIT_USER}:${TOOLKIT_PASSWORD}" "${TOOLKIT_API_URL}/api/fleet/nodes?active_only=false" | python -m json.tool

echo
echo "Smoke check complete."
