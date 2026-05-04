#!/usr/bin/env bash
# NVR Fleet — полная установка на VPS
# Использование: bash scripts/setup_vps.sh
# Повторный запуск безопасен (идемпотентен).
set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; CYA='\033[0;36m'; RST='\033[0m'
ok()   { echo -e "${GRN}[OK]${RST}  $*"; }
fail() { echo -e "${RED}[FAIL]${RST} $*"; exit 1; }
warn() { echo -e "${YEL}[WARN]${RST} $*"; }
info() { echo -e "${CYA}[INFO]${RST} $*"; }
step() { echo; echo -e "${CYA}══ $* ══${RST}"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo
echo "╔═══════════════════════════════════════════════╗"
echo "║         NVR Fleet — VPS Setup                 ║"
echo "╚═══════════════════════════════════════════════╝"
echo

# ── 1. Проверка системы ──────────────────────────────────────
step "Проверка системы"

# Docker
if ! command -v docker &>/dev/null; then
  info "Docker не найден. Устанавливаю..."
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker "$USER" 2>/dev/null || true
  systemctl enable docker
  systemctl start docker
  ok "Docker установлен: $(docker --version)"
else
  ok "Docker: $(docker --version)"
fi

# Docker Compose v2
if ! docker compose version &>/dev/null; then
  fail "Docker Compose v2 не найден. Установите Docker 24.0+ через: curl -fsSL https://get.docker.com | sh"
fi
ok "Docker Compose: $(docker compose version)"

# Git
if ! command -v git &>/dev/null; then
  info "Git не найден. Устанавливаю..."
  apt-get install -y git
fi
ok "Git: $(git --version)"

# ── 2. Проверка .env ─────────────────────────────────────────
step "Проверка конфигурации .env"

if [ ! -f "$ROOT_DIR/.env" ]; then
  if [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    warn ".env создан из .env.example"
    warn "Заполните обязательные поля и перезапустите скрипт:"
    warn "  nano $ROOT_DIR/.env"
    exit 1
  else
    fail ".env не найден и .env.example тоже отсутствует"
  fi
fi

set -a; source "$ROOT_DIR/.env"; set +a

MISSING=()
[ -z "${ADMIN_TOKEN:-}"            ] && MISSING+=("ADMIN_TOKEN")
[ -z "${JWT_SECRET:-}"             ] && MISSING+=("JWT_SECRET")
[ -z "${PUBLIC_HOST:-}"            ] && MISSING+=("PUBLIC_HOST")
[ -z "${MEDIAMTX_VIEWER_PASS:-}"   ] && MISSING+=("MEDIAMTX_VIEWER_PASS")
[ -z "${MEDIAMTX_INTERNAL_PASS:-}" ] && MISSING+=("MEDIAMTX_INTERNAL_PASS")
[ -z "${MTX_UI_PASSWORD:-}"        ] && MISSING+=("MTX_UI_PASSWORD")

if [ ${#MISSING[@]} -gt 0 ]; then
  fail "Незаполненные обязательные переменные в .env: ${MISSING[*]}"
fi

ok "PUBLIC_HOST = $PUBLIC_HOST"
ok "Все обязательные переменные заданы"

# ── 3. MTX Toolkit addon ─────────────────────────────────────
step "Подготовка MTX Toolkit addon"

ADDON_DIR="$ROOT_DIR/addons/mtx-toolkit"
if [ ! -d "$ADDON_DIR/.git" ]; then
  info "Клонирую MTX Toolkit addon..."
  bash "$ROOT_DIR/scripts/setup_mtx_toolkit.sh"
  ok "MTX Toolkit addon готов"
else
  info "MTX Toolkit addon уже существует. Обновляю..."
  bash "$ROOT_DIR/scripts/setup_mtx_toolkit.sh"
  ok "MTX Toolkit addon обновлён"
fi

# ── 4. Запуск основного стека ────────────────────────────────
step "Запуск основного стека (mediamtx, fleet-server, nginx, admin-ui)"

docker compose pull --quiet 2>/dev/null || true
docker compose up -d --build

# Ждём пока fleet-server поднимется
info "Жду fleet-server (до 30 сек)..."
for i in $(seq 1 30); do
  if docker exec fleet-server curl -sf http://localhost:8765/api/health &>/dev/null 2>&1; then
    ok "fleet-server готов (${i}s)"
    break
  fi
  sleep 1
done

ok "Основной стек запущен"

# ── 5. Запуск MTX Toolkit ────────────────────────────────────
step "Запуск MTX Toolkit (postgres, redis, backend, frontend)"

# Все 4 контейнера одной командой — иначе разные docker-сети
docker compose -f docker-compose.mtx-toolkit.yml --profile build up -d --build

# Ждём пока backend поднимется
info "Жду MTX Toolkit backend (до 60 сек)..."
MTX_READY=0
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:5002/api/health/ &>/dev/null 2>&1; then
    MTX_READY=1
    ok "MTX Toolkit backend готов (${i}s)"
    break
  fi
  sleep 1
done

if [ "$MTX_READY" -eq 0 ]; then
  warn "MTX Toolkit backend не ответил за 60 сек. Проверьте: docker logs mtx-toolkit-backend --tail=20"
  warn "Fleet-server зарегистрирует ноду автоматически когда backend поднимется."
fi

# ── 6. Перезапустить fleet-server чтобы он подключился к MTX ─
step "Синхронизация fleet-server с MTX Toolkit"

if [ "$MTX_READY" -eq 1 ]; then
  # fleet-server имеет sync loop — просто даём ему время
  info "Жду автоматической регистрации ноды (до 15 сек)..."
  sleep 10

  # Проверяем что нода появилась
  NODE_COUNT=$(curl -sf http://127.0.0.1:5002/api/fleet/nodes?active_only=false 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('nodes',[])))" 2>/dev/null || echo "0")

  if [ "$NODE_COUNT" -gt 0 ]; then
    ok "Нода MediaMTX автоматически зарегистрирована в Fleet!"
  else
    # Принудительная регистрация если sync loop не успел
    info "Sync loop не успел — регистрирую ноду вручную..."
    RTSP_PORT="${RTSP_PORT:-8554}"
    curl -sf -X POST http://127.0.0.1:5002/api/fleet/nodes \
      -H "Content-Type: application/json" \
      -d "{
        \"name\": \"MediaMTX ${PUBLIC_HOST}\",
        \"api_url\": \"http://mediamtx-internal:${MEDIAMTX_INTERNAL_PASS}@host.docker.internal:9997\",
        \"rtsp_url\": \"rtsp://viewer:${MEDIAMTX_VIEWER_PASS}@host.docker.internal:${RTSP_PORT}\",
        \"environment\": \"production\",
        \"is_active\": true
      }" &>/dev/null || true

    sleep 3
    curl -sf -X POST http://127.0.0.1:5002/api/fleet/nodes/1/sync \
      -H "Content-Type: application/json" -d "{}" &>/dev/null || true
    ok "Нода зарегистрирована принудительно"
  fi
fi

# ── 7. SSL ───────────────────────────────────────────────────
step "Настройка SSL"

if [ -f "$ROOT_DIR/scripts/setup_ssl.sh" ]; then
  if docker exec nvr-nginx test -f /etc/letsencrypt/live/"$PUBLIC_HOST"/fullchain.pem &>/dev/null 2>&1; then
    ok "SSL сертификат уже существует для $PUBLIC_HOST"
  else
    info "Получаю Let's Encrypt сертификат для $PUBLIC_HOST..."
    bash "$ROOT_DIR/scripts/setup_ssl.sh" || warn "SSL setup failed — запустите вручную: bash scripts/setup_ssl.sh"
  fi
else
  warn "scripts/setup_ssl.sh не найден — настройте SSL вручную"
fi

# ── 8. Итоговый статус ───────────────────────────────────────
step "Статус установки"

echo
printf "%-30s %-12s\n" "КОНТЕЙНЕР" "СТАТУС"
printf "%-30s %-12s\n" "──────────────────────────────" "──────────"

CONTAINERS=(mediamtx fleet-server nvr-nginx nvr-admin-ui
            mtx-toolkit-postgres mtx-toolkit-redis
            mtx-toolkit-backend mtx-toolkit-frontend
            mtx-toolkit-celery-worker mtx-toolkit-celery-beat)

ALL_OK=true
for c in "${CONTAINERS[@]}"; do
  STATE=$(docker inspect --format '{{.State.Status}}' "$c" 2>/dev/null || echo "missing")
  if [ "$STATE" = "running" ]; then
    printf "%-30s ${GRN}%-12s${RST}\n" "$c" "$STATE"
  else
    printf "%-30s ${RED}%-12s${RST}\n" "$c" "$STATE"
    ALL_OK=false
  fi
done

echo
echo "════════════════════════════════════════════"
if $ALL_OK; then
  ok "Установка завершена успешно!"
else
  warn "Некоторые контейнеры не запустились. Проверьте логи:"
  warn "  docker compose logs --tail=20"
  warn "  docker compose -f docker-compose.mtx-toolkit.yml logs --tail=20"
fi

echo
echo "  Панель управления:  https://${PUBLIC_HOST}"
echo "  MTX Toolkit:        https://${PUBLIC_HOST}:3001"
echo "  Логин admin:        ${ADMIN_TOKEN}"
echo
echo "  Просмотр логов:"
echo "  docker compose logs -f fleet-server"
echo "  docker compose -f docker-compose.mtx-toolkit.yml logs -f mtx-toolkit-backend"
echo "════════════════════════════════════════════"
