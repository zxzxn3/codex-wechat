"""Rebind orchestration with native service and network boundaries mocked."""
import os
from pathlib import PureWindowsPath
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat import rebind
from codex_wechat import wechat_auth
from codex_wechat.app import startup_account
from codex_wechat.configuration import ConfigurationError
from codex_wechat.storage import Store
from codex_wechat.wechat import LoginResult
from tests.support.fakes import FakeServiceAdapter
from tests.support.helpers import outbox_entry, runtime_paths_at


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
        bridge.setdefault("version", 1)
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
        with patch.object(rebind, "runtime_paths", return_value=runtime_paths_at(
                self.root, config_file=self.config_path, state_dir=self.state_dir)):
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
            "outbox": [outbox_entry("unsent", text="unsent")],
        }, account)

        async def login(store, **kwargs):
            previous = store.load("account")
            refreshed = {**previous, "token": "new"}
            self.assertFalse(kwargs["persist"])
            return LoginResult(refreshed, previous, False)

        adapter = FakeServiceAdapter(running=True)
        await self.run_rebind(adapter, login, stop_active=True)
        bridge, saved_account = self.load_bridge()
        self.assertEqual(saved_account["token"], "new")
        self.assertEqual(bridge["cursor"], "cursor")
        self.assertEqual(bridge["inbox"], [{"id": "queued"}])
        self.assertEqual(bridge["outbox"], [outbox_entry("unsent", text="unsent")])
        self.assertEqual(bridge["wechatAuth"]["state"], wechat_auth.AUTH_VALID)
        self.assertEqual(adapter.calls, ["status", "status", "stop", "start"])

    async def test_identity_change_quarantines_old_chat_data(self):
        old_account = {"token": "old", "bot": "old-bot", "user": "old-user"}
        self.seed({
            "cursor": "cursor",
            "seen": ["old"],
            "context": "old-context",
            "currentInput": {"id": "current"},
            "inbox": [{"id": "queued"}],
            "outbox": [outbox_entry(
                "unsent", user="old-user", context="old-context", text="unsent")],
            "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED},
        }, old_account)

        async def login(store, *, rebind=False, persist=True):
            previous = store.load("account")
            account = {"token": "new", "bot": "new-bot", "user": "new-user"}
            self.assertTrue(rebind)
            self.assertFalse(persist)
            return LoginResult(account, previous, True)

        adapter = FakeServiceAdapter(running=True)
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
        adapter = FakeServiceAdapter(running=True)
        login = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, r"(?:--stop-active|-StopActive)"):
            await self.run_rebind(adapter, login)
        self.assertEqual(adapter.calls, ["status"])
        login.assert_not_awaited()

    async def test_login_failure_does_not_stop_running_service(self):
        self.seed({
            "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED},
        })
        adapter = FakeServiceAdapter(running=True)
        login = AsyncMock(side_effect=RuntimeError("offline"))
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await self.run_rebind(adapter, login)
        self.assertEqual(adapter.calls, ["status"])
        self.assertTrue(adapter.running)

    async def test_scan_wait_keeps_service_running_and_commit_loads_latest_state(self):
        account = {"token": "old", "bot": "bot-a", "user": "owner"}
        self.seed({"cursor": "cursor", "inbox": [], "outbox": []}, account)
        adapter = FakeServiceAdapter(running=True)

        async def login(store, *, rebind=False, persist=True):
            self.assertFalse(persist)
            self.assertTrue(adapter.running)
            self.assertEqual(adapter.calls, ["status"])
            # A concurrently running bridge can still lock and save during QR waiting.
            current = Store(self.state_dir)
            try:
                state = current.load("bridge")
                state["inbox"].append({"id": "during-scan"})
                current.save("bridge", state)
            finally:
                current.close()
            return LoginResult({**account, "token": "new"}, account, False)

        await self.run_rebind(adapter, login)
        bridge, saved = self.load_bridge()
        self.assertEqual(saved["token"], "new")
        self.assertEqual(bridge["inbox"], [{"id": "during-scan"}])
        self.assertEqual(adapter.calls, ["status", "status", "stop", "start"])


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
        expected = (
            "& 'C:\\Users\\O''Brien\\AppData\\Local\\codex-wechat\\"
            "codex-wechat.ps1' rebind")
        self.assertEqual(command, expected)


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
        bridge.setdefault("version", 1)
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
        with patch.object(rebind, "runtime_paths", return_value=runtime_paths_at(
                self.root, config_file=self.config, state_dir=self.state_dir)):
            return await rebind.run_rebind(
                self.state_dir, self.config, interactive=True,
                adapter=adapter, login_runner=login, **kwargs)

    async def test_noninteractive_refuses_before_touching_state_or_service(self):
        adapter = FakeServiceAdapter(running=True)
        with self.assertRaisesRegex(ValueError, "交互终端"):
            with patch.object(rebind, "runtime_paths", return_value=runtime_paths_at(
                    self.root, config_file=self.config, state_dir=self.state_dir)):
                await rebind.run_rebind(
                    self.state_dir, self.config, interactive=False, adapter=adapter)
        self.assertEqual(adapter.calls, [])
        self.assertFalse(self.state_dir.exists())

    async def test_running_and_restart_requested_need_stop_active(self):
        for key in ("running", "restartRequested"):
            with self.subTest(key=key):
                self.seed({key: True})
                adapter = FakeServiceAdapter(running=True)
                login = AsyncMock()
                with self.assertRaisesRegex(RuntimeError, r"(?:--stop-active|-StopActive)"):
                    await self.run_rebind(adapter, login)
                self.assertEqual(adapter.calls, ["status"])
                login.assert_not_awaited()

    async def test_queued_inbox_alone_does_not_block_rebind(self):
        self.seed({"inbox": [{"id": "queued"}],
                   "outbox": [outbox_entry("unsent", text="unsent")]},
                  {"token": "t", "user": "u", "bot": "b"})

        async def login(store, **kwargs):
            return LoginResult(store.load("account"), store.load("account"), False)

        adapter = FakeServiceAdapter(running=False)
        await self.run_rebind(adapter, login)
        bridge = self.load("bridge")
        self.assertEqual(bridge["inbox"], [{"id": "queued"}])
        self.assertEqual(bridge["outbox"], [outbox_entry("unsent", text="unsent")])
        self.assertEqual(adapter.calls, ["status", "status"])

    async def test_failure_keeps_credentials_and_bridge_state(self):
        account = {"token": "t", "user": "u", "bot": "b"}
        bridge = {"cursor": "c", "inbox": [{"id": "i"}],
                  "outbox": [outbox_entry("o", text="queued")],
                  "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}
        self.seed(bridge, account)
        adapter = FakeServiceAdapter(running=True)

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

        adapter = FakeServiceAdapter(running=False)
        with self.assertRaisesRegex(RuntimeError, "未完成记录"):
            await self.run_rebind(adapter, login)
        saved = self.load("bridge")
        self.assertEqual(saved["bindingPending"], bridge["bindingPending"])
        self.assertEqual(saved["wechatAuth"]["state"], wechat_auth.AUTH_REQUIRED)

    async def test_same_user_bot_change_preserves_delivery_and_clears_pending(self):
        old = {"token": "old", "user": "u", "bot": "b1"}
        self.seed({"cursor": "c", "inbox": [{"id": "i"}],
                   "outbox": [outbox_entry("o", text="queued")],
                   "thread": "thread-keep",
                   "wechatAuth": {"state": wechat_auth.AUTH_REQUIRED}}, old)
        new = {"token": "new", "user": "u", "bot": "b2"}

        async def login(store, *, rebind=False, persist=True):
            previous = store.load("account")
            self.assertTrue(rebind)
            self.assertFalse(persist)
            return LoginResult(new, previous, True)

        adapter = FakeServiceAdapter(running=False)
        await self.run_rebind(adapter, login)
        bridge = self.load("bridge")
        self.assertNotIn("bindingPending", bridge)
        self.assertEqual(bridge["thread"], "thread-keep")
        self.assertEqual(bridge["wechatAuth"]["state"], wechat_auth.AUTH_VALID)
        self.assertEqual(bridge["inbox"], [{"id": "i"}])
        self.assertEqual(bridge["outbox"], [
            {**outbox_entry("o", text="queued"), "context": ""}
        ])
        self.assertEqual(bridge["cursor"], "")
        self.assertTrue(bridge["wechatContextPending"])
        self.assertEqual(self.load("account"), new)


class StartupPendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_rebind_blocks_start_and_points_at_rebind_command(self):
        account = {"token": "t", "user": "u", "bot": "b"}
        state = {
            "version": 1,
            "bindingPending": {"target": {"user": "u", "bot": "b2"}},
        }
        store = Mock(load=Mock(
            side_effect=lambda name, default=None: account if name == "account" else state))
        with patch("codex_wechat.app.login", AsyncMock()) as login:
            with self.assertRaises(ConfigurationError) as caught:
                await startup_account(store)
        self.assertIn("rebind", str(caught.exception))
        login.assert_not_called()


class EntryRetirementTests(unittest.TestCase):
    def test_python_entry_rejects_old_action_and_flag(self):
        root = Path(__file__).resolve().parents[2]
        for arguments in (["relink"], ["rebind", "--replace-identity"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "codex_wechat", *arguments],
                    capture_output=True, text=True, timeout=20,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                         "PYTHONPATH": str(root)})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_scripts_expose_rebind_and_no_legacy_names(self):
        root = Path(__file__).resolve().parents[2]
        linux = (root / "codex-wechat").read_text(encoding="utf-8")
        windows = (root / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertNotIn("relink", linux)
        self.assertNotIn("relink", windows)
        self.assertIn("'rebind'", windows)
        self.assertNotIn("ReplaceIdentity", windows)


if __name__ == "__main__":
    unittest.main()
