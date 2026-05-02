# NVR-Fleet — Security Hardening Log

This document tracks actual security improvements made to the codebase.
It is a factual record, not a wish-list.

---

## Implemented (this session)

### 1. Repo hygiene — npm cache removed from git (commit `50ca7ec`)

**Problem:** `admin-ui/.npm-cache/` (498 files, ~1300 tracked blobs) was committed
despite being in `.gitignore`. This bloated the repo and leaked internal build
state.

**Fix:** Created a new git tree without any `.npm-cache/` paths. `.gitignore`
already had the correct rule — the files were just already tracked.

---

### 2. Dangerous default secrets removed (commit `1dcf0fb`)

**Problem:**
- `ADMIN_TOKEN` defaulted to `"admin-secret-change-me"` — a known string
- `JWT_SECRET` was set to `ADMIN_TOKEN` — single secret served two roles
- `docker-compose.yml` had `ADMIN_TOKEN: ${ADMIN_TOKEN:-change-me-now}`

**Fix:**
- If `ADMIN_TOKEN` is unset: generate an ephemeral random value with a
  `WARNING` log. Sessions survive restart only if set in `.env`.
- `JWT_SECRET` is now independent of `ADMIN_TOKEN`. Also ephemeral-with-warning
  if unset.
- `docker-compose.yml` passes `${ADMIN_TOKEN}` and `${JWT_SECRET}` without
  fallback defaults — server fails loudly if not configured.
- `.env.example` updated with all required variables and instructions.

---

### 3. Password hashing upgraded to bcrypt (commit `1dcf0fb`)

**Problem:** Passwords were stored as `sha256(salt + password)`. SHA-256 is
fast — trivially brute-forced with GPU acceleration.

**Fix:**
- `_hash_password()` now uses bcrypt (cost factor 12) when the `bcrypt`
  library is available.
- `_verify_password()` supports all three historical formats:
  - `bcrypt:<hash>` — current
  - `sha256:<salt>:<hash>` — previous format
  - `<salt>:<hash>` — oldest format (no migration needed; verified on login)
- `bcrypt>=4.1.0` added to `requirements.txt`.
- Existing password hashes continue to work. Passwords are re-hashed to
  bcrypt format on next successful login (future improvement).

---

### 4. Legacy super-admin auth paths removed (commit `1dcf0fb`)

**Problem — two legacy paths:**

1. **Bearer `ADMIN_TOKEN`** — any request with
   `Authorization: Bearer <ADMIN_TOKEN>` bypassed JWT and database entirely,
   returning a virtual admin user with `id=0`.

2. **Password-only login** — `POST /api/auth/login` with no `username` and
   `password == ADMIN_TOKEN` returned the raw `ADMIN_TOKEN` string as a JWT
   token. This violates JWT semantics and creates a token that never expires.

**Fix:** Both paths removed. All authentication now requires a valid JWT issued
by `POST /api/auth/login` with `username` + `password`. The `ADMIN_TOKEN` is
used only for agent WebSocket authentication.

---

### 5. Zip traversal protection in backup import (commit `1dcf0fb`)

**Problem:** `POST /api/system/backup/import` extracted a zip file without
validating paths. A crafted backup could write files outside the intended
directory via paths like `../../../../etc/cron.d/evil`.

**Fix:** Before reading any entry, iterate `archive.namelist()` and reject
any entry that:
- starts with `/` (absolute path)
- contains `..` as a path component

Returns HTTP 400 with the offending path if triggered.

---

### 6. Auth module extracted (commit `142af10`)

**Problem:** All auth logic (hashing, JWT, role dependencies) was inline in
the 2733-line `main.py` monolith.

**Fix:** Created `fleet-server/auth.py` with clean public API:
- `hash_password(password) -> str`
- `verify_password(password, hash) -> bool`
- `create_jwt(user_id, username, role) -> str`
- `decode_jwt(token) -> dict`
- `ADMIN_TOKEN`, `JWT_SECRET` — loaded once at import with ephemeral fallback

`main.py` imports from `auth` — behavior unchanged, boundaries cleaner.

---

## Remaining risks (prioritized)

| Risk | Severity | Notes |
|------|----------|-------|
| SHA-256 legacy hashes not auto-upgraded on login | Medium | Add re-hash on successful login |
| `allow_origins=["*"]` CORS | Medium | Acceptable for self-hosted if not internet-exposed |
| `/var/run/docker.sock` volume in compose | High | Required for stack control; document threat model |
| No rate limiting on `/api/auth/login` | Medium | Add `slowapi` or nginx limit_req |
| `MEDIAMTX_INTERNAL_PASS` still a string in compose env | Medium | Fixed in docker-compose; ensure `.env` is set |
| main.py still 2700+ lines | Medium | Further modularization in progress |
| No TLS enforcement at app level | Low | Handled by nginx; document this |

---

## Next recommended actions

1. Re-hash legacy SHA-256 passwords to bcrypt on successful login
2. Add login rate limiting (`slowapi` or nginx `limit_req`)
3. Continue extracting `backup.py`, `tls_utils.py`, `mtx_sync.py` from main.py
4. Add `ARCHITECTURE.md` documenting the current module map
