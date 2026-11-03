"""Independent counterexamples for the rebind rename and identity policy."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from pathlib import PureWindowsPath

import rebind
import wechat
import wechat_auth
from tests.http_fixtures import exchange
from codex_wechat import Bridge, startup_account
from configuration import ConfigurationError
from errors import WeChatAuthExpired
from service_adapter import ServiceState
from storage import Store
from wechat import LoginResult


class RebindCommandTests(unittest.TestCase):
    def test_linux_command_is_shell_safe_and_round_trips(self):
        root = Path("/opt/Agent WeChat/it's here")
        with patch.object(wechat_auth, "IS_WINDOWS", False), \
                patch.object(wechat_auth, "INSTALL_ROOT", root):
            command = wechat_auth.rebind_command()
        self.assertEqual(shlex.split(command), [str(root / "codex-wechat"), "rebind"])
        self.assertIn("'", command)

    def test_windows_command_uses_powershell_call_operator_and_escaping(self):
        root = PureWindowsPath(r"C:\Users\O'Brien\AppData\Local\codex-wechat")
        with patch.object(wechat_auth, "IS_WINDOWS", True), \
                patch.object(wechat_auth, "INSTALL_ROOT", root):
            command = wechat_auth.rebind_command()
        expected = "& 'C:\\Users\\O''Brien\\AppData\\Local\\codex-wechat\\codex-wechat.ps1' rebind"
        self.assertEqual(command, expected)


class FakeAdapter:
    def __init__(self, running):
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


class RunRebindTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_dir = self.root / ".state"
        self.config = self.root / "config.json"
        self.config.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, bridge, account=None):
        store = Store(self.state_dir)
        try:
            if account is not None:
                store.save("account", account)
            store.save("bridge", bridge)
        finally:
            store.close()

    def load(self, name):
        store = Store(self.state_dir, lock=False)
        return store.load(name)

    async def run_rebind(self, adapter, login, **kwargs):
        with patch.object(rebind, "data_root", return_value=self.root):
            return await rebind.run_rebind(
                self.state_dir, self.config, interactive=True,
                adapter=adapter, login_runner=login, **kwargs)

    async def test_noninteractive_refuses_before_touching_state_or_service(self):
        adapter = FakeAdapter(running=True)
        with self.assertRaisesRegex(ValueError, "交互终端"):
            with patch.object(rebind, "data_root", return_value=self.root):
                await rebind.run_rebind(
                    self.state_dir, self.config, interactive=False, adapter=adapter)
        self.assertEqual(adapter.calls, [])
        self.assertFalse(self.state_dir.exists())

    async def test_running_and_restart_requested_need_stop_active(self):
        for key in ("running", "restartRequested"):
            with self.subTest(key=key):
                self.seed({key: True})
                adapter = FakeAdapter(running=True)
                login = AsyncMock()
                with self.assertRaisesRegex(RuntimeError, r"(?:--stop-active|-StopActive)"):
                    await self.run_rebind(adapter, login)
                self.assertEqual(adapter.calls, ["status"])
                login.assert_not_awaited()

    async def test_queued_inbox_alone_does_not_block_rebind(self):
        self.seed({"inbox": [{"id": "queued"}], "outbox": [{"id": "unsent"}]},
                  {"token": "t", "user": "u", "bot": "b"})
        async def login(store, **kwargs):
            return LoginResult(store.load("account"), store.load("account"), False)
        adapter = FakeAdapter(running=False)
        await self.run_rebind(adapter, login)
        bridge = self.load("bridge")
        self.assertEqual(bridge["inbox"], [{"id": "queued"}])
        self.assertEqual(bridge["outbox"], [{"id": "unsent"}])
        self.assertEqual(adapter.calls, ["status", "status"])

    async def test_failure_keeps_credentials_and_bridge_state(self):
        account = {"token": "t", "user": "u", "bot": "b"}
        bridge = {"cursor": "c", "inbox": [{"id": "i"}], "outbox": [{"id": "o"}],
                  "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}
        self.seed(bridge, account)
        adapter = FakeAdapter(running=True)
        async def login(store, **kwargs):
            raise RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await self.run_rebind(adapter, login)
        self.assertEqual(self.load("account"), account)
        self.assertEqual(self.load("bridge"), bridge)
        self.assertEqual(adapter.calls, ["status"])

    async def test_pending_target_mismatch_does_not_clear_or_mark_valid(self):
        account = {"token": "t", "user": "u", "bot": "b"}
        bridge = {"bindingPending": {"target": {"bot": "other", "user": "u"}},
                  "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}
        self.seed(bridge, account)
        async def login(store, **kwargs):
            return LoginResult(account, account, False)
        adapter = FakeAdapter(running=False)
        with self.assertRaisesRegex(RuntimeError, "未完成记录"):
            await self.run_rebind(adapter, login)
        saved = self.load("bridge")
        self.assertEqual(saved["bindingPending"], bridge["bindingPending"])
        self.assertEqual(saved["wechatAuth"]["state"], wechat_auth.AUTH_REQUIRED)

    async def test_same_user_bot_change_preserves_delivery_and_clears_pending(self):
        old = {"token": "old", "user": "u", "bot": "b1"}
        self.seed({"cursor": "c", "inbox": [{"id": "i"}],
                   "outbox": [{"id": "o"}],
                   "thread": "thread-keep",
                   "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}, old)
        new = {"token": "new", "user": "u", "bot": "b2"}
        async def login(store, *, rebind=False, persist=True):
            previous = store.load("account")
            self.assertTrue(rebind)
            self.assertFalse(persist)
            return LoginResult(new, previous, True)
        adapter = FakeAdapter(running=False)
        await self.run_rebind(adapter, login)
        bridge = self.load("bridge")
        self.assertNotIn("bindingPending", bridge)
        self.assertEqual(bridge["thread"], "thread-keep")
        self.assertEqual(bridge["wechatAuth"]["state"], wechat_auth.AUTH_VALID)
        self.assertEqual(bridge["inbox"], [{"id": "i"}])
        self.assertEqual(bridge["outbox"], [{"id": "o", "context": ""}])
        self.assertEqual(bridge["cursor"], "")
        self.assertTrue(bridge["wechatContextPending"])
        self.assertEqual(self.load("account"), new)


class StartupPendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_rebind_blocks_start_and_points_at_rebind_command(self):
        account = {"token": "t", "user": "u", "bot": "b"}
        state = {"bindingPending": {"target": {"user": "u", "bot": "b2"}}}
        store = Mock(load=Mock(side_effect=lambda name, default=None: account if name == "account" else state))
        with patch("codex_wechat.login", AsyncMock()) as login:
            with self.assertRaises(ConfigurationError) as caught:
                await startup_account(store)
        self.assertIn("rebind", str(caught.exception))
        login.assert_not_called()


class RequestClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, *, auth, payload, status=200, returncode=0):
        output = json.dumps(payload).encode() + b"\n" + str(status).encode()
        with exchange(output, code=returncode):
            client = wechat.WeChat({"token": "t", "base_url": "https://ilinkai.weixin.qq.com"})
            return await client.request("ilink/bot/getupdates", {}, auth=auth)

    async def test_auth_minus_14_is_the_only_auth_expiry(self):
        with self.assertRaises(WeChatAuthExpired):
            await self.request(auth=True, payload={"ret": -14})
        with self.assertRaises(RuntimeError) as caught:
            await self.request(auth=False, payload={"ret": -14})
        self.assertNotIsInstance(caught.exception, WeChatAuthExpired)
        with self.assertRaises(RuntimeError) as caught:
            await self.request(auth=True, payload={"errcode": 1})
        self.assertNotIsInstance(caught.exception, WeChatAuthExpired)

    async def test_http_and_network_errors_are_not_auth_expiry(self):
        for kwargs in ({"status": 500, "payload": {"ret": -14}},
                       {"returncode": 7, "payload": {}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError) as caught:
                await self.request(auth=True, **kwargs)
            self.assertNotIsInstance(caught.exception, WeChatAuthExpired)


class PollerNetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.channel = Mock()
        self.bridge = Bridge(self.store, {"token": "t", "user": "u", "bot": "b"},
                             {}, channel=self.channel)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_network_error_does_not_pause_or_notify(self):
        called = asyncio.Event()
        async def poll(cursor):
            called.set()
            raise RuntimeError("network down")
        self.channel.poll = poll
        with patch("codex_wechat.service_adapter.notify", AsyncMock()) as notify:
            task = asyncio.create_task(self.bridge.poller())
            await asyncio.wait_for(called.wait(), 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.bridge.wechat_paused)
        notify.assert_not_awaited()
        self.assertNotIn("wechatAuth", self.bridge.state)


class EntryRetirementTests(unittest.TestCase):
    def test_python_entry_rejects_old_action_and_flag(self):
        root = Path(__file__).resolve().parent.parent
        for arguments in (["relink"], ["rebind", "--replace-identity"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-B", str(root / "codex_wechat.py"), *arguments],
                    capture_output=True, text=True, timeout=20,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_scripts_expose_rebind_and_no_legacy_names(self):
        root = Path(__file__).resolve().parent.parent
        linux = (root / "codex-wechat").read_text(encoding="utf-8")
        windows = (root / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertNotIn("relink", linux)
        self.assertNotIn("relink", windows)
        self.assertIn("'rebind'", windows)
        self.assertNotIn("ReplaceIdentity", windows)


if __name__ == "__main__":
    unittest.main()
