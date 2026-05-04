![NVR Fleet](./nvr_fleet_architecture.png)

# NVR Fleet

> Self-hosted платформа управления распределёнными NVR-площадками за NAT — без VPN, без облака.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![MediaMTX](https://img.shields.io/badge/MediaMTX-v1.11.3-green)](https://github.com/bluenviron/mediamtx)
[![go2rtc](https://img.shields.io/badge/go2rtc-v1.9-green)](https://github.com/AlexxIT/go2rtc)

---

## Содержание

- [Что это](#что-это)
- [Архитектура](#архитектура)
- [Требования к серверу](#требования-к-серверу)
- [Установка зависимостей (VPS)](#установка-зависимостей-vps)
- [Быстрый старт](#быстрый-старт)
- [MTX Toolkit Fleet](#mtx-toolkit-fleet)
- [Установка агента на мини-ПК](#установка-агента-на-мини-пк)
- [Роли пользователей](#роли-пользователей)
- [Поддерживаемые NVR](#поддерживаемые-nvr)
- [MTX Toolkit](#mtx-toolkit)
- [Брендинг и локализация](#брендинг-и-локализация)
- [Производительность](#производительность)
- [Масштабирование](#масштабирование)
- [Ограничения](#ограничения)
- [Разработка](#разработка)

---

## Что это

Три задачи в одном стеке:

| Задача | Решение |
|--------|---------|
| Единый live-просмотр камер через VPS | go2rtc → MediaMTX → HLS/WebRTC |
| Просмотр архива напрямую с NVR | Archive RPC через WebSocket туннель |
| Подключение iVMS-4200 / SmartPSS без VPN | Reverse TCP tunnel на 3 порта |

---

## Архитектура

```
[NVR + камеры]
      │ RTSP (LAN)
[Мини-ПК: fleet-agent + go2rtc]
      │ WebSocket (TLS) + reverse TCP tunnel
[VPS: fleet-server + MediaMTX + Nginx]
      │ HTTPS / HLS / WebRTC
[Браузер / iVMS-4200 / SmartPSS]
```

**Ключевые принципы:**
- RTSP не идёт через WS-туннель — go2rtc пушит напрямую в MediaMTX
- WS-туннель используется только для control-plane и thick-client портов
- Нет VPN, нет проброса портов на клиентской стороне

---

## Требования к серверу

### VPS (сервер)

| Параметр | Минимум | Рекомендуется |
|----------|---------|---------------|
| ОС | Ubuntu 20.04+ / Debian 11+ | Ubuntu 22.04 LTS |
| CPU | 1 vCPU | 2 vCPU |
| RAM | 1 ГБ | 4 ГБ |
| Диск | 20 ГБ | 40 ГБ SSD |
| Сеть | 100 Мбит/с | 1 Гбит/с |
| Docker | 24.0+ | latest |
| Docker Compose | v2.20+ | latest |

**Открытые порты:**

| Порт | Протокол | Назначение |
|------|----------|------------|
| 80 | TCP | HTTP → редирект на HTTPS |
| 443 | TCP | HTTPS (Nginx, основной UI) |
| 8554 | TCP | RTSP (MediaMTX, приём от агентов) |
| 8888 | TCP | HLS (MediaMTX, опционально) |
| 3001 | TCP | MTX Toolkit UI (опционально, можно закрыть) |

### Мини-ПК на площадке

| Параметр | Минимум |
|----------|---------|
| ОС | Linux (Ubuntu 20.04+ / Armbian / OpenWrt + Python) |
| CPU | x86-64 или ARM64 |
| RAM | 512 МБ |
| Python | 3.10+ |
| Сеть | Доступ в интернет (исходящий TCP 443, 8554) |

---

## Установка зависимостей (VPS)

> Выполняется один раз на чистом сервере перед установкой NVR Fleet.

### 1. Обновить систему

```bash
apt update && apt upgrade -y
apt install -y curl git nano htop
```

### 2. Установить Docker

> ⚠️ Не используйте `apt install docker.io` или `snap install docker` — это устаревшие версии. Устанавливайте официальным скриптом:

```bash
curl -fsSL https://get.docker.com | sh
```

Добавить текущего пользователя в группу docker (чтобы не нужен был sudo):

```bash
usermod -aG docker $USER
newgrp docker   # применить без перелогина
```

Проверить:

```bash
docker --version          # должно быть 24.0+
docker compose version    # должно быть v2.20+
```

### 3. Включить автозапуск Docker

```bash
systemctl enable docker
systemctl start docker
```

### 4. Настроить домен (DNS)

Создайте A-запись у вашего DNS-провайдера:

```
nvr.yourdomain.com  →  IP_вашего_VPS
```

Проверить что DNS резолвится:

```bash
ping nvr.yourdomain.com
```

### 5. Убедиться что git установлен

```bash
git --version   # если нет: apt install -y git
```

---

## Быстрый старт

### Шаг 1. Клонировать репозиторий

```bash
git clone https://github.com/redlline/NVR-Fleet /opt/NVR-Fleet
cd /opt/NVR-Fleet
```

### Шаг 2. Настроить переменные окружения

```bash
cp .env.example .env
nano .env
```

Минимальный `.env` для запуска:

```env
# Публичный домен вашего VPS (без https://)
PUBLIC_HOST=nvr.yourdomain.com

# Токен администратора (используется как начальный пароль admin)
ADMIN_TOKEN=ваш_сложный_токен_здесь

# JWT секрет (любая случайная строка ≥32 символа)
JWT_SECRET=случайная_строка_минимум_32_символа

# Пароль viewer для RTSP/HLS (read-only доступ к стримам)
MEDIAMTX_VIEWER_PASS=viewer_password

# Пароль для внутреннего API MediaMTX
MEDIAMTX_INTERNAL_PASS=internal_api_password

# MTX Toolkit UI (порт 3001)
MTX_UI_USER=admin
MTX_UI_PASSWORD=toolkit_password
```

> **Генерация случайных паролей:** `openssl rand -hex 32`

### Шаг 3. Запустить установку

Один скрипт делает всё: проверяет Docker, поднимает основной стек, устанавливает и запускает MTX Toolkit, регистрирует ноду MediaMTX в Fleet, настраивает SSL.

```bash
bash scripts/setup_vps.sh
```

> Повторный запуск безопасен — скрипт идемпотентен.

### Шаг 4. Войти в панель

Откройте `https://nvr.yourdomain.com` в браузере.

- Логин: `admin`
- Пароль: значение `ADMIN_TOKEN` из `.env`

> После первого входа смените пароль через **System → Users → Edit**.

> **Нода MediaMTX** в MTX Toolkit Fleet регистрируется автоматически — fleet-server подключается к MTX Toolkit при старте и каждые несколько секунд синхронизирует стримы. Ручная регистрация через curl не нужна.

---

## MTX Toolkit Fleet

MTX Toolkit — дополнительный интерфейс управления MediaMTX потоками (порт `:3001`).

Запускается автоматически через `bash scripts/setup_vps.sh`. Нода MediaMTX регистрируется в Fleet без каких-либо ручных действий — fleet-server подключается к MTX Toolkit при старте и синхронизирует стримы каждые несколько секунд.

### Диагностика (если что-то не работает)

```bash
# Статус всех контейнеров Toolkit
docker compose -f docker-compose.mtx-toolkit.yml ps

# Здоровье backend
curl -s http://127.0.0.1:5002/api/health/ | python3 -m json.tool

# Список нод в Fleet
curl -s http://127.0.0.1:5002/api/fleet/nodes?active_only=false | python3 -m json.tool

# Логи backend
docker logs mtx-toolkit-backend --tail=30
```

> ⚠️ Если поднимать Toolkit вручную, все контейнеры должны стартовать **одной командой** — иначе они попадут в разные Docker-сети:
> ```bash
> docker compose -f docker-compose.mtx-toolkit.yml --profile build up -d
> ```

---

## Установка агента на мини-ПК

Выполняется на каждом мини-ПК на площадке.

### Требования на мини-ПК

```bash
# Ubuntu / Debian
apt update
apt install -y python3 python3-pip python3-venv curl git

# Проверить версию Python (нужно 3.10+)
python3 --version
```

### Автоматическая установка

```bash
curl -fsSL https://nvr.yourdomain.com/install.sh | bash
```

### Ручная установка

```bash
mkdir -p /opt/nvr-fleet-agent && cd /opt/nvr-fleet-agent

curl -fsSL https://nvr.yourdomain.com/agent/agent.py -o agent.py

python3 -m venv .venv
.venv/bin/pip install --quiet websockets pyyaml fastapi uvicorn

cat > /etc/nvr-fleet-agent.env << 'EOF'
SITE_ID=site001
AGENT_TOKEN=ваш_ADMIN_TOKEN_с_сервера
SERVER_HOST=nvr.yourdomain.com
SERVER_WS=wss://nvr.yourdomain.com/ws
SERVER_API=https://nvr.yourdomain.com/api
EOF

.venv/bin/python agent.py
```

### Автозапуск агента (systemd)

```bash
cat > /etc/systemd/system/nvr-fleet-agent.service << 'EOF'
[Unit]
Description=NVR Fleet Agent
After=network.target

[Service]
EnvironmentFile=/etc/nvr-fleet-agent.env
ExecStart=/opt/nvr-fleet-agent/.venv/bin/python /opt/nvr-fleet-agent/agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nvr-fleet-agent
systemctl start nvr-fleet-agent
```

---

## Роли пользователей

| Роль | Просмотр потоков | Архив | Управление площадками | Пользователи / Конфиг |
|------|-----------------|-------|----------------------|----------------------|
| **admin** | ✅ | ✅ | ✅ | ✅ |
| **operator** | ✅ | ✅ | ❌ | ❌ |
| **viewer** | ✅ | ❌ | ❌ | ❌ |

**Создание пользователей**: System → Users → Add user

При первом запуске автоматически создаётся пользователь `admin`. Начальный пароль = значение `ADMIN_TOKEN` из `.env`. Смените через UI после первого входа.

---

## Поддерживаемые NVR

| Вендор | Live RTSP | Архив | Thick client tunnel | Адаптер |
|--------|-----------|-------|---------------------|---------|
| Hikvision | ✅ | ✅ ISAPI | ✅ iVMS-4200 | `hikvision` |
| Dahua | ✅ | ✅ ONVIF | ✅ SmartPSS | `dahua` |
| UNV / Uniview | ✅ | ✅ ONVIF | ✅ EZStation | `unv` |
| ONVIF generic | ✅ | ✅ | ✅ | `onvif` |
| Другие | ✅ RTSP | ⚠️ ONVIF fallback | ✅ | автовыбор |

> **Важно**: автообнаружение устройств (WS-Discovery, UDP 3702) через туннель не работает.
> В iVMS-4200 / SmartPSS добавляйте устройства вручную по IP:PORT из панели.

---

## MTX Toolkit

Дополнительный интерфейс управления MediaMTX потоками на порту `:3001`.

**Защита паролем:**
```bash
docker exec mtx-toolkit-frontend sh -c \
  'apk add --no-cache apache2-utils && htpasswd -bc /etc/nginx/.htpasswd $MTX_UI_USER $MTX_UI_PASSWORD'
docker compose -f docker-compose.mtx-toolkit.yml restart mtx-toolkit-frontend
```

> `.htpasswd` хранится в памяти контейнера — пересоздаётся после `docker compose down`. Пароль берётся из переменных окружения.

---

## Брендинг и локализация

### Переименование панели

Редактируйте блок `BRAND` в `admin-ui/src/App.jsx`:

```javascript
const BRAND = {
  name:      "Ваше название",
  logoIcon:  "/logo.png",
  copyright: "© 2026 Ваша компания",
}
```

**Кастомный логотип**: поместите файл в `admin-ui/public/logo.png`.

### Языки интерфейса

Переключатель **EN / RU / TK** расположен внизу сайдбара.

Для добавления нового языка — добавьте объект в `translations` в `admin-ui/src/lib/i18n.js`.

---

## Производительность

> Замеры: VPS Hetzner CX22 (2 vCPU / 4 ГБ), мини-ПК Intel N100, H.264 1080p@25fps

### VPS: 10 площадок × 2 потока × 4 Мбит/с

| Компонент | CPU | RAM | Сеть |
|-----------|-----|-----|------|
| MediaMTX (20 путей) | ~3–5% | ~120 МБ | 80 Мбит/с |
| fleet-server | ~1–2% | ~80 МБ | — |
| Nginx | ~1% | ~30 МБ | — |
| **Итого** | **~5–8%** | **~230 МБ** | **80 Мбит/с** |

---

## Масштабирование

| Площадок | Потоков | VPS | Узкое место |
|----------|---------|-----|-------------|
| 10 | 20–40 | 2 vCPU / 4 ГБ / 200 Мбит | Сеть |
| 50 | 100–200 | 4 vCPU / 8 ГБ / 1 Гбит | MediaMTX |
| 100 | 200–400 | 8 vCPU / 16 ГБ / 1 Гбит | MediaMTX |
| 500 | 1000+ | Кластер (3+ VPS) | fleet-server |

**SQLite → PostgreSQL** (при >50 площадок):
```env
DATABASE_URL=postgresql://fleet:password@postgres:5432/fleet
```

---

## Ограничения

| Ограничение | Детали |
|-------------|--------|
| UDP-туннель | Не поддерживается. WS-Discovery (UDP 3702) не работает |
| RTSP шифрование | Порт 8554 — plaintext. HLS/WebRTC идут через Nginx (TLS) |
| SQLite | WAL mode. При >100 площадок переходите на PostgreSQL |
| Archive concurrent | Макс. 2 одновременных сессии архива на площадку |
| MediaMTX per instance | ~200–300 RTSP путей при 4 Мбит/с каждый |

---

## Разработка

```bash
# Backend
cd fleet-server
pip install -r requirements.txt
uvicorn main:app --reload --port 8765

# Frontend
cd admin-ui
npm install
npm run dev
# Windows ESM fallback: npx vite build
```

### Переменные окружения (.env)

| Переменная | Описание |
|-----------|----------|
| `ADMIN_TOKEN` | Токен агентов и начальный пароль admin (обязательно) |
| `JWT_SECRET` | Секрет подписи JWT (обязательно, иначе сессии сбрасываются при рестарте) |
| `MEDIAMTX_VIEWER_PASS` | Read-only пароль viewer для RTSP/HLS (обязательно) |
| `MEDIAMTX_INTERNAL_PASS` | Пароль внутреннего API MediaMTX порт 9997 (обязательно) |
| `PUBLIC_HOST` | Публичный домен VPS без https:// (обязательно) |
| `DATABASE_URL` | URL базы данных (по умолчанию SQLite) |
| `MTX_UI_USER` | Логин MTX Toolkit (по умолчанию `admin`) |
| `MTX_UI_PASSWORD` | Пароль MTX Toolkit (обязательно) |

---

*Проект активно развивается. Issues и PR приветствуются.*
