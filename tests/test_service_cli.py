"""CLI gates for login and the background service, without a real install."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import codex_wechat
import service_adapter
from configuration import ConfigurationError
from service_adapter import ServiceState


def interactive(input_is_tty=True, output_is_tty=True):
    return (patch.object(sys.stdin, "isatty", return_value=input_is_tty),
            patch.object(sys.stdout, "isatty", return_value=output_is_tty))


class LoginGateTests(unittest.IsolatedAsyncioTestCase):
    """The gate lives here so every platform gets it, not just PowerShell."""

    async def test_requires_an_interactive_terminal(self):
        status = AsyncMock()
        input_patch, output_patch = interactive(input_is_tty=False)
        with input_patch, output_patch, \
                patch.object(codex_wechat.service_adapter, "status", status):
            with self.assertRaisesRegex(ConfigurationError, "交互终端"):
                await codex_wechat.require_login_allowed()
        status.assert_not_awaited()

    async def test_refuses_while_the_service_runs(self):
        input_patch, output_patch = interactive()
        with input_patch, output_patch, \
                patch.object(codex_wechat.service_adapter, "status",
                             AsyncMock(return_value=ServiceState(True, True))):
            with self.assertRaisesRegex(ConfigurationError, "先停止后台服务"):
                await codex_wechat.require_login_allowed()

    async def test_allows_login_when_the_service_is_stopped(self):
        input_patch, output_patch = interactive()
        with input_patch, output_patch, \
                patch.object(codex_wechat.service_adapter, "status",
                             AsyncMock(return_value=ServiceState(True, False))):
            await codex_wechat.require_login_allowed()

    async def test_allows_login_when_no_service_is_installed(self):
        input_patch, output_patch = interactive()
        with input_patch, output_patch, \
                patch.object(codex_wechat.service_adapter, "status",
                             AsyncMock(return_value=ServiceState(False, False))):
            await codex_wechat.require_login_allowed()

    async def test_foreign_installation_becomes_a_configuration_error(self):
        input_patch, output_patch = interactive()
        with input_patch, output_patch, \
                patch.object(codex_wechat.service_adapter, "status",
                             AsyncMock(side_effect=ValueError("不属于这个安装目录"))):
            with self.assertRaisesRegex(ConfigurationError, "不属于"):
                await codex_wechat.require_login_allowed()

    async def test_main_refuses_an_elevated_terminal(self):
        startup_store = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "status"]), \
                patch.object(codex_wechat, "is_elevated", return_value=True), \
                patch.object(codex_wechat, "startup_store", startup_store):
            with self.assertRaisesRegex(ConfigurationError, "普通用户"):
                await codex_wechat.main()
        startup_store.assert_not_awaited()

    async def test_main_gates_login_before_taking_the_bridge_lock(self):
        gate = AsyncMock(side_effect=ConfigurationError("blocked"))
        startup_store = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "login"]), \
                patch.object(codex_wechat.os, "umask"), \
                patch.object(codex_wechat, "require_login_allowed", gate), \
                patch.object(codex_wechat, "startup_store", startup_store):
            with self.assertRaises(ConfigurationError):
                await codex_wechat.main()
        gate.assert_awaited_once_with()
        startup_store.assert_not_awaited()


class ServiceFailureShapeTests(unittest.IsolatedAsyncioTestCase):
    """A missing service is one condition, so it gets one message and one code."""

    async def test_start_reports_a_missing_service_as_configuration(self):
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(return_value=ServiceState(False, False))):
            with self.assertRaises(ConfigurationError) as raised:
                await codex_wechat.start_background_service(
                    SimpleNamespace(state_dir=Path("/state"), config=Path("/config")))
        self.assertEqual(str(raised.exception), service_adapter.NOT_INSTALLED)

    async def test_restart_reports_a_missing_service_as_configuration(self):
        with patch.object(codex_wechat.service_adapter, "restart",
                          AsyncMock(side_effect=ValueError(service_adapter.NOT_INSTALLED))):
            with self.assertRaises(ConfigurationError) as raised:
                await codex_wechat.restart_background_service()
        self.assertEqual(str(raised.exception), service_adapter.NOT_INSTALLED)

    async def test_adapter_start_and_restart_agree_on_the_message(self):
        with patch.object(service_adapter, "status",
                          AsyncMock(return_value=ServiceState(False, False))):
            for command in (service_adapter.start, service_adapter.restart):
                with self.assertRaises(ValueError) as raised:
                    await command()
                self.assertEqual(str(raised.exception), service_adapter.NOT_INSTALLED)
