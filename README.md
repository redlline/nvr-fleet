<img width="3438" height="2438" alt="nvr_fleet_architecture" src="https://github.com/user-attachments/assets/78aa8d11-20cb-452a-8fc2-d10ec141c4cc" />

# NVR Fleet

Self-hosted платформа для управления несколькими площадками с NVR за NAT.

Проект решает три задачи:

1. единый live-просмотр камер через VPS
2. просмотр архива напрямую с самих NVR
3. подключение толстых клиентов вроде `iVMS-4200` без VPN

## Как устроено

```text
[NVR]
  -> локальный LAN
[Мини-ПК: fleet-agent + go2rtc + local admin]
  -> WebSocket / reverse tunnel
[VPS: fleet-server + MediaMTX]
  -> Admin UI / HLS / WebRTC / RTSP / thick clients
```

## Компоненты

- `fleet-server/`
  FastAPI backend, SQLite, CRUD площадок и камер, WebSocket-хаб для агентов, archive RPC и reverse TCP tunnel orchestration.
- `fleet-agent/`
  Агент на мини-ПК рядом с NVR. Управляет live-потоками, архивом, reverse tunnel и локальной панелью.
- `admin-ui/`
  React/Vite интерфейс для площадок, камер, live, архива, tunnel-портов и system actions.
- `mediamtx/`
  Центральный RTSP/HLS/WebRTC broker для live и временных archive playback streams.
- `nginx/`
  Reverse proxy для UI, API, installer и media endpoint'ов.
- `scripts/install.sh`
  Installer агента на площадке.

## Live-потоки

Live работает так:

1. создаётся площадка в Admin UI
2. сервер генерирует install-команду и токен агента
3. агент ставится на мини-ПК в LAN рядом с NVR
4. агент получает конфиг, локально читает RTSP с NVR и публикует наружу на VPS
5. операторы смотрят камеры через WebRTC, HLS или RTSP

## Архив с NVR

Архив не хранится на VPS как центральная запись.

Логика такая:

1. оператор открывает вкладку `Archive`
2. backend отправляет запрос агенту нужной площадки
3. агент запрашивает список записей у самого NVR
4. при старте playback агент поднимает временный поток из архива NVR
5. этот поток публикуется в `MediaMTX`
6. UI воспроизводит его через HLS/WebRTC

Такой подход экономит место на сервере и подходит для площадок за NAT.

### Слой адаптеров

Адаптеры живут в агенте, потому что именно агент находится в LAN с NVR.

Сейчас:

- `hikvision` - реализован
- `dahua` - реализован через ONVIF media/recording/replay
- `onvif` - реализован через ONVIF media/recording/replay

## Толстые клиенты без VPN

Для `iVMS-4200` и похожих программ проект использует отдельный контур reverse TCP tunnel через сервер.

### Как это работает

1. толстый клиент подключается к публичному IP сервера
2. сервер принимает соединение на выделенном per-site порту
3. этот TCP-трафик передаётся через уже существующий канал `agent -> server`
4. агент открывает локальное TCP-соединение до NVR

Это не VPN и не эмуляция NVR на VPS. Это транспорт до реального NVR через агент.

### Порты по умолчанию

- `HTTP tunnel`: `20080-20179`
- `RTSP tunnel`: `25554-25653`
- `Control tunnel`: `28000-28099`

Для каждой площадки сервер автоматически выделяет:

- `tunnel_http_port`
- `tunnel_rtsp_port`
- `tunnel_control_port`

Их видно:

- в карточке площадки в Admin UI
- в локальной панели агента на мини-ПК

### Для Hikvision / iVMS-4200

Используйте:

- host: публичный адрес сервера
- HTTP port: `tunnel_http_port`
- server/control port: `tunnel_control_port`
- RTSP port: `tunnel_rtsp_port`

## Локальная панель на мини-ПК

Агент поднимает лёгкую панель на площадке:

```text
http://MINI_PC_IP:7070
```

Через неё можно:

