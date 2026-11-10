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
            store.save("bridge", {
                "version": 1,
                "wechatAuth": {"state": "valid"},
                "seen": ["a"],
            })
            state = load_bridge_state(store)
        self.assertEqual(state["wechatAuth"], {"state": "valid"})
        self.assertEqual(state["seen"], ["a"])
        self.assertEqual(state["version"], 1)
        for key in ("cursor", "thread", "inbox", "outbox", "running"):
            self.assertIn(key, state)

    def test_repaired_state_does_not_share_mutable_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            first = load_bridge_state(store)
            first["inbox"].append("x")
            second = load_bridge_state(store)
        self.assertEqual(second["inbox"], [])

    def test_non_object_file_is_quarantined_before_defaults_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            raw = '["not", "a", "dict"]\n'
            (root / "bridge.json").write_text(raw, encoding="utf-8")
            state = load_bridge_state(store)
            backups = list(root.glob("bridge.corrupt-*.json"))
            quarantined = backups[0].read_text(encoding="utf-8") if backups else None
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["inbox"], [])
        self.assertEqual(len(backups), 1)
        self.assertEqual(quarantined, raw)

    def test_status_reports_bad_state_without_modifying_or_quarantining_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            path = root / "bridge.json"
            raw = '{"version": 1, "inbox": null}\n'
            path.write_text(raw, encoding="utf-8")
            payload = codex_wechat.status_payload(store)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)
            self.assertEqual(list(root.glob("bridge.corrupt-*.json")), [])
        self.assertEqual(payload["state"], "recovery_required")
        self.assertEqual(payload["state_diagnostic"]["kind"], "invalid_shape")
        self.assertIn("inbox", payload["state_diagnostic"]["detail"])

    def test_malformed_json_is_quarantined_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            raw = '{"cursor": '
            (root / "bridge.json").write_text(raw, encoding="utf-8")
            state = load_bridge_state(store)
            backups = list(root.glob("bridge.corrupt-*.json"))
            quarantined = backups[0].read_text(encoding="utf-8") if backups else None
        self.assertEqual(state["cursor"], "")
        self.assertEqual(state["stateRecovery"]["kind"], "invalid_json")
        self.assertEqual(len(backups), 1)
        self.assertEqual(quarantined, raw)

    def test_nested_invalid_type_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            store.save("bridge", {"version": 1, "pendingControls": {}})
            state = load_bridge_state(store)
        self.assertEqual(state["pendingControls"], [])
        self.assertEqual(state["stateRecovery"]["kind"], "invalid_shape")

    def test_invalid_backend_session_and_budget_types_are_quarantined(self):
        cases = (
            {"version": 1, "backendSessions": {"codex": {"thread": []}}},
            {"version": 1, "replyBudget": {"seq": 1, "used": "many"}},
        )
        for saved in cases:
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory), lock=False)
                store.save("bridge", saved)
                state = load_bridge_state(store)
                self.assertEqual(state["stateRecovery"]["kind"], "invalid_shape")

    def test_unversioned_state_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            store.save("bridge", {"thread": "old"})
            state = load_bridge_state(store)
        self.assertIsNone(state["thread"])
        self.assertEqual(state["stateRecovery"]["kind"], "invalid_shape")
        self.assertIn("version", state["stateRecovery"]["detail"])

    def test_version_zero_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            path = root / "bridge.json"
            raw = '{"version": 0, "thread": "old"}\n'
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported_version"):
                load_bridge_state(store)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)
            self.assertEqual(list(root.glob("bridge.corrupt-*.json")), [])

    def test_structurally_invalid_outbox_is_quarantined(self):
        cases = (
            {"outbox": [{}]},
            {
                "outbox": [{
                    "id": "m",
                    "user": "u",
                    "context": "context",
                    "text": "message",
                }]
            },
        )
        for invalid in cases:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory), lock=False)
                store.save("bridge", {"version": 1, **invalid})
                state = load_bridge_state(store)
                self.assertEqual(state["outbox"], [])
                self.assertEqual(state["stateRecovery"]["kind"], "invalid_shape")

    def test_boolean_binding_pending_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            store.save("bridge", {"version": 1, "bindingPending": True})
            state = load_bridge_state(store)
        self.assertNotIn("bindingPending", state)
        self.assertEqual(state["stateRecovery"]["kind"], "invalid_shape")

    def test_future_version_fails_closed_without_moving_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, lock=False)
            path = root / "bridge.json"
            raw = '{"version": 2}\n'
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported_version"):
                load_bridge_state(store)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)
            self.assertEqual(list(root.glob("bridge.corrupt-*.json")), [])

    def test_valid_status_exposes_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), lock=False)
            payload = codex_wechat.status_payload(store)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["state_version"], 1)


class BridgeRepairTests(unittest.TestCase):
    """Regression: /sessions and the recovery loop read state['thread'] directly."""

    def _bridge(self, saved):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = Store(Path(directory.name), lock=False)
        if saved is not None:
            store.save("bridge", {"version": 1, **saved})
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
