# RUNBOOK

## Purpose

This runbook covers the NVR Fleet control plane, the main Docker stack, and the optional MTX Toolkit add-on. Use it for deploys, post-deploy smoke checks, and first-response troubleshooting.

## Pre-deploy checklist

Required environment variables in `.env`:

- `ADMIN_TOKEN`
- `JWT_SECRET`
- `PUBLIC_HOST`
- `PUBLIC_SCHEME`
- `MEDIAMTX_INTERNAL_PASS`
- `MEDIAMTX_VIEWER_PASS`
- `MTX_UI_USER`
- `MTX_UI_PASSWORD`

Critical note:

- `fleet-server` now fails at startup if `JWT_SECRET`, `MEDIAMTX_INTERNAL_PASS`, or `MEDIAMTX_VIEWER_PASS` are missing.
- `mtx-toolkit-frontend` now fails at startup if `/etc/nginx/.htpasswd` cannot be created.

## Deploy or update the main stack

```bash
cd /opt/nvr-fleet
git fetch origin
git checkout main
git pull --ff-only origin main
docker compose build --no-cache nginx fleet-server admin-ui
docker compose up -d --force-recreate nginx fleet-server admin-ui mediamtx
```

## Deploy or update MTX Toolkit

Source tree requirement:

```bash
cd /opt/nvr-fleet
test -d addons/mtx-toolkit || git clone https://github.com/Dan-Calvert/mtx-toolkit addons/mtx-toolkit
docker compose -f docker-compose.mtx-toolkit.yml --profile build up -d --build
```

## Post-deploy smoke check

Main checks:

- `/api/auth/me`
- `/api/system/stack`
- `http://127.0.0.1:5002/api/health/`
- `http://127.0.0.1:5002/api/fleet/nodes?active_only=false`

Reusable command:

```bash
cd /opt/nvr-fleet
chmod +x scripts/smoke-check.sh
BASE_URL="http://127.0.0.1" \
USERNAME="admin" \
PASSWORD="YOUR_ADMIN_PASSWORD" \
TOOLKIT_USER="admin" \
TOOLKIT_PASSWORD="YOUR_MTX_UI_PASSWORD" \
./scripts/smoke-check.sh
```

Expected outcomes:

- `/api/auth/me` returns the authenticated user and `role: "admin"` for the admin login.
- `/api/system/stack` shows container health and integration health separately.
- `MTX Toolkit API` integration is `ok`.
- `MTX Fleet sync` is `ok` or at worst `degraded`, not `failing`.
- `api/fleet/nodes` contains the current MediaMTX node.

## Routine diagnostics

### Main stack

```bash
docker compose ps
docker compose logs --tail=200 fleet-server
docker compose logs --tail=200 admin-ui
docker compose logs --tail=200 nginx
docker compose logs --tail=200 mediamtx
```

### MTX Toolkit

```bash
docker compose -f docker-compose.mtx-toolkit.yml --profile build ps
docker logs mtx-toolkit-backend --tail=200
docker logs mtx-toolkit-frontend --tail=200
curl -i http://127.0.0.1:5002/api/health/
curl -i -u admin:YOUR_MTX_UI_PASSWORD http://127.0.0.1:3001/
```

## Failure patterns

### `fleet-server` does not start

Likely cause:

- missing `JWT_SECRET`
- missing `MEDIAMTX_INTERNAL_PASS`
- missing `MEDIAMTX_VIEWER_PASS`

Check:

```bash
docker compose logs --tail=100 fleet-server
```

### MTX Toolkit UI returns `403`

Likely cause:

- `.htpasswd` was not created inside `mtx-toolkit-frontend`

Check:

```bash
docker logs mtx-toolkit-frontend --tail=100
docker exec -it mtx-toolkit-frontend sh -lc 'ls -l /etc/nginx/.htpasswd'
```

Expected:

- startup log includes `Created .htpasswd`
- `/etc/nginx/.htpasswd` exists and is non-empty

### System page is green but Fleet is empty

Meaning:

- container health is fine, but MTX Toolkit registration or sync is failing

Check:

```bash
curl -s http://127.0.0.1:5002/api/fleet/nodes?active_only=false | python -m json.tool
docker logs fleet-server --tail=200 | grep -Ei 'MTX Toolkit|node bootstrap|node creation|sync-all|sync failed'
```

### Admin buttons are missing in the main UI

Likely causes:

- stale browser token
- logged in with a non-admin account
- frontend container not rebuilt after role/UI changes

Check:

```bash
curl -s -X POST http://127.0.0.1/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_ADMIN_PASSWORD"}'
curl -s http://127.0.0.1/api/auth/me -H "Authorization: Bearer YOUR_TOKEN"
```

Expected:

- `role` must be `admin`

## Operational notes

- Prefer `git pull --ff-only origin main` on servers to avoid accidental merge commits during recovery.
- Keep MTX Toolkit validation separate from core NVR Fleet validation. The add-on is an extra stack with its own runtime and auth layer.
- Treat `System` container health and integration health as different signals. Green containers do not guarantee successful MTX Fleet sync.
