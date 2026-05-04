#!/usr/bin/env bash
# NVR-Fleet smoke check — post-deploy verification
# Usage:
#   BASE_URL="https://nvr.yourdomain.com" \
#   USERNAME="admin" PASSWORD="yourpass" \
#   TOOLKIT_USER="admin" TOOLKIT_PASSWORD="yourpass" \
#   ./scripts/smoke-check.sh

set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8765}"
USERNAME="${USERNAME:-admin}"
PASSWORD="${PASSWORD:-}"
TOOLKIT_USER="${TOOLKIT_USER:-${MTX_UI_USER:-admin}}"
TOOLKIT_PASSWORD="${TOOLKIT_PASSWORD:-${MTX_UI_PASSWORD:-}}"
TOOLKIT_API_URL="${TOOLKIT_API_URL:-http://127.0.0.1:5002}"

RED="\033[0;31m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; NC="\033[0m"
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; }
info() { echo -e "${YELLOW}[INFO]${NC} $*"; }

parse_json() {
  if command -v jq &>/dev/null; then
    jq .
  elif command -v python3 &>/dev/null; then
    python3 -m json.tool
  else
    cat
  fi
}

json_field() {
  local json="$1" field="$2"
  if command -v jq &>/dev/null; then
    echo "$json" | jq -r ".$field // empty"
  elif command -v python3 &>/dev/null; then
    echo "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get(\"$field\", \"\"))"
  fi
}

if [[ -z "$PASSWORD" ]]; then
  fail "PASSWORD env var is required"; exit 1
fi

echo "======================================"
echo " NVR-Fleet Smoke Check"
echo " BASE_URL: $BASE_URL"
echo "======================================"
echo ""

# 1. Login
info "== Login: POST /api/auth/login =="
RESP=$(curl -sS --max-time 10 -X POST "${BASE_URL}/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\"}" \
  -w "\nHTTP_STATUS:%{http_code}" 2>&1)

HTTP_STATUS=$(echo "$RESP" | grep "HTTP_STATUS:" | cut -d: -f2)
BODY=$(echo "$RESP" | grep -v "HTTP_STATUS:")

if [[ "$HTTP_STATUS" == "200" ]]; then
  TOKEN=$(json_field "$BODY" "token")
  ROLE=$(json_field "$BODY" "role")
  ok "Login successful — user: ${USERNAME}, role: ${ROLE}"
else
  fail "Login failed (HTTP $HTTP_STATUS)"
  echo "$BODY"
  echo ""
  info "Hint: check that fleet-server is running and BASE_URL is correct"
  info "Try: docker compose logs fleet-server --tail=20"
  exit 1
fi

echo ""

# 2. auth/me
info "== GET /api/auth/me =="
RESP=$(curl -sS --max-time 10 "${BASE_URL}/api/auth/me" \
  -H "Authorization: Bearer ${TOKEN}" \
  -w "\nHTTP_STATUS:%{http_code}")
HTTP_STATUS=$(echo "$RESP" | grep "HTTP_STATUS:" | cut -d: -f2)
BODY=$(echo "$RESP" | grep -v "HTTP_STATUS:")
if [[ "$HTTP_STATUS" == "200" ]]; then
  ok "auth/me"
  echo "$BODY" | parse_json
else
  fail "auth/me HTTP $HTTP_STATUS: $BODY"
fi

echo ""

# 3. system/stack
info "== GET /api/system/stack =="
RESP=$(curl -sS --max-time 10 "${BASE_URL}/api/system/stack" \
  -H "Authorization: Bearer ${TOKEN}" \
  -w "\nHTTP_STATUS:%{http_code}")
HTTP_STATUS=$(echo "$RESP" | grep "HTTP_STATUS:" | cut -d: -f2)
BODY=$(echo "$RESP" | grep -v "HTTP_STATUS:")
if [[ "$HTTP_STATUS" == "200" ]]; then
  ok "system/stack"
  echo "$BODY" | parse_json
else
  fail "system/stack HTTP $HTTP_STATUS"
fi

echo ""

# 4. MTX Toolkit health
if [[ -n "$TOOLKIT_API_URL" ]]; then
  info "== MTX Toolkit: GET /api/health/ =="
  RESP=$(curl -sS --max-time 5 "${TOOLKIT_API_URL}/api/health/" \
    -w "\nHTTP_STATUS:%{http_code}" 2>/dev/null)
  HTTP_STATUS=$(echo "$RESP" | grep "HTTP_STATUS:" | cut -d: -f2)
  BODY=$(echo "$RESP" | grep -v "HTTP_STATUS:")
  if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "MTX Toolkit health"
    echo "$BODY" | parse_json
  else
    fail "MTX Toolkit health HTTP $HTTP_STATUS (is mtx-toolkit-backend running?)"
  fi

  echo ""

  # 5. MTX Toolkit fleet nodes
  info "== MTX Toolkit: GET /api/fleet/nodes =="
  if [[ -z "$TOOLKIT_PASSWORD" ]]; then
    info "TOOLKIT_PASSWORD not set — skipping fleet nodes check"
  else
    RESP=$(curl -sS --max-time 5 \
      -u "${TOOLKIT_USER}:${TOOLKIT_PASSWORD}" \
      "${TOOLKIT_API_URL}/api/fleet/nodes?active_only=false" \
      -w "\nHTTP_STATUS:%{http_code}" 2>/dev/null)
    HTTP_STATUS=$(echo "$RESP" | grep "HTTP_STATUS:" | cut -d: -f2)
    BODY=$(echo "$RESP" | grep -v "HTTP_STATUS:")
    if [[ "$HTTP_STATUS" == "200" ]]; then
      NODE_COUNT=$(json_field "$BODY" "total_nodes" 2>/dev/null || echo "?")
      ok "MTX Toolkit fleet: total_nodes=${NODE_COUNT}"
      echo "$BODY" | parse_json
    else
      fail "MTX Toolkit fleet nodes HTTP $HTTP_STATUS"
      echo "$BODY"
    fi
  fi
fi

echo ""
echo "======================================"
echo " Smoke check complete"
echo "======================================"
