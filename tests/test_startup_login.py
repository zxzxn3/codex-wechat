"""First-launch login is interactive only; never contact the real WeChat API."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat import (
    restart_background_service,
    start_background_service,
    startup_account,
)
from configuration import ConfigurationError


class StartupLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_start_uses_the_same_binding_gate(self):
        store = Mock()
        events = []
        store.close.side_effect = lambda: events.append("closed")
        with patch("codex_wechat.service_adapter.status",
                   AsyncMock(return_value=SimpleNamespace(
                       installed=True, running=False))), \
             patch("codex_wechat.startup_store", AsyncMock(return_value=store)), \
             patch("codex_wechat.startup_account", AsyncMock()) as account, \
             patch("codex_wechat.service_adapter.start",
                   AsyncMock(side_effect=lambda: events.append("started"))) as start, \
             patch("codex_wechat.show_log_path") as log_hint:
            await start_background_service(SimpleNamespace(action="service-start"))
        account.assert_awaited_once_with(store)
        start.assert_awaited_once()
        log_hint.assert_called_once_with()
        store.close.assert_called_once()
        self.assertEqual(events, ["closed", "started"])

    async def test_service_start_is_a_noop_when_the_service_is_running(self):
        with patch("codex_wechat.service_adapter.status",
                   AsyncMock(return_value=SimpleNamespace(
                       installed=True, running=True))), \
             patch("codex_wechat.startup_store", AsyncMock()) as store, \
             patch("codex_wechat.service_adapter.start", AsyncMock()) as start, \
             patch("codex_wechat.show_log_path") as log_hint:
            await start_background_service(SimpleNamespace(action="service-start"))
        store.assert_not_awaited()
        start.assert_not_awaited()
        log_hint.assert_called_once_with()

    async def test_restart_prints_the_log_location(self):
        with patch("codex_wechat.service_adapter.restart", AsyncMock()) as restart, \
             patch("codex_wechat.show_log_path") as log_hint, \
             patch("builtins.print") as printed:
            await restart_background_service()
        restart.assert_awaited_once()
        log_hint.assert_called_once_with()
        printed.assert_called_once_with("后台服务已重启。")

    async def test_existing_binding_never_triggers_login_even_without_terminal(self):
        account = {"token": "fixture", "user": "owner"}
        store = Mock(load=Mock(side_effect=lambda name, default=None: account if name == "account" else {}))
        with patch("codex_wechat.login", AsyncMock()) as login, \
             patch("codex_wechat.sys.stdin.isatty", return_value=False):
            self.assertEqual(await startup_account(store), account)
        login.assert_not_called()

    async def test_first_interactive_start_binds_then_returns_account(self):
        account = {"token": "fixture", "user": "owner"}
        store = Mock(load=Mock(side_effect=[None, account]))
        with patch("codex_wechat.login", AsyncMock()) as login, \
             patch("codex_wechat.sys.stdin.isatty", return_value=True), \
             patch("codex_wechat.sys.stdout.isatty", return_value=True):
            self.assertEqual(await startup_account(store), account)
        login.assert_awaited_once_with(store, starting=True)

    async def test_background_or_redirected_output_refuses_without_login(self):
        for stdin, stdout in ((False, False), (True, False), (False, True)):
            with self.subTest(stdin=stdin, stdout=stdout), \
                 patch("codex_wechat.login", AsyncMock()) as login, \
                 patch("codex_wechat.sys.stdin.isatty", return_value=stdin), \
                 patch("codex_wechat.sys.stdout.isatty", return_value=stdout):
                with self.assertRaises(ConfigurationError):
                    await startup_account(Mock(load=Mock(return_value=None)))
                login.assert_not_called()

    async def test_cancelled_or_failed_login_does_not_continue_or_retry(self):
        for error in (asyncio.CancelledError(), RuntimeError("offline")):
            with patch("codex_wechat.login", AsyncMock(side_effect=error)) as login, \
                 patch("codex_wechat.sys.stdin.isatty", return_value=True), \
                 patch("codex_wechat.sys.stdout.isatty", return_value=True):
                with self.assertRaises(type(error)):
                    await startup_account(Mock(load=Mock(return_value=None)))
                self.assertEqual(login.await_count, 1)

    async def test_success_without_saved_account_does_not_start_bridge(self):
        with patch("codex_wechat.login", AsyncMock()), \
             patch("codex_wechat.sys.stdin.isatty", return_value=True), \
             patch("codex_wechat.sys.stdout.isatty", return_value=True):
            with self.assertRaisesRegex(ConfigurationError, "绑定未完成"):
                await startup_account(Mock(load=Mock(return_value=None)))
