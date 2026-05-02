"""
fleet-server/auth.py — Authentication and authorization helpers.

Extracted from main.py to isolate auth concerns:
- Password hashing (bcrypt with SHA-256 legacy fallback)
- JWT creation/decoding
- FastAPI dependency functions: require_admin, require_operator, require_viewer
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

try:
    import bcrypt as _bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _BCRYPT_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Secrets ─────────────────────────────────────────────────────────────────

def _load_secret(env_var: str, purpose: str) -> str:
    value = os.environ.get(env_var, "").strip()
    if not value:
        value = secrets.token_hex(32)
        logger.warning(
            "%s not set in environment — using ephemeral value. "
            "Set %s in your .env to persist across restarts.",
            env_var, env_var,
        )
    return value


ADMIN_TOKEN: str = _load_secret("ADMIN_TOKEN", "agent authentication")
JWT_SECRET: str = _load_secret("JWT_SECRET", "session signing")
JWT_ALGO: str = "HS256"
JWT_EXPIRE_HOURS: int = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

# ── Password hashing ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password. Uses bcrypt when available, SHA-256 as fallback."""
    if _BCRYPT_AVAILABLE:
        return "bcrypt:" + _bcrypt.hashpw(
            password.encode(), _bcrypt.gensalt(rounds=12)
        ).decode()
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"sha256:{salt}:{h}"


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against a stored hash.

    Supports:
      - bcrypt: prefixed hashes (current)
      - sha256:<salt>:<hash> (previous format)
      - <salt>:<hash> (oldest format, pre-migration)
    """
    try:
        if password_hash.startswith("bcrypt:"):
            stored = password_hash[len("bcrypt:"):]
            return _bcrypt.checkpw(password.encode(), stored.encode())
        if password_hash.startswith("sha256:"):
            _, salt, h = password_hash.split(":", 2)
        else:
            # oldest format: salt:hash
            salt, h = password_hash.split(":", 1)
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == h
    except Exception:
        return False


# ── JWT ──────────────────────────────────────────────────────────────────────

def create_jwt(user_id: int, username: str, role: str) -> str:
    import jwt as _jwt
    from datetime import datetime, timedelta, timezone
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_jwt(token: str) -> dict:
    import jwt as _jwt
    return _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])


# ── FastAPI dependencies ──────────────────────────────────────────────────────

_security = HTTPBearer(auto_error=False)


def get_current_user_dep(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_security),
    # db is injected by the caller's Depends chain — we receive the User object
    # via a wrapper below to avoid importing SQLAlchemy models here.
):
    """
    Low-level token decoder. Returns decoded JWT payload dict.
    Higher-level dependencies (require_admin, etc.) wrap this with a DB lookup.
    """
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_jwt(creds.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
