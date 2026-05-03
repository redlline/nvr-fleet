# NVR-Fleet — Operations Guide

This document covers known operational risks, deployment requirements,
and mitigation guidance for running NVR-Fleet in production.

---

## Known Architecture Risks

### 1. docker.sock mounted in fleet-server container

**Location:** `docker-compose.yml` — `fleet-server` service

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

**Why it exists:** fleet-server needs to restart Docker services (MediaMTX,
nginx) via the System → Stack UI page without SSH access to the host.

**Risk:** If fleet-server is ever compromised via a vulnerability (e.g. RCE
through backup import or archive playback), the attacker gains full Docker
daemon access, which is equivalent to root on the host.

**Mitigations applied:**
- fleet-server is not exposed directly to the internet (nginx proxy in front)
- backup import has 50 MB size limit and zip traversal protection
- archive endpoints require admin role
- JWT authentication on all state-mutating endpoints

**Recommendation for high-security deployments:**
Remove the `docker.sock` volume and disable stack restart from UI. Use a
separate process with limited Docker API access (e.g. `docker-socket-proxy`
with `ALLOW_START=1` and `ALLOW_STOP=1` only for specific containers).

---

### 2. mediamtx runs in host network mode

**Location:** `docker-compose.yml` — `mediamtx` service

```yaml
network_mode: host
```

**Why it exists:** MediaMTX needs to bind RTSP (8554), HLS (8888), WebRTC
(8889) ports. With host networking these ports are accessible on the server's
primary interface without explicit port mapping.

**Risk:** MediaMTX API (port 9997) and metrics (port 9998) are bound to all
interfaces. The API is protected by `MEDIAMTX_API_PASS`, but if that variable
is unset the fallback generates a random value (warning logged).

**Mitigations applied:**
- `authMethod: internal` — all read/write/publish requires valid credentials
- anonymous `user: any` removed from MediaMTX config
- MEDIAMTX_API_PASS warning logged at startup if unset

**Recommendation:**
- Add firewall rules blocking ports 9997 and 9998 from external access
- Consider `network_mode: bridge` with explicit port mapping if you can
  accept the Docker NAT overhead on RTSP streams

---

### 3. rate limiting is in-memory only

**Location:** `fleet-server/main.py` — `_login_attempts`

Login brute-force protection uses an in-memory dict. After
`docker compose restart fleet-server` all attempt counters reset.

**Risk:** An attacker who can cause fleet-server restarts (e.g. DoS via
large requests) can reset the rate limit repeatedly.

**Mitigation applied:** Periodic cleanup of stale IPs every 10 minutes
prevents unbounded memory growth under IP-rotating attacks.

**Recommendation for production:** Use nginx `limit_req` in front of
fleet-server for durable rate limiting that survives app restarts:

```nginx
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

location /api/auth/login {
    limit_req zone=login burst=3 nodelay;
    proxy_pass http://fleet-server:8765;
}
```

---

### 4. JWT stored in localStorage (XSS surface)

**Location:** `admin-ui/src/lib/api.js`

JWT tokens are stored in `localStorage`. An XSS vulnerability in the
admin UI would allow token theft.

**Risk level:** Low for self-hosted deployments without user-generated content.

**Recommendation:** If the admin UI ever renders user-submitted content
(camera names, site names from untrusted sources), migrate to HttpOnly
cookies for token storage.

---

## Required Environment Variables

All variables marked **required** must be set in `/opt/nvr-fleet/.env`
before starting the stack. Missing variables generate WARNING logs and
use ephemeral random values that are lost on restart.

| Variable | Required | Purpose |
|----------|----------|---------|
| `ADMIN_TOKEN` | ✅ | Initial admin password and agent auth |
| `JWT_SECRET` | ✅ | Signs JWT session tokens |
| `PUBLIC_HOST` | ✅ | Public hostname for CORS and URLs |
| `MEDIAMTX_VIEWER_PASS` | ✅ | Read-only viewer password for RTSP/HLS |
| `MEDIAMTX_API_PASS` | ✅ | Internal MediaMTX API password |
| `MEDIAMTX_PUBLISH_SECRET` | ✅ | HMAC secret for per-site publish creds |
| `MTX_UI_PASSWORD` | if using MTX Toolkit | nginx basic auth password |

Generate all secrets at once:
```bash
for var in ADMIN_TOKEN JWT_SECRET MEDIAMTX_VIEWER_PASS \
           MEDIAMTX_API_PASS MEDIAMTX_PUBLISH_SECRET; do
    echo "$var=$(openssl rand -hex 32)"
done >> /opt/nvr-fleet/.env
```

---

## Post-Deploy Checklist

- [ ] All required `.env` variables set (no WARNING in `docker compose logs fleet-server`)
- [ ] `Deploy config` clicked in UI for each site (writes mediamtx.yml with real credentials)
- [ ] Firewall blocks ports 9997, 9998 (MediaMTX API/metrics) from external access
- [ ] nginx rate limiting configured for `/api/auth/login`
- [ ] TLS certificate uploaded in System → Certificate
- [ ] Default admin password changed after first login
