"""Live notice updates against private temporary files, no backend requests."""
import json
from pathlib import Path
import tempfile
import unittest

from codex_wechat import Bridge
from commands import handle, notice
from configuration import completion_template
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
                await handle(bridge, "/finish", "text done {模型}")
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
                             compactionNotice=True, quotaWarningThresholds=[25], cwd="/new")
                path.write_text(json.dumps(fresh))
                self.assertEqual(bridge.current_input_hint(), "\n\ncustom hint")
                self.assertEqual(bridge.slicing.target_chars, 900)
                self.assertTrue(bridge.backend.compaction_notice)
                self.assertEqual(bridge.notifications.quota_thresholds, (25,))
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
