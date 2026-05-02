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



class StreamStatRtspTests(unittest.TestCase):
    def test_stream_stat_rtsp_url_field_exists(self):
        """StreamStatOut must have an optional rtsp_url field."""
        import sys
        sys.path.insert(0, ROOT)
        from schemas import StreamStatOut
        # Field must be present and default to None
        fields = StreamStatOut.model_fields
        self.assertIn("rtsp_url", fields)
        self.assertIsNone(fields["rtsp_url"].default)

    def test_mediamtx_viewer_pass_env_round_trip(self):
        """MEDIAMTX_VIEWER_PASS must propagate consistently from env to config_gen."""
        import os, sys
        sys.path.insert(0, ROOT)
        from config_gen import mediamtx_viewer_pass

        os.environ["MEDIAMTX_VIEWER_PASS"] = "s3cr3t-v1ewer"
        try:
            val = mediamtx_viewer_pass()
            self.assertEqual(val, "s3cr3t-v1ewer",
                "mediamtx_viewer_pass() must return the MEDIAMTX_VIEWER_PASS env var value")
        finally:
            del os.environ["MEDIAMTX_VIEWER_PASS"]

    def test_mediamtx_viewer_pass_empty_when_unset(self):
        """mediamtx_viewer_pass() must return '' when env var is absent."""
        import os, sys
        sys.path.insert(0, ROOT)
        from config_gen import mediamtx_viewer_pass
        os.environ.pop("MEDIAMTX_VIEWER_PASS", None)
        self.assertEqual(mediamtx_viewer_pass(), "")

if __name__ == "__main__":
    unittest.main()

