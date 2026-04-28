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
        self.assertIn("pass: VIEWER_PASS", content)
        self.assertIn("user: siteabcd1234", content)
        self.assertIn("pass: PASS_abcd1234", content)
        self.assertIn("path: ~^siteabcd1234/.+$", content)


if __name__ == "__main__":
    unittest.main()
