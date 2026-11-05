"""CLI gates for login and the background service, without a real install."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import codex_wechat
import service_adapter
from configuration import ConfigurationError
from service_adapter import ServiceState
from storage import Store


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


class ServiceStatusPayloadTests(unittest.IsolatedAsyncioTestCase):
    """`status` reports the host that runs the bridge, not just saved state."""

    async def store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name) / "state")
        self.addCleanup(store.lock.close)
        return store

    async def test_the_host_state_and_exit_code_land_in_the_json(self):
        store = await self.store()
        with patch.object(codex_wechat.service_adapter, "status", AsyncMock(
                return_value=ServiceState(True, False, state="Ready", last_result=78))):
            payload = await codex_wechat.status_payload_with_service(store)
        self.assertEqual(payload["service"],
                         {"installed": True, "running": False,
                          "state": "Ready", "last_result": 78})

    async def test_an_unreadable_host_is_omitted_instead_of_failing_status(self):
        store = await self.store()
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(side_effect=ValueError("不属于这个安装目录"))):
            payload = await codex_wechat.status_payload_with_service(store)
        self.assertNotIn("service", payload)
        self.assertIn("bound", payload)

    async def test_windows_json_carries_the_native_state_and_exit_code(self):
        output = ('{"installed":true,"owned":true,"running":false,'
                  '"state":"Ready","last_result":78}')
        with patch.object(service_adapter, "_windows_request", AsyncMock(return_value=output)), \
                patch.object(service_adapter, "IS_WINDOWS", True):
            current = await service_adapter.status()
        self.assertEqual((current.installed, current.running), (True, False))
        self.assertEqual((current.state, current.last_result), ("Ready", 78))

    async def test_the_entry_point_can_forward_the_host_state(self):
        store = await self.store()
        host = ('{"installed":true,"owned":true,"running":false,'
                '"state":"Ready","last_result":78}')
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(side_effect=AssertionError("nested query"))):
            payload = await codex_wechat.status_payload_with_service(store, host)
        self.assertEqual(payload["service"],
                         {"installed": True, "running": False,
                          "state": "Ready", "last_result": 78})

    async def test_a_foreign_host_payload_is_omitted(self):
        store = await self.store()
        host = '{"installed":true,"owned":false,"running":false,"state":"Ready"}'
        payload = await codex_wechat.status_payload_with_service(store, host)
        self.assertNotIn("service", payload)

    async def test_an_unparsable_forwarded_payload_is_reported_on_stderr(self):
        store = await self.store()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            payload = await codex_wechat.status_payload_with_service(store, "{installed:true}")
        self.assertNotIn("service", payload)
        self.assertIn("服务宿主状态无法解析", stderr.getvalue())
