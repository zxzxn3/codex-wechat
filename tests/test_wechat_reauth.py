"""Authentication-expiry handling in the bridge; no real WeChat requests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from codex_wechat import wechat_auth
from codex_wechat.app import Bridge, status_payload
from codex_wechat.errors import WeChatAuthExpired
from codex_wechat.storage import Store


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
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock()) as notify:
            task = asyncio.create_task(bridge.sender())
            self.tasks.append(task)
            await wait_until(lambda: bridge.wechat_paused)
            await bridge.require_wechat_rebind()
        saved = self.store.load("bridge")
        self.assertEqual(saved["wechatAuth"]["state"], wechat_auth.AUTH_REQUIRED)
        self.assertEqual(saved["outbox"][0]["id"], original["id"])
        self.assertNotIn("attempts", saved["outbox"][0])
        self.assertEqual(saved["consecutiveSendFailures"], 0)
        notify.assert_awaited_once()

    async def test_poll_expiry_enters_same_paused_state(self):
        self.channel.poll.side_effect = WeChatAuthExpired()
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock()) as notify:
            task = asyncio.create_task(self.bridge.poller())
            self.tasks.append(task)
            await wait_until(lambda: self.bridge.wechat_paused)
        saved = self.store.load("bridge")
        self.assertEqual(saved["wechatAuth"]["state"], wechat_auth.AUTH_REQUIRED)
        self.assertTrue(self.bridge.wechat_paused)
        notify.assert_awaited_once()

    async def test_old_notice_gets_one_updated_rebind_command(self):
        self.bridge.state["wechatAuth"] = {"state": wechat_auth.AUTH_REQUIRED,
                                           "notificationAttempted": True}
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock()) as notify:
            await self.bridge.require_wechat_rebind()
            await self.bridge.require_wechat_rebind()
        notify.assert_awaited_once()
        self.assertIn(wechat_auth.rebind_command(), notify.call_args.args[1])
        self.assertIn("rebind", notify.call_args.args[1])

    async def test_local_status_exposes_recovery_command(self):
        self.bridge.state["outbox"] = [{"id": "still-unsent"}]
        wechat_auth.require_rebind(
            self.bridge.state, pending=1, now=123
        )
        self.store.save("account", self.bridge.account)
        self.store.save("bridge", self.bridge.state)
        payload = status_payload(self.store)
        self.assertTrue(payload["rebind_required"])
        self.assertFalse(payload["available"])
        self.assertEqual(payload["rebind_command"], wechat_auth.rebind_command())
        self.assertEqual(payload["unsent"], 1)
        self.assertEqual(payload["auth_detected_at"], 123)

    async def test_notice_retries_false_then_persists_success(self):
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock(
                side_effect=[False, False, True])) as notify, \
                patch("codex_wechat.app.asyncio.sleep", AsyncMock()) as sleep:
            await self.bridge.require_wechat_rebind()
            await self.bridge.require_wechat_rebind()
        self.assertEqual(notify.await_count, 3)
        self.assertEqual([call.args for call in sleep.await_args_list], [(5,), (5,)])
        self.assertTrue(self.store.load("bridge")["wechatAuth"]["notificationDelivered"])

    async def test_failed_notice_budget_resets_on_process_restart(self):
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock(
                return_value=False)) as notify, \
                patch("codex_wechat.app.asyncio.sleep", AsyncMock()) as sleep:
            await asyncio.gather(self.bridge.require_wechat_rebind(),
                                 self.bridge.require_wechat_rebind())
        self.assertEqual(notify.await_count, 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertNotIn("notificationDelivered", self.store.load("bridge")["wechatAuth"])
        restarted = Bridge(self.store, self.bridge.account, {}, channel=self.channel)
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock(return_value=True)) as notify:
            await restarted.require_wechat_rebind()
        notify.assert_awaited_once()

    async def test_notice_exception_uses_same_retry_budget(self):
        with patch("codex_wechat.app.service_adapter.notify", AsyncMock(
                side_effect=[RuntimeError("fixture"), True])) as notify, \
                patch("codex_wechat.app.asyncio.sleep", AsyncMock()) as sleep:
            await self.bridge.require_wechat_rebind()
        self.assertEqual(notify.await_count, 2)
        sleep.assert_awaited_once_with(5)


if __name__ == "__main__":
    unittest.main()
