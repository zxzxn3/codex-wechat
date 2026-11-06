"""The user-controlled outbox discard command."""
import tempfile
import unittest
from pathlib import Path

from codex_wechat.app import Bridge
from codex_wechat.commands import dispatch, handle
from codex_wechat.storage import Store


class DiscardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["context"] = "context"

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_discard_clears_pending_output_and_appends_the_confirmation(self):
        bridge = self.bridge
        bridge.reply("one")
        bridge.reply("two")
        bridge.state["failedOutbox"] = [{"id": "historical"}]
        bridge.state["inbox"] = [{"id": "queued"}]
        bridge.state["pendingControls"] = [{"id": "control"}]

        await handle(bridge, "/discard", "")

        self.assertEqual([entry["text"] for entry in bridge.state["outbox"]],
                         ["2 条消息被清空。"])
        self.assertEqual(bridge.state["failedOutbox"], [{"id": "historical"}])
        self.assertEqual(bridge.state["inbox"], [{"id": "queued"}])
        self.assertEqual(bridge.state["pendingControls"], [{"id": "control"}])

    async def test_discard_preserves_the_entry_currently_being_sent(self):
        bridge = self.bridge
        bridge.reply("one")
        bridge.reply("two")
        in_flight = bridge.state["outbox"][0]
        bridge.output._in_flight_id = in_flight["id"]

        await handle(bridge, "/discard", "")

        self.assertEqual([entry["text"] for entry in bridge.state["outbox"]],
                         ["one", "1 条消息被清空。"])

    async def test_discard_releases_a_deferred_pause_after_clearing(self):
        bridge = self.bridge
        bridge.output.paused = True
        bridge.reply("one")
        bridge.reply("two")
        bridge.refresh_reply_budget(resume=False)

        await dispatch(bridge, "/discard")

        self.assertFalse(bridge.output.paused)
        self.assertFalse(bridge.deferred_resume)
        self.assertEqual([entry["text"] for entry in bridge.state["outbox"]],
                         ["2 条消息被清空。"])


if __name__ == "__main__":
    unittest.main()
