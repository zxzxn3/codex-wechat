"""A retained chat must wait for the new binding's context without losing work."""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock

from codex_wechat.app import Bridge
from codex_wechat.storage import Store
from codex_wechat import wechat_auth


class RebindDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.bridge = Bridge(self.store, {"user": "owner", "bot": "new"}, {})
        self.bridge.state.update(context="old", seen=["seen"])
        self.bridge.reply("saved")
        wechat_auth.prepare_rebind(self.bridge.state,
                                   {"user": "owner", "bot": "old"}, self.bridge.account)
        self.bridge.save()
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.close()
        self.temp.cleanup()

    def message(self, user="owner", identity="seen", context="fresh"):
        return {"from_user_id": user, "message_type": 1, "message_id": identity,
                "context_token": context, "item_list": [{"type": 1, "text_item": {"text": "hello"}}]}

    async def test_duplicate_message_refreshes_context_without_reexecuting(self):
        b = self.bridge
        b.reply("while waiting")
        await b.deliver_answer("result while waiting", finish_running=False)
        await b.receive({"msgs": [self.message()]})
        self.assertFalse(b.state.get("wechatContextPending"))
        self.assertEqual(b.state["inbox"], [])
        self.assertEqual([entry["text"] for entry in b.state["outbox"]],
                         ["saved", "while waiting", "result while waiting"])
        self.assertTrue(all(entry["context"] == "fresh" for entry in b.state["outbox"]))
        saved = self.store.load("bridge")
        self.assertNotIn("wechatContextPending", saved)
        self.assertEqual(saved["outbox"], b.state["outbox"])

    async def test_wrong_user_and_missing_context_cannot_release_output(self):
        b = self.bridge
        await b.receive({"msgs": [self.message(user="stranger"), self.message(context="")]})
        self.assertTrue(b.state["wechatContextPending"])
        self.assertEqual(b.state["outbox"][0]["context"], "")

    async def test_sender_waits_then_sends_with_fresh_context(self):
        b = self.bridge
        sent = asyncio.Event()
        async def send(entry):
            self.assertEqual(entry["context"], "fresh")
            sent.set()
        b.channel.send = AsyncMock(side_effect=send)
        b.media.upload = AsyncMock()
        self.tasks.append(asyncio.create_task(b.sender()))
        # Give the sender a scheduling opportunity; no network or upload is allowed.
        await asyncio.sleep(0)
        b.channel.send.assert_not_awaited()
        b.media.upload.assert_not_awaited()
        await b.receive({"msgs": [self.message()]})
        await asyncio.wait_for(sent.wait(), 1)
        await asyncio.wait_for(b.outbox_drained.wait(), 1)
        self.assertEqual(b.state["outbox"], [])

    async def test_wait_survives_restart_and_prevents_input_submission(self):
        b = Bridge(self.store, self.bridge.account, {})
        self.assertTrue(b.state["wechatContextPending"])
        b.state["inbox"].append({"id": "queued", "text": "work"})
        b.backend.run = AsyncMock()
        self.tasks.append(asyncio.create_task(b.worker()))
        await asyncio.sleep(0)
        self.assertEqual(b.state["inbox"][0]["id"], "queued")
        b.backend.run.assert_not_awaited()