- посмотреть текущую конфигурацию камер
- сделать autodiscover каналов с NVR
- найти ONVIF-устройства в локальной сети через WS-Discovery
- добавить новые каналы
- удалить каналы
- изменить имя, номер канала, stream type и enabled
- сохранить изменения сразу в центральную конфигурацию

После сохранения сервер автоматически делает deploy в агент.

### Какие протоколы использует autodiscovery

Здесь есть два разных режима:

1. `Autodiscover` каналов уже настроенного NVR:
   - `hikvision` -> `ISAPI` (`/ISAPI/Streaming/channels` и совместимые endpoints)
   - `dahua` -> `ONVIF Media`
   - `onvif` -> `ONVIF Media`

2. `Find NVRs in LAN`:
   - `ONVIF WS-Discovery` multicast probe в локальной сети
   - этот режим находит ONVIF-совместимые устройства, включая Hikvision и Dahua, если у них включён ONVIF

То есть это не клон `SADP` и не реализация проприетарного discovery-пакета Dahua. Для LAN-поиска проект сейчас использует стандартный ONVIF discovery, а для чтения каналов и архива - vendor API или ONVIF уже после того, как устройство найдено/настроено.

## Создание площадки

При создании площадки указываются:

- `Archive adapter`
- `NVR IP`
- `NVR API port`
- `NVR control port`
- `NVR RTSP port`
- учётные данные NVR
- количество каналов
- `main/sub` как default stream type

## Установка агента

После создания площадки сервер покажет команду вида:

```bash
curl -fsSL http://cams.yourdomain.com/install.sh | bash -s -- \
  --site SITE_ID \
  --token TOKEN \
  --server cams.yourdomain.com \
  --scheme http
```

Installer:

- ставит `go2rtc`
- ставит Python-зависимости агента
- скачивает `agent.py`
- создаёт systemd unit
- поднимает локальную панель на `7070`

## Быстрый старт на VPS

### 1. Подготовка

```bash
apt install -y docker.io docker-compose-plugin
ufw allow 443/tcp 80/tcp 8554/tcp 8889/tcp
ufw allow 20080:20179/tcp
ufw allow 25554:25653/tcp
ufw allow 28000:28099/tcp
```

### 2. Клонирование и настройка

```bash
git clone https://github.com/redlline/NVR-Fleet /opt/nvr-fleet
cd /opt/nvr-fleet
cp .env.example .env
nano .env
```

Минимум:

- `ADMIN_TOKEN`
- `PUBLIC_HOST`

### 3. TLS без SSH

```bash
mkdir -p nginx/certs
# Если сертификат уже получен в ZeroSSL:
cp /path/to/fullchain.pem nginx/certs/fullchain.pem
cp /path/to/privkey.pem nginx/certs/privkey.pem
```

Если файлов ещё нет, это тоже нормально:

- проект стартует в `HTTP-only` режиме
- ты заходишь в web UI по `http://YOUR_DOMAIN`
- открываешь `System`
- загружаешь `fullchain.pem` и `privkey.pem`
- встроенный `nginx` сам переключается на `HTTPS` без SSH

### 4. Запуск

```bash
docker compose up -d --build
docker compose ps
```

### 5. Открытие интерфейса

```text
http://cams.yourdomain.com
```

После загрузки сертификата через `System` интерфейс автоматически станет доступен и по:

```text
https://cams.yourdomain.com
```

## System actions

Во вкладке `System` доступны:

- загрузка, замена и удаление TLS-сертификатов
- статус контейнеров и базовые health probes
- restart всего стека или отдельных сервисов
- export backup
- import backup с redeploy конфигурации

Это позволяет после первичного запуска дальше обслуживать проект без постоянных заходов по SSH.

## Доступ к просмотру

Live:

- Admin UI
- RTSP
- HLS
- WebRTC

Archive:

- Admin UI через временный playback stream

Толстые клиенты:

- `iVMS-4200`
- похожие клиенты, которым нужен TCP-доступ до реального NVR через сервер

## Локальные проверки

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
└── .env.example
```
