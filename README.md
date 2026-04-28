![NVR Fleet architecture](./nvr_fleet_architecture.png)

# NVR Fleet

Self-hosted платформа для управления несколькими площадками с NVR за NAT.

Проект решает три основные задачи:

1. Единый live-просмотр камер через VPS.
2. Просмотр архива напрямую с самих NVR.
3. Подключение толстых клиентов вроде `iVMS-4200` без VPN через reverse TCP tunnel.

## Как устроено

```text
[NVR]
  -> локальный LAN
[Мини-ПК: fleet-agent + go2rtc + local admin]
  -> WebSocket / reverse tunnel
[VPS: fleet-server + MediaMTX + nginx]
  -> Admin UI / HLS / WebRTC / RTSP / thick clients
```

## Компоненты

- `fleet-server/`  
  FastAPI backend, SQLite, CRUD площадок и камер, WebSocket-хаб для агентов, archive RPC, tunnel orchestration, TLS/system actions.
- `fleet-agent/`  
  Агент на мини-ПК рядом с NVR. Управляет live-потоками, архивом, reverse tunnel и локальной панелью.
- `admin-ui/`  
  React/Vite интерфейс для площадок, камер, live, архива, туннелей и системных действий.
- `mediamtx/`  
  Центральный RTSP/HLS/WebRTC broker для live и временных playback streams из архива.
- `nginx/`  
  Reverse proxy для UI, API, installer и media endpoint-ов.
- `docker-compose.mtx-toolkit.yml`  
  Необязательный отдельный add-on для `MTX Toolkit`, если нужен дополнительный dashboard именно для MediaMTX.

## Возможности

### Live

- live-просмотр через браузер
- HLS / WebRTC / RTSP
- централизованный status/traffic по площадкам

### Архив

- список записей тянется с NVR, а не с VPS
- поиск по площадке, камере и интервалу времени
- playback публикуется как временный поток через `MediaMTX`

### Толстые клиенты

- per-site HTTP / RTSP / control tunnel ports
- работа через один публичный сервер
- без VPN

### Локальная панель на мини-ПК

Адрес:

```text
http://MINI_PC_IP:7070
```

Через неё можно:

- посмотреть текущую конфигурацию камер
- сделать autodiscover каналов с NVR
- найти ONVIF-устройства в локальной сети через WS-Discovery
- добавлять, удалять и редактировать каналы
- сохранять изменения сразу в центральную конфигурацию

## Адаптеры архива и discovery

Сейчас поддерживаются:

- `hikvision` — vendor API / ISAPI
- `dahua` — через ONVIF media/recording/replay
- `onvif` — через ONVIF media/recording/replay

Autodiscovery разделён на два режима:

1. `Autodiscover channels` для уже настроенного NVR:
   - `hikvision` -> `ISAPI`
   - `dahua` -> `ONVIF Media`
   - `onvif` -> `ONVIF Media`
2. `Find NVRs in LAN`:
   - `ONVIF WS-Discovery`

## Быстрый старт на VPS

### 1. Подготовка

```bash
apt install -y docker.io docker-compose-plugin
ufw allow 80/tcp 443/tcp 8554/tcp 8889/tcp
ufw allow 20080:20179/tcp
ufw allow 25554:25653/tcp
ufw allow 28000:28099/tcp
```

### 2. Клонирование и `.env`

```bash
git clone https://github.com/redlline/NVR-Fleet /opt/nvr-fleet
cd /opt/nvr-fleet
cp .env.example .env
```

Минимум, что нужно заполнить:

- `ADMIN_TOKEN`
- `PUBLIC_HOST`

### 3. TLS без SSH

Если сертификаты уже получены:

```bash
mkdir -p nginx/certs
cp /path/to/fullchain.pem nginx/certs/fullchain.pem
cp /path/to/privkey.pem nginx/certs/privkey.pem
```

Если сертификатов пока нет, это тоже нормально:

- проект стартует в `HTTP-only`
- сертификаты можно загрузить позже через вкладку `System`

### 4. Запуск ядра

```bash
docker compose up -d --build
docker compose ps
```

После этого интерфейс будет доступен по адресу:

```text
http://YOUR_DOMAIN
```

После загрузки сертификатов через `System`:

```text
https://YOUR_DOMAIN
```

## Установка агента на площадке

После создания площадки сервер покажет install-команду вида:

```bash
curl -fsSL https://cams.yourdomain.com/install.sh | bash -s -- \
  --site SITE_ID \
  --token TOKEN \
  --server cams.yourdomain.com \
  --scheme https
```

Installer:

- ставит `go2rtc`
- ставит Python-зависимости агента
- скачивает `agent.py`
- создаёт `systemd` unit
- поднимает локальную панель на `7070`

## System UI

Во вкладке `System` доступны:

- загрузка, замена и удаление TLS-сертификатов
- статус контейнеров и health probes
- restart всего стека и отдельных сервисов
- просмотр логов сервисов
- export / import backup
- one-click rotate backup на сервере

В карточке площадки доступны:

- `Deploy config`
- `Restart agent`
- `Drain + redeploy`

## MTX Toolkit как отдельный add-on

`MTX Toolkit` не входит в обязательный compose ядра.

Подготовка исходников:

```bash
bash scripts/setup_mtx_toolkit.sh
```

Запуск:

```bash
docker compose -f docker-compose.mtx-toolkit.yml up -d --build
```

После этого будут доступны:

- UI: `http://YOUR_SERVER:3001`
- API: `http://YOUR_SERVER:5002`

## Проверки

Backend и agent:

```bash
python -m py_compile fleet-server/main.py fleet-agent/agent.py
python -m unittest discover -s fleet-server/tests -p "test_*.py"
```

Frontend:

```bash
cd admin-ui
npm install
npm run build
```

## Структура проекта

```text
nvr-fleet/
├── admin-ui/
├── fleet-agent/
├── fleet-server/
│   └── tests/
├── mediamtx/
├── nginx/
├── scripts/
├── docker-compose.yml
├── docker-compose.mtx-toolkit.yml
└── .env.example
```
