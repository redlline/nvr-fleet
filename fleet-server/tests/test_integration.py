"""
fleet-server/tests/test_integration.py

Integration tests using FastAPI TestClient.
These tests start the actual application and verify HTTP behavior end-to-end:
auth endpoints, role enforcement, rate limiting, ACL.
"""
import os
import sys
import json
import pytest

# Ensure fleet-server directory is in path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Set required environment variables before importing app
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token-32chars-padding00")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32chars-padding000")
os.environ.setdefault("MEDIAMTX_VIEWER_PASS", "test-viewer-pass")
os.environ.setdefault("MEDIAMTX_API_PASS", "test-api-pass")
os.environ.setdefault("MEDIAMTX_PUBLISH_SECRET", "test-publish-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration.db")

import unittest

try:
    from fastapi.testclient import TestClient
    from main import app
    from database import engine, Base, SessionLocal
    from models import User
    from auth import hash_password
    HAS_DEPS = True
except ImportError as e:
    HAS_DEPS = False
    SKIP_REASON = str(e)


def setUpTestDB():
    """Create tables and seed test users."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(User(username="admin", password_hash=hash_password("adminpass123"),
                        role="admin", is_active=True, allowed_sites="[]"))
            db.add(User(username="viewer1", password_hash=hash_password("viewerpass123"),
                        role="viewer", is_active=True, allowed_sites="[]"))
            db.add(User(username="operator1", password_hash=hash_password("operatorpass123"),
                        role="operator", is_active=True, allowed_sites="[]"))
            db.commit()
    finally:
        db.close()


def tearDownTestDB():
    import os
    try:
        os.remove("test_integration.db")
    except FileNotFoundError:
        pass


@unittest.skipUnless(HAS_DEPS, f"Dependencies missing: {'' if HAS_DEPS else SKIP_REASON}")
class AuthEndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        setUpTestDB()
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        tearDownTestDB()

    def _login(self, username, password):
        r = self.client.post("/api/auth/login",
                             json={"username": username, "password": password})
        return r

    def _auth_header(self, username, password):
        r = self._login(username, password)
        assert r.status_code == 200, f"Login failed: {r.text}"
        return {"Authorization": f"Bearer {r.json()['token']}"}

    # ── Login ──────────────────────────────────────────────────────────────

    def test_login_valid_admin(self):
        r = self._login("admin", "adminpass123")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("token", data)
        self.assertEqual(data["role"], "admin")
        self.assertEqual(data["username"], "admin")

    def test_login_wrong_password(self):
        r = self._login("admin", "wrongpassword")
        self.assertEqual(r.status_code, 401)

    def test_login_unknown_user(self):
        r = self._login("nobody", "anything")
        self.assertEqual(r.status_code, 401)

    def test_login_missing_username(self):
        r = self.client.post("/api/auth/login", json={"password": "x"})
        self.assertIn(r.status_code, [400, 401, 422])

    # ── auth/me ────────────────────────────────────────────────────────────

    def test_auth_me_valid_token(self):
        headers = self._auth_header("admin", "adminpass123")
        r = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["username"], "admin")
        self.assertEqual(data["role"], "admin")

    def test_auth_me_no_token(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 401)

    def test_auth_me_bad_token(self):
        r = self.client.get("/api/auth/me",
                            headers={"Authorization": "Bearer invalid.token.here"})
        self.assertEqual(r.status_code, 401)

    # ── Role enforcement ──────────────────────────────────────────────────

    def test_viewer_can_access_dashboard(self):
        headers = self._auth_header("viewer1", "viewerpass123")
        r = self.client.get("/api/dashboard", headers=headers)
        self.assertEqual(r.status_code, 200)

    def test_viewer_cannot_create_site(self):
        headers = self._auth_header("viewer1", "viewerpass123")
        r = self.client.post("/api/sites",
                             json={"name": "X", "city": "X", "vendor": "hikvision"},
                             headers=headers)
        self.assertEqual(r.status_code, 403)

    def test_viewer_cannot_access_users(self):
        headers = self._auth_header("viewer1", "viewerpass123")
        r = self.client.get("/api/users", headers=headers)
        self.assertEqual(r.status_code, 403)

    def test_operator_cannot_create_site(self):
        headers = self._auth_header("operator1", "operatorpass123")
        r = self.client.post("/api/sites",
                             json={"name": "X", "city": "X", "vendor": "hikvision"},
                             headers=headers)
        self.assertEqual(r.status_code, 403)

    def test_admin_can_list_users(self):
        headers = self._auth_header("admin", "adminpass123")
        r = self.client.get("/api/users", headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_unauthenticated_cannot_list_sites(self):
        r = self.client.get("/api/sites")
        self.assertEqual(r.status_code, 401)

    # ── Rate limiting ──────────────────────────────────────────────────────

    def test_rate_limit_login_triggers_429(self):
        """After _LOGIN_MAX_ATTEMPTS bad logins, next attempt returns 429."""
        from main import _login_attempts, _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECONDS
        import time
        # Directly stuff the dict to simulate exhaustion without 10 real requests
        _login_attempts["test-rate-ip"] = [time.time()] * _LOGIN_MAX_ATTEMPTS
        r = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "x"},
            headers={"x-forwarded-for": "test-rate-ip"},  # won't match client.host
        )
        # The middleware uses request.client.host (127.0.0.1 in TestClient)
        # so trigger it directly via the dict
        from main import _check_login_rate_limit
        from fastapi import HTTPException as FHE
        with self.assertRaises(FHE) as ctx:
            _check_login_rate_limit("test-rate-ip")
        self.assertEqual(ctx.exception.status_code, 429)
        # Cleanup
        del _login_attempts["test-rate-ip"]

    def test_rate_limit_cleanup_removes_stale_ips(self):
        """Stale IPs older than the window are purged on next call."""
        from main import _login_attempts, _login_last_cleanup
        import main as _main, time
        _main._login_last_cleanup = 0.0  # force cleanup on next call
        # Add a very old entry
        _login_attempts["stale-ip"] = [time.time() - 9999]
        _main._check_login_rate_limit("fresh-ip")
        self.assertNotIn("stale-ip", _login_attempts)
        _login_attempts.pop("fresh-ip", None)

    # ── Password length guard ─────────────────────────────────────────────

    def test_create_user_rejects_password_over_72_bytes(self):
        headers = self._auth_header("admin", "adminpass123")
        long_pass = "a" * 73
        r = self.client.post("/api/users",
                             json={"username": "toolong", "password": long_pass, "role": "viewer"},
                             headers=headers)
        self.assertEqual(r.status_code, 400)
        self.assertIn("72", r.json().get("detail", ""))

    # ── Backup size limit ────────────────────────────────────────────────

    def test_backup_import_rejects_oversized_file(self):
        headers = self._auth_header("admin", "adminpass123")
        big = b"x" * (51 * 1024 * 1024)  # 51 MB
        from io import BytesIO
        r = self.client.post(
            "/api/system/backup/import",
            files={"file": ("big.zip", BytesIO(big), "application/zip")},
            headers=headers,
        )
        self.assertEqual(r.status_code, 413)


    def test_realtime_traffic_returns_structure(self):
        """GET /api/traffic/realtime must return rx_bps and tx_bps fields."""
        headers = self._auth_header("admin", "adminpass123")
        r = self.client.get("/api/traffic/realtime", headers=headers)
        # Endpoint connects to MediaMTX; in test env it returns zeros but structure is valid
        self.assertIn(r.status_code, [200])
        data = r.json()
        self.assertIn("rx_bps", data)
        self.assertIn("tx_bps", data)
        self.assertIsInstance(data["rx_bps"], int)
        self.assertIsInstance(data["tx_bps"], int)

    def test_dashboard_returns_structure(self):
        """GET /api/dashboard must return summary counters."""
        headers = self._auth_header("admin", "adminpass123")
        r = self.client.get("/api/dashboard", headers=headers)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for key in ("total_sites", "online_agents", "offline_agents",
                    "total_cameras", "live_streams"):
            self.assertIn(key, data, f"missing key: {key}")

    def test_map_returns_list(self):
        """GET /api/map must return a list."""
        headers = self._auth_header("viewer1", "viewerpass123")
        r = self.client.get("/api/map", headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

if __name__ == "__main__":
    unittest.main()
