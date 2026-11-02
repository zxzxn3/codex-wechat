"""Rebind orchestration with native service and network boundaries mocked."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import rebind
import wechat_auth
from service_adapter import ServiceState
from storage import Store
from wechat import LoginResult


class FakeAdapter:
    def __init__(self, *, running):
        self.running = running
        self.calls = []

    async def status(self):
        self.calls.append("status")
        return ServiceState(True, self.running)

    async def stop_and_wait(self):
        self.calls.append("stop")
        self.running = False

    async def start(self):
        self.calls.append("start")
        self.running = True


class RebindTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_dir = self.root / ".state"
        self.config_path = self.root / "config.json"
        self.config_path.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, bridge, account=None):
        store = Store(self.state_dir)
        try:
            if account is not None:
                store.save("account", account)
            store.save("bridge", bridge)
        finally:
            store.lock.close()

    def load_bridge(self):
        store = Store(self.state_dir, lock=False)
        return store.load("bridge"), store.load("account")

    async def run_rebind(self, adapter, login_runner, **kwargs):
        with patch.object(rebind, "data_root", return_value=self.root):
            return await rebind.run_rebind(
                self.state_dir,
                self.config_path,
                interactive=True,
                adapter=adapter,
                login_runner=login_runner,
                **kwargs,
            )

    async def test_same_identity_preserves_cursor_inbox_and_outbox(self):
        account = {"token": "old", "bot": "bot-a", "user": "owner"}
        self.seed({
            "cursor": "cursor",
            "inbox": [{"id": "queued"}],
            "outbox": [{"id": "unsent"}],
        }, account)

        async def login(store, **kwargs):
            previous = store.load("account")
            refreshed = {**previous, "token": "new"}
            store.save("account", refreshed)
            return LoginResult(refreshed, previous, False)

        adapter = FakeAdapter(running=True)
        await self.run_rebind(adapter, login, stop_active=True)
        bridge, saved_account = self.load_bridge()
        self.assertEqual(saved_account["token"], "new")
        self.assertEqual(bridge["cursor"], "cursor")
        self.assertEqual(bridge["inbox"], [{"id": "queued"}])
        self.assertEqual(bridge["outbox"], [{"id": "unsent"}])
        self.assertEqual(bridge["wechatAuth"]["state"], wechat_auth.AUTH_VALID)
        self.assertEqual(adapter.calls, ["status", "stop", "start"])

    async def test_identity_change_quarantines_old_chat_data(self):
        old_account = {"token": "old", "bot": "old-bot", "user": "old-user"}
        self.seed({
            "cursor": "cursor",
            "seen": ["old"],
            "context": "old-context",
            "currentInput": {"id": "current"},
            "inbox": [{"id": "queued"}],
            "outbox": [{"id": "unsent"}],
            "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED},
        }, old_account)

        async def login(store, *, rebind=False,
                        before_identity_change=None):
            previous = store.load("account")
            account = {"token": "new", "bot": "new-bot", "user": "new-user"}
            self.assertTrue(rebind)
            before_identity_change(previous, account)
            store.save("account", account)
            return LoginResult(account, previous, True)

        adapter = FakeAdapter(running=True)
        await self.run_rebind(
            adapter, login, stop_active=True
        )
        bridge, account = self.load_bridge()
        self.assertEqual(account["user"], "new-user")
        self.assertEqual(bridge["cursor"], "")
        self.assertEqual(bridge["seen"], [])
        self.assertNotIn("context", bridge)
        self.assertNotIn("currentInput", bridge)
        self.assertEqual(bridge["inbox"], [])
        self.assertEqual(bridge["outbox"], [])
        self.assertEqual(
            [item["phase"] for item in bridge["inputRecords"]],
            [wechat_auth.IDENTITY_CHANGED, wechat_auth.IDENTITY_CHANGED],
        )
        self.assertEqual(bridge["failedOutbox"][-1]["status"],
                         wechat_auth.IDENTITY_CHANGED)

    async def test_active_work_requires_explicit_stop(self):
        self.seed({
            "currentInput": {"id": "working"},
            "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED},
        })
        adapter = FakeAdapter(running=True)
        login = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "--stop-active"):
            await self.run_rebind(adapter, login)
        self.assertEqual(adapter.calls, ["status"])
        login.assert_not_awaited()

    async def test_login_failure_restores_original_running_service(self):
        self.seed({
            "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED},
        })
        adapter = FakeAdapter(running=True)
        login = AsyncMock(side_effect=RuntimeError("offline"))
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await self.run_rebind(adapter, login)
        self.assertEqual(adapter.calls, ["status", "stop", "start"])
        self.assertTrue(adapter.running)


if __name__ == "__main__":
    unittest.main()
