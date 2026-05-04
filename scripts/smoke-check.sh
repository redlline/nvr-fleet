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
# Parse token: try jq first, fall back to python3, then python
if command -v jq &>/dev/null; then
  TOKEN="$(echo "$LOGIN_JSON" | jq -r '.token')"
elif command -v python3 &>/dev/null; then
  TOKEN="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])' <<<"$LOGIN_JSON")"
else
  TOKEN="$(python -c 'import json,sys; print(json.load(sys.stdin)["token"])' <<<"$LOGIN_JSON")"
fi
echo "login ok for ${USERNAME}"

echo
echo "== /api/auth/me =="
curl -fsS "${BASE_URL}/api/auth/me" \
  -H "Authorization: Bearer ${TOKEN}" | (command -v jq &>/dev/null && jq . || python3 -m json.tool 2>/dev/null || cat)

echo
echo "== /api/system/stack =="
curl -fsS "${BASE_URL}/api/system/stack" \
  -H "Authorization: Bearer ${TOKEN}" | (command -v jq &>/dev/null && jq . || python3 -m json.tool 2>/dev/null || cat)

echo
echo "== MTX Toolkit health =="
curl -fsS "${TOOLKIT_API_URL}/api/health/" | (command -v jq &>/dev/null && jq . || python3 -m json.tool 2>/dev/null || cat)

echo
echo "== MTX Toolkit fleet nodes =="
curl -fsS -u "${TOOLKIT_USER}:${TOOLKIT_PASSWORD}" "${TOOLKIT_API_URL}/api/fleet/nodes?active_only=false" | (command -v jq &>/dev/null && jq . || python3 -m json.tool 2>/dev/null || cat)

echo
echo "Smoke check complete."
