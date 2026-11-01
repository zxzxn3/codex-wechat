"""Authentication-expiry handling in the bridge; no real WeChat requests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import wechat_auth
from codex_wechat import Bridge, status_payload
from errors import WeChatAuthExpired
from storage import Store


async def wait_until(predicate, timeout=1):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


class WeChatReauthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.channel = SimpleNamespace(poll=AsyncMock(), send=AsyncMock())
        self.bridge = Bridge(
            self.store,
            {"token": "fixture", "bot": "bot-a", "user": "owner"},
            {},
            channel=self.channel,
        )
        self.bridge.state["context"] = "context"
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.lock.close()
        self.temp.cleanup()

    async def test_send_expiry_pauses_without_consuming_retry_attempt(self):
        bridge = self.bridge
        bridge.reply("pending")
        original = dict(bridge.state["outbox"][0])
        self.channel.send.side_effect = WeChatAuthExpired()
        with patch("codex_wechat.service_adapter.notify", AsyncMock()) as notify:
            task = asyncio.create_task(bridge.sender())
            self.tasks.append(task)
            await wait_until(lambda: bridge.wechat_paused)
            await bridge.require_wechat_relink()
        saved = self.store.load("bridge")
        self.assertEqual(saved["wechatAuth"]["state"], wechat_auth.AUTH_REQUIRED)
        self.assertEqual(saved["outbox"][0]["id"], original["id"])
        self.assertNotIn("attempts", saved["outbox"][0])
        self.assertEqual(saved["consecutiveSendFailures"], 0)
        notify.assert_awaited_once()

    async def test_poll_expiry_enters_same_paused_state(self):
        self.channel.poll.side_effect = WeChatAuthExpired()
        with patch("codex_wechat.service_adapter.notify", AsyncMock()) as notify:
            task = asyncio.create_task(self.bridge.poller())
            self.tasks.append(task)
            await wait_until(lambda: self.bridge.wechat_paused)
        saved = self.store.load("bridge")
        self.assertEqual(saved["wechatAuth"]["state"], wechat_auth.AUTH_REQUIRED)
        self.assertTrue(self.bridge.wechat_paused)
        notify.assert_awaited_once()

    async def test_local_status_exposes_recovery_command(self):
        self.bridge.state["outbox"] = [{"id": "still-unsent"}]
        wechat_auth.require_relink(
            self.bridge.state, pending=1, now=123
        )
        self.store.save("account", self.bridge.account)
        self.store.save("bridge", self.bridge.state)
        payload = status_payload(self.store)
        self.assertTrue(payload["relink_required"])
        self.assertFalse(payload["available"])
        self.assertEqual(payload["relink_command"], "codex-wechat relink")
        self.assertEqual(payload["unsent"], 1)
        self.assertEqual(payload["auth_detected_at"], 123)


if __name__ == "__main__":
    unittest.main()
