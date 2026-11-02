"""Static-concurrency counterexamples for scan-time rebind."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rebind
import wechat_auth
from service_adapter import ServiceState
from storage import Store
from wechat import LoginResult


class FakeAdapter:
    def __init__(self, running=True):
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


class ScanRaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_dir = self.root / ".state"
        self.config = self.root / "config.json"
        self.config.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, bridge, account):
        store = Store(self.state_dir)
        try:
            store.save("account", account)
            store.save("bridge", bridge)
        finally:
            store.close()

    def load(self, name):
        store = Store(self.state_dir, lock=False)
        return store.load(name)

    async def run_rebind(self, login, adapter, *, stop_active=False):
        with patch.object(rebind, "data_root", return_value=self.root):
            return await rebind.run_rebind(
                self.state_dir, self.config, interactive=True,
                adapter=adapter, login_runner=login, stop_active=stop_active)

    def login_that_starts(self, old, new, job):
        async def login(store, **kwargs):
            running = Store(self.state_dir)
            try:
                state = running.load("bridge")
                state["currentInput"] = job
                state["cursor"] = "cursor-during-scan"
                running.save("bridge", state)
            finally:
                running.close()
            return LoginResult(new, old, True)
        return login

    async def test_work_started_during_scan_needs_stop_active(self):
        old = {"token": "old", "user": "u", "bot": "b1"}
        self.seed({"cursor": "c", "inbox": [], "outbox": [],
                   "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}, old)
        job = {"id": "started-during-scan", "phase": "accepted", "clientId": "c"}
        adapter = FakeAdapter()
        with self.assertRaisesRegex(RuntimeError, "--stop-active"):
            await self.run_rebind(self.login_that_starts(
                old, {"token": "new", "user": "u", "bot": "b2"}, job), adapter)
        self.assertEqual(adapter.calls, ["status", "status"])
        self.assertTrue(adapter.running)
        self.assertEqual(self.load("account"), old)
        state = self.load("bridge")
        self.assertEqual(state["currentInput"], job)
        self.assertNotIn("bindingPending", state)
        self.assertNotIn("wechatContextPending", state)

    async def test_other_user_task_started_during_scan_needs_stop_active(self):
        old = {"token": "old", "user": "u1", "bot": "b1"}
        self.seed({"cursor": "c", "inbox": [], "outbox": [],
                   "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}, old)
        job = {"id": "started-during-scan", "phase": "accepted"}
        adapter = FakeAdapter()
        with self.assertRaisesRegex(RuntimeError, "--stop-active"):
            await self.run_rebind(self.login_that_starts(
                old, {"token": "new", "user": "u2", "bot": "b2"}, job), adapter)
        self.assertEqual(adapter.calls, ["status", "status"])
        self.assertEqual(self.load("account"), old)
        self.assertEqual(self.load("bridge")["currentInput"], job)

    async def test_stop_active_allows_explicit_switch_and_quarantines(self):
        old = {"token": "old", "user": "u1", "bot": "b1"}
        self.seed({"cursor": "c", "inbox": [], "outbox": [],
                   "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}, old)
        new = {"token": "new", "user": "u2", "bot": "b2"}
        job = {"id": "started-during-scan", "phase": "accepted"}
        adapter = FakeAdapter()
        await self.run_rebind(self.login_that_starts(old, new, job), adapter,
                              stop_active=True)
        state = self.load("bridge")
        self.assertEqual(self.load("account"), new)
        self.assertNotIn("currentInput", state)
        self.assertEqual(state["inputRecords"][-1]["id"], "started-during-scan")
        self.assertEqual(state["inputRecords"][-1]["phase"], wechat_auth.IDENTITY_CHANGED)
        self.assertEqual(adapter.calls, ["status", "status", "stop", "start"])

    async def test_new_inbox_during_scan_still_commits_without_stop_active(self):
        old = {"token": "old", "user": "u", "bot": "b1"}
        self.seed({"cursor": "c", "inbox": [], "outbox": [],
                   "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}, old)
        new = {"token": "new", "user": "u", "bot": "b2"}

        async def login(store, **kwargs):
            running = Store(self.state_dir)
            try:
                state = running.load("bridge")
                state["inbox"].append({"id": "during-scan"})
                state["outbox"].append({"id": "reply-during-scan", "context": "old"})
                running.save("bridge", state)
            finally:
                running.close()
            return LoginResult(new, old, True)

        adapter = FakeAdapter()
        await self.run_rebind(login, adapter)
        state = self.load("bridge")
        self.assertEqual(self.load("account"), new)
        self.assertEqual(state["inbox"], [{"id": "during-scan"}])
        self.assertEqual(state["outbox"][0]["id"], "reply-during-scan")
        self.assertTrue(state["wechatContextPending"])


class ScanFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_dir = self.root / ".state"
        self.config = self.root / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.old = {"token": "old", "user": "u", "bot": "b1"}
        self.bridge = {"cursor": "c", "inbox": [{"id": "q"}], "outbox": [],
                       "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}
        store = Store(self.state_dir)
        try:
            store.save("account", self.old)
            store.save("bridge", self.bridge)
        finally:
            store.close()

    def tearDown(self):
        self.temp.cleanup()

    def load(self, name):
        store = Store(self.state_dir, lock=False)
        return store.load(name)

    async def run_rebind(self, login, adapter):
        with patch.object(rebind, "data_root", return_value=self.root):
            return await rebind.run_rebind(
                self.state_dir, self.config, interactive=True,
                adapter=adapter, login_runner=login)

    async def test_cancel_during_scan_does_not_stop_or_write(self):
        adapter = FakeAdapter()
        async def login(store, **kwargs):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_rebind(login, adapter)
        self.assertEqual(adapter.calls, ["status"])
        self.assertTrue(adapter.running)
        self.assertEqual(self.load("account"), self.old)
        self.assertEqual(self.load("bridge"), self.bridge)

    async def test_account_changed_during_scan_is_rejected_and_service_restored(self):
        changed = {"token": "third", "user": "u", "bot": "b3"}
        async def login(store, **kwargs):
            writer = Store(self.state_dir)
            try:
                writer.save("account", changed)
            finally:
                writer.close()
            return LoginResult({"token": "new", "user": "u", "bot": "b2"},
                               self.old, True)
        adapter = FakeAdapter()
        with self.assertRaisesRegex(RuntimeError, "扫码期间"):
            await self.run_rebind(login, adapter)
        self.assertEqual(adapter.calls, ["status", "status", "stop", "start"])
        self.assertTrue(adapter.running)
        self.assertEqual(self.load("account"), changed)
        self.assertEqual(self.load("bridge"), self.bridge)

    async def test_stop_failure_restores_service_without_writes(self):
        class FailingStop(FakeAdapter):
            async def stop_and_wait(self):
                self.calls.append("stop")
                raise RuntimeError("stop failed")
        adapter = FailingStop()
        async def login(store, **kwargs):
            return LoginResult({"token": "new", "user": "u", "bot": "b2"},
                               self.old, True)
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            await self.run_rebind(login, adapter)
        self.assertEqual(adapter.calls, ["status", "status", "stop", "start"])
        self.assertTrue(adapter.running)
        self.assertEqual(self.load("account"), self.old)
        self.assertEqual(self.load("bridge"), self.bridge)


class BindedRedirectTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self, statuses, getconfig=None):
            self.statuses = list(statuses)
            self.getconfig = getconfig
            self.calls = []
        async def request(self, endpoint, body=None, *, auth=True, timeout=40):
            self.calls.append(endpoint)
            if endpoint.startswith("ilink/bot/get_bot_qrcode"):
                return {"qrcode": "q", "qrcode_img_content": "https://example.invalid"}
            if endpoint.startswith("ilink/bot/get_qrcode_status"):
                return {"status": self.statuses.pop(0)}
            if endpoint.startswith("ilink/bot/getconfig"):
                if self.getconfig:
                    raise self.getconfig
                return {}
            raise AssertionError(endpoint)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def login(self, client):
        import wechat
        with patch.object(wechat, "WeChat", return_value=client), \
                patch.object(wechat, "print_qr"):
            return await wechat.login(self.store, rebind=True, persist=False)

    async def test_binded_redirect_without_local_credentials_is_not_success(self):
        import wechat
        client = self.Client(["binded_redirect"])
        with self.assertRaisesRegex(RuntimeError, "没有可用凭据"):
            await self.login(client)
        self.assertIsNone(self.store.load("account"))

    async def test_binded_redirect_after_failure_revalidates_local_token(self):
        self.store.save("account", {"token": "t", "user": "u", "bot": "b"})
        self.store.save("bridge", {"wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}})
        import wechat
        client = self.Client(["binded_redirect"], getconfig=RuntimeError("expired"))
        with self.assertRaisesRegex(RuntimeError, "expired"):
            await self.login(client)
        self.assertEqual(self.store.load("account"), {"token": "t", "user": "u", "bot": "b"})


class LockReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_service_can_take_store_lock_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_dir = root / ".state"
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            account = {"token": "t", "user": "u", "bot": "b"}
            store = Store(state_dir)
            try:
                store.save("account", account)
                store.save("bridge", {"wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}})
            finally:
                store.close()

            class LockCheckingAdapter(FakeAdapter):
                async def start(self):
                    self.calls.append("start")
                    probe = Store(state_dir)
                    probe.close()
                    self.running = True

            adapter = LockCheckingAdapter()

            async def login(store_, **kwargs):
                writer = Store(state_dir)
                try:
                    writer.save("account", {"token": "other", "user": "u", "bot": "x"})
                finally:
                    writer.close()
                return LoginResult({"token": "new", "user": "u", "bot": "b2"},
                                   account, True)

            with patch.object(rebind, "data_root", return_value=root):
                with self.assertRaisesRegex(RuntimeError, "扫码期间"):
                    await rebind.run_rebind(
                        state_dir, config, interactive=True,
                        adapter=adapter, login_runner=login)
            self.assertEqual(adapter.calls, ["status", "status", "stop", "start"])
