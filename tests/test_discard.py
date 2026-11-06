"""The user-controlled outbox discard command."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    async def test_d_short_alias_dispatches_discard(self):
        bridge = self.bridge
        bridge.reply("one")

        await dispatch(bridge, "/d")

        self.assertEqual([entry["text"] for entry in bridge.state["outbox"]],
                         ["1 条消息被清空。"])

    @staticmethod
    def _text_message(message_id, text):
        return {
            "message_id": message_id,
            "from_user_id": "owner",
            "message_type": 1,
            "context_token": "context",
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }

    @staticmethod
    def _capture_sends(bridge):
        sent = []

        async def send(entry):
            sent.append(entry["text"])
            return {"message_id": len(sent)}

        bridge.channel.send = send
        return sent

    async def test_discard_gate_survives_a_later_message_in_the_same_batch(self):
        bridge = self.bridge
        bridge.output.paused = True
        bridge.reply("old finish")
        sent = self._capture_sends(bridge)
        sender = asyncio.create_task(bridge.sender())
        worker = None
        try:
            with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
                await asyncio.sleep(0.01)
                self.assertEqual(sent, [])

                await bridge.receive({"msgs": [
                    self._text_message("discard", "/d"),
                    self._text_message("later", "later message"),
                ]})
                await asyncio.sleep(0.01)
                self.assertEqual(sent, [])

                worker = asyncio.create_task(bridge.control_worker("local"))
                for _ in range(200):
                    if sent:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(sent, ["1 条消息被清空。"])
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def test_discard_gate_waits_for_a_busy_local_lane(self):
        bridge = self.bridge
        bridge.reply("old finish")
        blocking = {
            "id": "blocking",
            "text": "/help",
            "received": {"controlId": "blocking", "request_id": "blocking"},
            "state": "queued",
            "lane": "local",
        }
        bridge.state["pendingControls"] = [blocking]
        self.assertTrue(bridge.enqueue_control(blocking))
        entered = asyncio.Event()
        release = asyncio.Event()
        original_control = bridge.control

        async def control(text, *, received=None):
            if text.startswith("/help"):
                entered.set()
                await release.wait()
                return
            await original_control(text, received=received)

        bridge.control = control
        worker = asyncio.create_task(bridge.control_worker("local"))
        sender = None
        try:
            await entered.wait()
            await bridge.receive({"msgs": [self._text_message("discard", "/d")]})
            sent = self._capture_sends(bridge)
            with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
                sender = asyncio.create_task(bridge.sender())
                await asyncio.sleep(0.01)
                self.assertEqual(sent, [])

                release.set()
                for _ in range(200):
                    if sent:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(sent, ["1 条消息被清空。"])
        finally:
            release.set()
            if sender is not None:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_restored_discard_keeps_the_sender_gated(self):
        temp = tempfile.TemporaryDirectory()
        store = Store(Path(temp.name) / "state")
        bridge = Bridge(store, {"user": "owner"}, {})
        bridge.state["context"] = "context"
        bridge.reply("old finish")
        bridge.state["pendingControls"] = [{
            "id": "discard",
            "text": "/d",
            "received": {"controlId": "discard", "request_id": "discard"},
            "state": "queued",
            "lane": "local",
        }]
        bridge.save()
        store.lock.close()

        store = Store(Path(temp.name) / "state")
        restarted = Bridge(store, {"user": "owner"}, {})
        restarted.restore_controls()
        sent = self._capture_sends(restarted)
        sender = asyncio.create_task(restarted.sender())
        worker = None
        try:
            with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
                await asyncio.sleep(0.01)
                self.assertEqual(sent, [])

                worker = asyncio.create_task(restarted.control_worker("local"))
                for _ in range(200):
                    if sent:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(sent, ["1 条消息被清空。"])
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            store.lock.close()
            temp.cleanup()

    async def _assert_command_clears_backlog_before_sender(self, command):
        temp = tempfile.TemporaryDirectory()
        store = Store(Path(temp.name) / "state")
        bridge = Bridge(store, {"user": "owner"}, {})
        bridge.state["context"] = "context"
        bridge.reply("old finish")
        for _ in range(bridge.reply_budget()):
            bridge.spend_reply_budget()
        bridge.state["budgetPromptSent"] = True
        sent = []

        async def send(entry):
            sent.append(entry["text"])
            return {"message_id": len(sent)}

        bridge.channel.send = send
        message = {
            "message_id": "discard",
            "from_user_id": "owner",
            "message_type": 1,
            "context_token": "context",
            "item_list": [{"type": 1, "text_item": {"text": command}}],
        }
        try:
            with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
                sender = asyncio.create_task(bridge.sender())
                worker = None
                try:
                    await asyncio.sleep(0.01)
                    self.assertEqual(sent, [])

                    await bridge.receive({"msgs": [message]})
                    await asyncio.sleep(0.01)
                    self.assertEqual(sent, [])

                    worker = asyncio.create_task(bridge.control_worker("local"))
                    for _ in range(200):
                        if sent:
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(sent, ["1 条消息被清空。"])
                finally:
                    if worker is not None:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
        finally:
            store.lock.close()
            temp.cleanup()

    async def test_discard_and_alias_clear_backlog_before_the_sender_can_wake(self):
        for command in ("/discard", "/d"):
            with self.subTest(command=command):
                await self._assert_command_clears_backlog_before_sender(command)


if __name__ == "__main__":
    unittest.main()
