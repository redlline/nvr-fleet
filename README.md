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
- [Быстрый старт](#быстрый-старт)
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

## Быстрый старт

### Требования

- VPS: Ubuntu 20.04+, Docker, Docker Compose v2
- Мини-ПК на площадке: Linux, Python 3.10+

### Установка на VPS

```bash
git clone https://github.com/redlline/NVR-Fleet
cd NVR-Fleet
cp .env.example .env
# Отредактировать .env: ADMIN_TOKEN, PUBLIC_HOST, пароли
nano .env

docker compose up -d
```

### Установка агента на мини-ПК

**Рекомендуемый способ — через UI панели:**

1. Создать площадку в **Sites → Add site**
2. Открыть площадку → кнопка **Deploy config**
3. Панель покажет готовую команду установки вида:
   ```
   curl -s https://nvr.yourserver.com/install.sh | bash -s -- \
     --site <SITE_ID> --token <AGENT_TOKEN> --server nvr.yourserver.com
   ```
4. Выполнить эту команду на мини-ПК — скрипт установит все зависимости,
   создаст systemd-сервисы и запустит агента автоматически.

> `install.sh` доступен по адресу `https://nvr.yourserver.com/install.sh` —
> он обслуживается самим fleet-server, адрес генерируется панелью автоматически.

**Ручная установка (если UI недоступен):**

```bash
curl -s https://nvr.yourserver.com/install.sh | bash -s -- \
  --site <SITE_ID> --token <AGENT_TOKEN> --server nvr.yourserver.com
```

После установки агент запускается как systemd-сервис `nvr-fleet-agent`
и автоматически переподключается при потере сети.


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
# Установить логин/пароль в .env
MTX_UI_USER=admin
MTX_UI_PASSWORD=yourpassword

# Создать .htpasswd и перезапустить
docker exec mtx-toolkit-frontend sh -c \
  'apk add --no-cache apache2-utils && htpasswd -bc /etc/nginx/.htpasswd $MTX_UI_USER $MTX_UI_PASSWORD'
docker compose -f docker-compose.mtx-toolkit.yml restart mtx-toolkit-frontend
```


> `.htpasswd` хранится в памяти контейнера — пересоздаётся после `docker compose down`. Пароль берётся из переменных окружения. Автоматическое создание через `entrypoint.sh` монтируется через volume.
---

### Языки интерфейса

Переключатель **EN / RU / TK** расположен внизу сайдбара.

Для добавления нового языка — добавьте объект в `translations` в `admin-ui/src/lib/i18n.js`:

```javascript
de: {
  dashboard: "Dashboard",
  sites: "Standorte",
  // ...
}
```

---

## Производительность

> Замеры: VPS Hetzner CX22 (2 vCPU / 4 ГБ), мини-ПК Intel N100, H.264 1080p@25fps

### Один туннель

| Транспорт | Битрейт | CPU агент | CPU VPS | Задержка |
|-----------|---------|-----------|---------|----------|
| WS over TLS | 4 Мбит/с | ~1.2% | ~0.8% | +2–5 мс |
| WS over TLS | 8 Мбит/с | ~2.1% | ~1.4% | +2–5 мс |
| Прямой RTSP | 4 Мбит/с | ~0.3% | — | baseline |

### Агент: 4 потока × 4 Мбит/с

| Компонент | CPU |
|-----------|-----|
| go2rtc (RTSP ingest) | ~4–6% |
| fleet-agent (WS + stats) | ~1.5–2% |
| **Итого** | **~6–8%** |

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
SQLAlchemy-модели совместимы без изменений кода.

**Шардирование MediaMTX**: fleet-server хранит `mtx_endpoint` per site. При >100 площадок запустите несколько инстансов MediaMTX и назначайте площадки вручную через Edit Site.

---

## Ограничения

| Ограничение | Детали |
|-------------|--------|
| UDP-туннель | Не поддерживается. Только TCP. WS-Discovery (UDP 3702) не работает |
| RTSP шифрование | Порт 8554 — plaintext. HLS/WebRTC идут через Nginx (TLS) |
| SQLite | WAL mode. При >100 площадок возможны блокировки — переходите на PostgreSQL |
| Archive concurrent | Макс. 2 одновременных сессии архива на площадку (лимит NVR ISAPI/ONVIF) |
| MediaMTX per instance | ~200–300 RTSP путей при 4 Мбит/с каждый |

---

### Переменные окружения (.env)

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `ADMIN_TOKEN` | (required, no default) | Токен аутентификации агентов. Если не задан — эфемерный с WARNING. |
| `JWT_SECRET` | (required, no default) | Независимый секрет подписи JWT. Если не задан — эфемерный: сессии сбрасываются при рестарте. |
| `MEDIAMTX_VIEWER_PASS` | (required) | Read-only пароль viewer для RTSP/HLS в MediaMTX. |
| `PUBLIC_HOST` | `localhost` | Публичный домен VPS |
| `DATABASE_URL` | SQLite | URL базы данных |
| `MTX_UI_USER` | `admin` | Логин MTX Toolkit |
| `MTX_UI_PASSWORD` | (required, no default) | Пароль MTX Toolkit (nginx basic auth на порту 3001) |

---

*Проект активно развивается. Issues и PR приветствуются.*






