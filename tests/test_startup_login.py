"""First-launch login is interactive only; never contact the real WeChat API."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat import startup_account
from configuration import ConfigurationError


class StartupLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_binding_never_triggers_login_even_without_terminal(self):
        account = {"token": "fixture", "user": "owner"}
        store = Mock(load=Mock(return_value=account))
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
