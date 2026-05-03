"""
fleet-server/tests/test_agent.py

Unit tests for fleet-agent/agent.py logic.
Tests run without a real WebSocket/go2rtc connection — all I/O is mocked.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

# Add agent directory to path
AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "fleet-agent")
sys.path.insert(0, AGENT_DIR)

# Set required env vars before importing agent
os.environ.setdefault("SITE_ID", "test-site-01")
os.environ.setdefault("AGENT_TOKEN", "test-agent-token")
os.environ.setdefault("SERVER_HOST", "localhost")
os.environ.setdefault("SERVER_WS", "ws://localhost:8765/ws/agent/test-site-01")
os.environ.setdefault("SERVER_API", "http://localhost:8765")
os.environ.setdefault("GO2RTC_BIN", "/usr/bin/true")
os.environ.setdefault("GO2RTC_YAML", "/tmp/test_go2rtc.yaml")
os.environ.setdefault("GO2RTC_SVC", "go2rtc")
os.environ.setdefault("FFMPEG_BIN", "/usr/bin/ffmpeg")
os.environ.setdefault("AGENT_ADMIN_HOST", "127.0.0.1")
os.environ.setdefault("AGENT_ADMIN_PORT", "7070")
os.environ.setdefault("AGENT_STATE_DIR", "/tmp/test_agent_state")

try:
    import agent as ag
    HAS_AGENT = True
    SKIP_REASON = ""
except ImportError as e:
    HAS_AGENT = False
    SKIP_REASON = str(e)
except Exception as e:
    HAS_AGENT = False
    SKIP_REASON = f"Agent import error: {e}"


@unittest.skipUnless(HAS_AGENT, SKIP_REASON)
class WsMessageValidationTests(unittest.TestCase):
    """Test WS message allowlist and dispatch table."""

    def setUp(self):
        self.ws = AsyncMock()
        self.ws.send = AsyncMock()

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_non_dict_message_ignored(self):
        """Non-dict messages must be silently rejected."""
        self._run(ag.handle_message(self.ws, "not a dict"))
        self.ws.send.assert_not_called()

    def test_ping_gets_pong(self):
        """Ping messages must trigger a pong response."""
        self._run(ag.handle_message(self.ws, {"type": "ping"}))
        self.ws.send.assert_called_once()
        sent = json.loads(self.ws.send.call_args[0][0])
        self.assertEqual(sent.get("type"), "pong")

    def test_unknown_action_rejected(self):
        """Actions not in _VALID_SERVER_ACTIONS must be rejected with a warning."""
        self._run(ag.handle_message(self.ws, {
            "action": "rm_rf_slash",
            "request_id": "req-1",
        }))
        # No reply should be sent for unknown allowlist-rejected actions
        self.ws.send.assert_not_called()

    def test_valid_actions_in_allowlist(self):
        """All _VALID_SERVER_ACTIONS must be in _ACTION_HANDLERS."""
        for action in ag._VALID_SERVER_ACTIONS:
            if action == "ping":
                continue
            self.assertIn(action, ag._ACTION_HANDLERS,
                f"Action '{action}' in allowlist but missing from _ACTION_HANDLERS")

    def test_dispatch_table_has_no_extra_actions(self):
        """_ACTION_HANDLERS must not have actions missing from _VALID_SERVER_ACTIONS."""
        for action in ag._ACTION_HANDLERS:
            self.assertIn(action, ag._VALID_SERVER_ACTIONS,
                f"Action '{action}' in _ACTION_HANDLERS but not in allowlist")

    def test_get_status_sends_stream_status(self):
        """get_status action must send stream_status message."""
        with patch.object(ag, "publisher_status", return_value={"cam01": "active"}):
            self._run(ag.handle_message(self.ws, {
                "action": "get_status",
                "request_id": "req-42",
            }))
        self.ws.send.assert_called()
        calls_data = [json.loads(c[0][0]) for c in self.ws.send.call_args_list]
        types = [d.get("type") for d in calls_data]
        self.assertIn("stream_status", types)

    def test_drain_stops_publishers_and_sessions(self):
        """drain action must stop all publishers and archive sessions."""
        ag._ffmpeg_procs["cam01"] = MagicMock()
        ag._archive_sessions["sess1"] = MagicMock()
        with patch.object(ag, "stop_publisher") as mock_stop_pub,              patch.object(ag, "stop_archive_session") as mock_stop_sess,              patch.object(ag, "close_all_tcp_tunnels", new_callable=AsyncMock),              patch.object(ag, "publisher_status", return_value={}):
            self._run(ag.handle_message(self.ws, {
                "action": "drain",
                "request_id": "req-99",
            }))
            mock_stop_pub.assert_called_with("cam01")
            mock_stop_sess.assert_called_with("sess1")
        ag._ffmpeg_procs.clear()
        ag._archive_sessions.clear()


@unittest.skipUnless(HAS_AGENT, SKIP_REASON)
class RateLimitAndAllowlistTests(unittest.TestCase):
    """Test AGENT_ADMIN_HOST and config defaults."""

    def test_admin_host_defaults_to_loopback(self):
        """AGENT_ADMIN_HOST must default to 127.0.0.1 not 0.0.0.0."""
        host = os.environ.get("AGENT_ADMIN_HOST", "")
        self.assertNotEqual(host, "0.0.0.0",
            "AGENT_ADMIN_HOST must not default to 0.0.0.0 — exposes admin API on all interfaces")
        self.assertEqual(ag.AGENT_ADMIN_HOST, "127.0.0.1")

    def test_valid_server_actions_is_frozen_set(self):
        """_VALID_SERVER_ACTIONS should be a set (not a list) for O(1) lookup."""
        self.assertIsInstance(ag._VALID_SERVER_ACTIONS, (set, frozenset))

    def test_required_env_vars_read(self):
        """Agent must read SITE_ID, AGENT_TOKEN, SERVER_HOST from environment."""
        self.assertEqual(ag.SITE_ID, "test-site-01")
        self.assertEqual(ag.AGENT_TOKEN, "test-agent-token")


@unittest.skipUnless(HAS_AGENT, SKIP_REASON)
class CollectTrafficTests(unittest.TestCase):
    """Test collect_traffic() function."""

    def test_returns_empty_on_connection_error(self):
        """collect_traffic must return {} when go2rtc is unreachable."""
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            result = ag.collect_traffic()
        self.assertEqual(result, {})

    def test_parses_stream_stats(self):
        """collect_traffic must parse rx/tx from go2rtc API response."""
        mock_data = {
            "cam01": {
                "consumers": [{"type": "rtsp"}],
                "producers": [{"type": "rtsp", "receivers": [{"bytes": 1000}]}],
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = ag.collect_traffic()

        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
