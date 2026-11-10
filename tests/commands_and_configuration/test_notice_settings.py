"""Live notice updates against private temporary files, no backend requests."""
import json
from pathlib import Path
import tempfile
import unittest

from codex_wechat.app import Bridge
from codex_wechat.commands import SETTINGS, current_setting, dispatch, handle, notice
from codex_wechat.configuration import HotSettings, Notifications, completion_template
from codex_wechat.storage import Store


class NoticeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_notice_views_use_the_settings_subcommands(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "ctx"
                await handle(bridge, "/settings", "ack")
                self.assertIn("`/settings ack {模板}`",
                              bridge.state["outbox"][-1]["text"])
                await handle(bridge, "/settings", "finish")
                self.assertIn("`/settings finish {模板}`",
                              bridge.state["outbox"][-1]["text"])
            finally:
                store.lock.close()

    async def test_chat_update_completion_and_file_reload(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            initial = {"processingNotice": "old", "completionNotice": ""}
            path.write_text(json.dumps(initial))
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, initial, config_path=path)
                bridge.state["context"] = "ctx"
                await handle(bridge, "/settings", "ack hello")
                self.assertEqual(notice(bridge), "hello")
                await handle(bridge, "/settings", "finish done {model}")
                await bridge.deliver_answer("answer", notify_completion=True)
                self.assertEqual(bridge.state["outbox"][-1]["text"], "done 默认模型")
                path.write_text(json.dumps({"processingNotice": "new file", "completionNotice": "file done"}))
                self.assertEqual(notice(bridge), "new file")
                self.assertEqual(completion_template(bridge.config, bridge.state), "file done")
                path.write_text("{invalid")
                self.assertEqual(notice(bridge), "new file")
                await handle(bridge, "/settings", 'finish ""')
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
        from codex_wechat.prompts import SettingsView, settings_summary
        text = settings_summary(SettingsView(
            notice="", attachment_mib=20,
            notifications=Notifications.from_config({"quotaWarningThresholds": {
                "primary": [25, 10, 5],
                "secondary": [50, 25, 10, 5, 1],
            }})))
        self.assertIn("quotaWarningThresholds.primary: [25,10,5]", text)
        self.assertIn("quotaWarningThresholds.secondary: [50,25,10,5,1]", text)


class NumericSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_numeric_setting_round_trips_without_its_own_reader(self):
        # A new numeric setting only needs the table entry to be covered here.
        numeric = {name: setting for name, setting in SETTINGS.items() if setting.numeric}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {}, config_path=path)
                bridge.state["context"] = "ctx"
                for name, setting in numeric.items():
                    with self.subTest(name=name):
                        value = setting.minimum + 1
                        await handle(bridge, "/settings", f"{name} {value}")
                        self.assertEqual(current_setting(bridge, name), value)
                        section = json.loads(path.read_text())
                        for part in setting.path[:-1]:
                            section = section[part]
                        self.assertEqual(section[setting.path[-1]], value)
            finally:
                store.lock.close()

    async def test_numeric_settings_are_persisted_and_applied(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {}, config_path=path)
                bridge.state["context"] = "ctx"
                await handle(bridge, "/settings", "connectionIdleSeconds 5")
                self.assertEqual(bridge.settings.hot.connection_idle_seconds, 5)
                self.assertEqual(json.loads(path.read_text())["connectionIdleSeconds"], 5)
                await handle(bridge, "/settings", "slicing.targetChars 900")
                self.assertEqual(bridge.slicing.target_chars, 900)
                self.assertEqual(json.loads(path.read_text())["slicing"]["targetChars"], 900)
                await handle(bridge, "/settings", "slicing.smart false")
                self.assertEqual(bridge.slicing.mode, "length")
                self.assertIs(json.loads(path.read_text())["slicing"]["smart"], False)
                await handle(bridge, "/settings", "media.maxMiB 30")
                self.assertEqual(bridge.media.limit, 30 * 1024 * 1024)
                self.assertEqual(json.loads(path.read_text())["media"]["maxMiB"], 30)
                await handle(bridge, "/settings", "logs.keep 5")
                self.assertEqual(bridge.settings.hot.logs.keep, 5)
                self.assertEqual(json.loads(path.read_text())["logs"]["keep"], 5)
                await handle(bridge, "/settings", "budgets 7")
                self.assertEqual(bridge.reply_budget(), 7)
                self.assertEqual(json.loads(path.read_text())["sending"]["replyBudget"], 7)
                await handle(bridge, "/settings", "sending.replyBudget 4")
                self.assertEqual(bridge.reply_budget(), 4)
                self.assertEqual(json.loads(path.read_text())["sending"]["replyBudget"], 4)
                await handle(bridge, "/settings", "compactionNotice false")
                self.assertFalse(bridge.notifications.compaction)
                self.assertFalse(json.loads(path.read_text())["compactionNotice"])
                await handle(bridge, "/settings", "sourceHint hello")
                self.assertEqual(bridge.source_hint, "hello")
                self.assertEqual(json.loads(path.read_text())["sourceHint"], "hello")
                await handle(bridge, "/settings", 'sourceHint ""')
                self.assertEqual(bridge.source_hint, "")
                self.assertEqual(json.loads(path.read_text())["sourceHint"], "")
                self.assertIn("sourceHint: \n", bridge.state["outbox"][-1]["text"])
                await dispatch(bridge, "/settings connectionIdleSeconds 3601")
                self.assertIn("3600", bridge.state["outbox"][-1]["text"])
                self.assertEqual(bridge.settings.hot.connection_idle_seconds, 5)
                await dispatch(bridge, "/settings slicing.targetChars nope")
                self.assertIn("80–20000", bridge.state["outbox"][-1]["text"])
                self.assertEqual(bridge.slicing.target_chars, 900)
                await dispatch(bridge, "/settings slicing.smart maybe")
                self.assertIn("用法", bridge.state["outbox"][-1]["text"])
                self.assertEqual(bridge.slicing.mode, "length")
                await dispatch(bridge, "/settings media.maxMiB 101")
                self.assertIn("1–100", bridge.state["outbox"][-1]["text"])
                self.assertEqual(bridge.media.limit, 30 * 1024 * 1024)
                await dispatch(bridge, "/settings compactionNotice on")
                self.assertIn("用法", bridge.state["outbox"][-1]["text"])
                self.assertFalse(bridge.notifications.compaction)
                await dispatch(bridge, "/settings sourceHint nope")
                self.assertEqual(bridge.source_hint, "nope")
                await handle(bridge, "/settings", 'sourceHint ""')
                self.assertEqual(bridge.source_hint, "")
            finally:
                store.lock.close()


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
        from codex_wechat.prompts import SettingsView, settings_summary
        text = settings_summary(SettingsView(notice="", attachment_mib=10, connection_idle_seconds=2))
        self.assertIn("ack: \n", text)
        self.assertIn("connectionIdleSeconds: 2", text)
        text = settings_summary(SettingsView(notice="", attachment_mib=10, connection_idle_seconds=0))
        self.assertIn("connectionIdleSeconds: 0", text)

    async def test_values_are_bounded_integers(self):
        for value in (-1, 3601, True, "60", 1.5, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HotSettings.from_config({"connectionIdleSeconds": value})
        for value in (0, 1, 60, 3600):
            self.assertEqual(HotSettings.from_config({"connectionIdleSeconds": value}).connection_idle_seconds, value)
