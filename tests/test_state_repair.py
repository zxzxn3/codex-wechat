"""A state file written before the bridge runs must not break the bridge."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat import app as codex_wechat
from codex_wechat.commands import _session_lines
from codex_wechat.app import BRIDGE_STATE_DEFAULTS, Bridge, load_bridge_state
from codex_wechat.storage import Store


class LoadBridgeStateTests(unittest.TestCase):
    def test_missing_file_yields_the_full_default_set(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            self.assertEqual(load_bridge_state(store), BRIDGE_STATE_DEFAULTS)

    def test_partial_file_is_repaired_without_losing_saved_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            store.save("bridge", {"wechatAuth": {"state": "valid"}, "seen": ["a"]})
            state = load_bridge_state(store)
        self.assertEqual(state["wechatAuth"], {"state": "valid"})
        self.assertEqual(state["seen"], ["a"])
        for key in ("cursor", "thread", "inbox", "outbox", "running"):
            self.assertIn(key, state)

    def test_repaired_state_does_not_share_mutable_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            first = load_bridge_state(store)
            first["inbox"].append("x")
            second = load_bridge_state(store)
        self.assertEqual(second["inbox"], [])

    def test_non_object_file_is_replaced_by_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            store.save("bridge", ["not", "a", "dict"])
            self.assertEqual(load_bridge_state(store), BRIDGE_STATE_DEFAULTS)

    def test_status_payload_accepts_a_non_object_bridge_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            store.save("bridge", ["not", "a", "dict"])
            payload = codex_wechat.status_payload(store)
        self.assertFalse(payload["unfinished_at_last_save"])
        self.assertEqual(payload["queued"], 0)


class BridgeRepairTests(unittest.TestCase):
    """Regression: /sessions and the recovery loop read state['thread'] directly."""

    def _bridge(self, saved):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = Store(Path(directory.name), lock=False)
        if saved is not None:
            store.save("bridge", saved)
        with patch.object(codex_wechat, "WeChat"):
            bridge = Bridge(store, {"user": "u", "token": "t"}, {})
        return bridge

    def test_bridge_starts_from_a_login_only_state_file(self):
        bridge = self._bridge({"wechatAuth": {"state": "valid"}})
        self.assertIsNone(bridge.state["thread"])
        self.assertEqual(bridge.state["inbox"], [])
        self.assertEqual(bridge.state["outbox"], [])

    def test_session_rendering_no_longer_raises(self):
        bridge = self._bridge({"wechatAuth": {"state": "valid"}})
        lines = _session_lines(bridge, [])
        self.assertEqual(len(lines), 1)

    def test_recovery_precondition_reads_without_raising(self):
        bridge = self._bridge({"wechatAuth": {"state": "valid"}})
        self.assertFalse(bridge.state["thread"])

    def test_a_saved_thread_survives_the_repair(self):
        bridge = self._bridge({"thread": "01a1a470-a6bd-7e70", "seen": ["a"]})
        self.assertEqual(bridge.state["thread"], "01a1a470-a6bd-7e70")
        self.assertEqual(bridge.state["seen"], ["a"])
