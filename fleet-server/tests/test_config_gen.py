import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_gen import generate_go2rtc_yaml, normalize_stream_path, update_mediamtx_paths


class ConfigGenTests(unittest.TestCase):
    def test_generate_go2rtc_yaml_uses_local_stream_names_and_quoted_credentials(self):
        site = SimpleNamespace(
            id="abcd1234",
            nvr_user="admin@example.com",
            nvr_pass="p@ss word",
            nvr_ip="192.168.1.64",
            nvr_port=554,
        )
        cameras = [
            SimpleNamespace(site_id=site.id, channel=1, channel_id=101, enabled=True),
            SimpleNamespace(site_id=site.id, channel=2, channel_id=201, enabled=False),
        ]

        text = generate_go2rtc_yaml(site, cameras)

        self.assertIn("siteabcd1234_cam01", text)
        self.assertNotIn("siteabcd1234_cam02", text)
        self.assertIn("admin%40example.com:p%40ss%20word@192.168.1.64:554", text)

    def test_normalize_stream_path_restores_public_path(self):
        self.assertEqual(normalize_stream_path("siteabcd1234_cam01"), "siteabcd1234/cam01")
        self.assertEqual(normalize_stream_path("siteabcd1234/cam01"), "siteabcd1234/cam01")

    def test_update_mediamtx_paths_rewrites_auth_internal_users(self):
        site = SimpleNamespace(id="abcd1234")
        cameras = [SimpleNamespace(site_id=site.id, channel=1, channel_id=101, enabled=True)]

        fd, path = tempfile.mkstemp(suffix=".yml")
        os.close(fd)
        try:
            update_mediamtx_paths(path, [site], cameras)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        finally:
            os.unlink(path)

        self.assertIn("authMethod: internal", content)
        self.assertIn("user: viewer", content)
        # viewer pass comes from MEDIAMTX_VIEWER_PASS env var (empty string in test env)
        self.assertIn("pass:", content)  # viewer entry exists
        self.assertIn("user: viewer", content)
        self.assertIn("user: siteabcd1234", content)
        self.assertIn("pass: PASS_abcd1234", content)
        self.assertIn("path: ~^siteabcd1234/.+$", content)



class AuthHelpersTests(unittest.TestCase):
    def test_hash_and_verify_password_roundtrip(self):
        """hash_password / verify_password must round-trip correctly."""
        import sys
        sys.path.insert(0, os.path.join(ROOT))
        from auth import hash_password, verify_password

        h = hash_password("correct-horse-battery-staple")
        self.assertTrue(verify_password("correct-horse-battery-staple", h))
        self.assertFalse(verify_password("wrong-password", h))

    def test_verify_password_rejects_legacy_sha256_prefix_format(self):
        """Legacy sha256:salt:hash format still verifies correctly."""
        import hashlib, secrets as _secrets
        from auth import verify_password

        salt = _secrets.token_hex(16)
        password = "legacy-password"
        h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        stored = f"sha256:{salt}:{h}"
        self.assertTrue(verify_password(password, stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_verify_password_rejects_oldest_format(self):
        """Oldest salt:hash format (no prefix) still verifies correctly."""
        import hashlib, secrets as _secrets
        from auth import verify_password

        salt = _secrets.token_hex(16)
        password = "oldest-format"
        h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        stored = f"{salt}:{h}"
        self.assertTrue(verify_password(password, stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_mediamtx_viewer_pass_comes_from_env(self):
        """mediamtx_viewer_pass() must read from MEDIAMTX_VIEWER_PASS env var."""
        import sys, os
        sys.path.insert(0, ROOT)
        from config_gen import mediamtx_viewer_pass

        os.environ["MEDIAMTX_VIEWER_PASS"] = "test-viewer-secret"
        try:
            self.assertEqual(mediamtx_viewer_pass(), "test-viewer-secret")
        finally:
            del os.environ["MEDIAMTX_VIEWER_PASS"]

    def test_mediamtx_viewer_pass_defaults_to_empty(self):
        """mediamtx_viewer_pass() returns empty string when env var not set."""
        import sys, os
        sys.path.insert(0, ROOT)
        from config_gen import mediamtx_viewer_pass

        os.environ.pop("MEDIAMTX_VIEWER_PASS", None)
        self.assertEqual(mediamtx_viewer_pass(), "")

if __name__ == "__main__":
    unittest.main()

