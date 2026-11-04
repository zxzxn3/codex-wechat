"""The persisted shared-daemon choice and its reconnect."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from backend import WRITER_BUSY
from codex_wechat import Bridge
from commands import dispatch
from storage import Store


class SharedSettingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state")
        self.addCleanup(self.store.lock.close)
        self.path = Path(self.temp.name) / "config.json"

    def bridge(self, config=None):
        bridge = Bridge(self.store, {"user": "owner"}, config or {}, config_path=self.path)
        bridge.state["context"] = "context"
        bridge.state["thread"] = "thread-1"
        bridge.backend.release_thread = AsyncMock()
        bridge.backend.close = AsyncMock()
        return bridge

    def reconnect(self, value):
        """Stand in for profiles.switch, which would reconnect with the new mode."""
        async def run(bridge, name, *, force=False, adopt_busy=False):
            self.assertTrue(force)
            self.assertTrue(adopt_busy)
            bridge.backend.transport.shared = value
        return AsyncMock(side_effect=run)

    async def test_shared_on_is_persisted_and_reconnects(self):
        self.path.write_text(json.dumps({"cwd": self.temp.name,
                                         "backends": {"codex": {
                                             "command": ["codex", "app-server"]}}}),
                             encoding="utf-8")
        bridge = self.bridge({"backends": {"codex": {
            "command": ["codex", "app-server"]}}, "cwd": self.temp.name})
        with patch("commands.switch", self.reconnect(True)) as switch:
            await dispatch(bridge, "/settings shared true")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(saved["backends"]["codex"]["sharedDaemon"])
        # Unrelated keys survive the rewrite.
        self.assertEqual(saved["cwd"], self.temp.name)
        self.assertEqual(saved["backends"]["codex"]["command"], ["codex", "app-server"])
        switch.assert_awaited_once_with(bridge, "codex", force=True, adopt_busy=True)
        self.assertTrue(bridge.backend_configs["codex"]["sharedDaemon"])
        self.assertIn("shared: true", bridge.state["outbox"][-1]["text"])

    async def test_shared_choice_overrides_a_definition_written_by_desktop_setup(self):
        # `codex-desktop setup` stores sharedDaemon under backends[name].
        self.path.write_text(json.dumps({"backends": {
                                         "codex": {"sharedDaemon": True}}}),
                             encoding="utf-8")
        bridge = self.bridge({"backends": {"codex": {"sharedDaemon": True}}})
        self.assertTrue(bridge.backend.shared)
        with patch("commands.switch", self.reconnect(False)):
            await dispatch(bridge, "/settings shared false")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(saved["backends"]["codex"]["sharedDaemon"])
        self.assertFalse(bridge.backend_configs["codex"]["sharedDaemon"])

    async def test_shared_off_releases_the_thread_before_reconnecting(self):
        bridge = self.bridge()
        bridge.backend.shared_fallback = None
        bridge.backend.transport.shared = True
        with patch("commands.switch", self.reconnect(False)) as switch:
            await dispatch(bridge, "/settings shared false")
        bridge.backend.release_thread.assert_awaited_once_with("thread-1")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(saved["backends"]["codex"]["sharedDaemon"])
        switch.assert_awaited_once_with(bridge, "codex", force=True, adopt_busy=True)

    async def test_a_mode_change_is_refused_while_a_turn_runs(self):
        bridge = self.bridge()
        bridge.running = True
        with patch("commands.switch", AsyncMock()) as switch:
            await dispatch(bridge, "/settings shared true")
        # Switching mode restarts the backend, which would abort the live turn.
        self.assertIn(bridge.prompt("BUSY_CHANGE"), bridge.state["outbox"][-1]["text"])
        switch.assert_not_called()
        self.assertFalse(self.path.exists())

    async def test_shared_off_reports_a_thread_that_is_still_busy(self):
        bridge = self.bridge()
        bridge.backend.transport.shared = True
        bridge.backend.set_writer(WRITER_BUSY, "thread-1")
        with patch("commands.switch", self.reconnect(False)):
            await dispatch(bridge, "/settings shared false")
        self.assertIn("下一条消息会自动接管", bridge.state["outbox"][-1]["text"])

    async def test_shared_summary_reports_the_unavailable_reason(self):
        bridge = self.bridge()
        bridge.backend.shared_fallback = "共享后台自动启动后仍未连接成功：连接超时"
        with patch("commands.switch", AsyncMock()):
            await dispatch(bridge, "/settings shared true")
        text = bridge.state["outbox"][-1]["text"]
        self.assertIn("共享后台不可用", text)
        self.assertIn("连接超时", text)

    async def test_settings_lists_the_current_mode(self):
        bridge = self.bridge()
        bridge.ensure_backend = AsyncMock(side_effect=AssertionError("no backend"))
        with patch("commands.autostart.status", AsyncMock(return_value=True)):
            await dispatch(bridge, "/settings")
        self.assertIn("shared:", bridge.state["outbox"][-1]["text"])
        await dispatch(bridge, "/settings shared maybe")
        self.assertIn("用法", bridge.state["outbox"][-1]["text"])

    async def test_unwritable_configuration_is_reported(self):
        bridge = Bridge(self.store, {"user": "owner"}, {})
        bridge.state["context"] = "context"
        with patch("commands.switch", AsyncMock()) as switch:
            await dispatch(bridge, "/settings shared true")
        self.assertIn("未保存共享后台设置", bridge.state["outbox"][-1]["text"])
        switch.assert_not_called()

    async def test_a_failed_switch_leaves_the_file_and_the_mode_untouched(self):
        self.path.write_text(json.dumps({"backends": {"codex": {"sharedDaemon": True}}}),
                             encoding="utf-8")
        bridge = self.bridge({"backends": {"codex": {"sharedDaemon": True}}})
        with patch("commands.switch", AsyncMock(side_effect=RuntimeError("boom"))):
            await dispatch(bridge, "/settings shared false")
        self.assertIn("失败", bridge.state["outbox"][-1]["text"])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(saved["backends"]["codex"]["sharedDaemon"])
        self.assertTrue(bridge.backend_configs["codex"]["sharedDaemon"])


if __name__ == "__main__":
    unittest.main()
