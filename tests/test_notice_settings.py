"""Live notice updates against private temporary files, no backend requests."""
import json
from pathlib import Path
import tempfile
import unittest

from codex_wechat import Bridge
from commands import handle, notice
from configuration import HotSettings, Notifications, completion_template
from storage import Store


class NoticeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_update_completion_and_file_reload(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            initial = {"processingNotice": "old", "completionNotice": ""}
            path.write_text(json.dumps(initial))
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, initial, config_path=path)
                bridge.state["context"] = "ctx"
                await handle(bridge, "/ack", "text hello")
                self.assertEqual(notice(bridge), "hello")
                await handle(bridge, "/finish", "text done {model}")
                await bridge.deliver_answer("answer", notify_completion=True)
                self.assertEqual(bridge.state["outbox"][-1]["text"], "done 默认模型")
                path.write_text(json.dumps({"processingNotice": "new file", "completionNotice": "file done"}))
                self.assertEqual(notice(bridge), "new file")
                self.assertEqual(completion_template(bridge.config, bridge.state), "file done")
                path.write_text("{invalid")
                self.assertEqual(notice(bridge), "new file")
                await handle(bridge, "/finish", "text")
                self.assertEqual(completion_template(bridge.config, bridge.state), "")
                self.assertFalse(bridge.backend_started)
            finally:
                store.lock.close()

    async def test_shared_hot_reload_is_validated_before_applying(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            initial = {"processingNotice": "old", "cwd": root}
            path.write_text(json.dumps(initial))
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, initial, config_path=path)
                bridge.state["processingNotice"] = "chat override"
                fresh = dict(initial, sourceHint="custom hint", slicing={"targetChars": 900},
                             compactionNotice=True,
                             quotaWarningThresholds={"primary": [25], "secondary": [10]},
                             cwd="/new")
                path.write_text(json.dumps(fresh))
                self.assertEqual(bridge.current_input_hint(), "\n\ncustom hint")
                self.assertEqual(bridge.slicing.target_chars, 900)
                self.assertTrue(bridge.backend.compaction_notice)
                self.assertEqual(bridge.notifications.quota_thresholds,
                                 {"primary": (25,), "secondary": (10,)})
                self.assertEqual(notice(bridge), "chat override")
                self.assertEqual(bridge.config["cwd"], root)
                fresh.update(sourceHint="bad candidate", compactionNotice="invalid")
                path.write_text(json.dumps(fresh))
                self.assertEqual(bridge.current_input_hint(), "\n\ncustom hint")
                self.assertEqual(bridge.config["sourceHint"], "custom hint")
                fresh.update(sourceHint="", compactionNotice=False)
                path.write_text(json.dumps(fresh))
                self.assertEqual(bridge.current_input_hint(), "")
                path.unlink()
                self.assertEqual(bridge.current_input_hint(), "")
                self.assertEqual(bridge.slicing.target_chars, 900)
                self.assertFalse(bridge.backend_started)
            finally:
                store.lock.close()

    async def test_settings_summary_shows_independent_quota_windows(self):
        from prompts import settings_summary
        text = settings_summary(
            notice="", attachment_mib=20,
            notifications=Notifications.from_config({"quotaWarningThresholds": {
                "primary": [25, 10, 5],
                "secondary": [50, 25, 10, 5, 1],
            }}))
        self.assertIn("限额警告: 5小时=25%/10%/5%；7天=50%/25%/10%/5%/1%", text)


class ConnectionIdleSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_reload_invalid_value_and_delete_restore_default(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            path.write_text('{"connectionIdleSeconds":60}')
            store = Store(Path(root) / "state")
            bridge = Bridge(store, {"user": "owner"}, {"connectionIdleSeconds": 60}, config_path=path)
            try:
                path.write_text('{"connectionIdleSeconds":2}')
                bridge.reload_config()
                self.assertEqual(bridge.settings.hot.connection_idle_seconds, 2)
                self.assertEqual(bridge.channel.http.outgoing.idle_seconds, 2)
                self.assertEqual(bridge.channel.http.poll.idle_seconds, 2)
                path.write_text('{"connectionIdleSeconds":-1}')
                bridge.reload_config()
                self.assertEqual(bridge.channel.http.outgoing.idle_seconds, 2)
                path.write_text('{"connectionIdleSeconds":0}')
                bridge.reload_config()
                self.assertEqual(bridge.channel.http.outgoing.idle_seconds, 0)
                self.assertEqual(bridge.channel.http.poll.idle_seconds, 0)
                path.write_text('{}')
                bridge.reload_config()
                self.assertEqual(bridge.channel.http.outgoing.idle_seconds, 60)
                self.assertNotIn("connectionIdleSeconds", bridge.config)
                self.assertFalse(bridge.backend_started)
            finally:
                await bridge.channel.close()
                store.close()

    async def test_settings_summary_shows_effective_value(self):
        from prompts import settings_summary
        text = settings_summary(notice="", attachment_mib=10, connection_idle_seconds=2)
        self.assertIn("连接空闲: 2s", text)
        text = settings_summary(notice="", attachment_mib=10, connection_idle_seconds=0)
        self.assertIn("连接空闲: 不按空闲回收", text)

    async def test_values_are_bounded_integers(self):
        for value in (-1, 3601, True, "60", 1.5, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HotSettings.from_config({"connectionIdleSeconds": value})
        for value in (0, 1, 60, 3600):
            self.assertEqual(HotSettings.from_config({"connectionIdleSeconds": value}).connection_idle_seconds, value)
