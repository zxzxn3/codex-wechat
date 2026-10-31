"""Live notice updates against private temporary files, no backend requests."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_wechat import Bridge
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
