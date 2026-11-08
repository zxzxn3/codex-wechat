"""CLI gates for login and the background service, without a real install."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat import app as codex_wechat
from codex_wechat import service_adapter
from codex_wechat.configuration import ConfigurationError
from codex_wechat.service_adapter import ServiceState
from codex_wechat.storage import Store


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

    async def test_a_machine_without_systemd_still_allows_foreground_login(self):
        input_patch, output_patch = interactive()
        with input_patch, output_patch, \
                patch.object(codex_wechat.service_adapter, "status",
                             AsyncMock(side_effect=RuntimeError("no systemd"))):
            await codex_wechat.require_login_allowed()

    async def test_main_refuses_an_elevated_mutating_action(self):
        startup_store = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "stop"]), \
                patch.object(codex_wechat, "is_elevated", return_value=True), \
                patch.object(codex_wechat, "startup_store", startup_store):
            with self.assertRaisesRegex(ConfigurationError, "普通用户"):
                await codex_wechat.main()
        startup_store.assert_not_awaited()

    async def test_read_only_status_is_allowed_for_root(self):
        startup_store = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "status"]), \
                patch.object(codex_wechat, "is_elevated", return_value=True), \
                patch.object(codex_wechat, "startup_store", startup_store), \
                patch.object(codex_wechat, "status_payload_with_service",
                             AsyncMock(return_value={"bound": False})), \
                patch.object(codex_wechat.os, "umask"):
            with patch("builtins.print"):
                await codex_wechat.main()
        startup_store.assert_awaited_once()

    async def test_root_override_allows_mutating_container_commands(self):
        stop = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "stop"]), \
                patch.object(codex_wechat, "is_elevated", return_value=True), \
                patch.dict("os.environ", {"CODEX_WECHAT_ALLOW_ROOT": "1"}), \
                patch.object(codex_wechat.os, "umask"), \
                patch.object(codex_wechat, "stop_background_service", stop):
            await codex_wechat.main()
        stop.assert_awaited_once()

    async def test_service_child_start_skips_the_foreground_conflict_check(self):
        guard = AsyncMock()
        store = SimpleNamespace(load=lambda *_args, **_kwargs: {"token": "t", "user": "u"},
                                close=lambda: None)
        with patch.object(sys, "argv", ["codex_wechat.py", "start"]), \
                patch.dict("os.environ", {codex_wechat.SERVICE_CHILD_ENV: "1"}), \
                patch.object(codex_wechat.os, "umask"), \
                patch.object(codex_wechat, "require_foreground_start_allowed", guard), \
                patch.object(codex_wechat, "startup_store", AsyncMock(return_value=store)), \
                patch.object(codex_wechat, "startup_account",
                             AsyncMock(side_effect=ConfigurationError("stop here"))):
            with self.assertRaisesRegex(ConfigurationError, "stop here"):
                await codex_wechat.main()
        guard.assert_not_awaited()

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

    async def test_foreground_start_is_rejected_while_the_service_runs(self):
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(return_value=ServiceState(True, True))):
            with self.assertRaisesRegex(ConfigurationError, "后台服务正在运行"):
                await codex_wechat.require_foreground_start_allowed()

    async def test_service_start_reports_a_foreground_state_lock(self):
        args = SimpleNamespace(state_dir=Path("/state"), config=Path("/config"),
                               action="service-start")
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(return_value=ServiceState(True, False))), \
                patch.object(codex_wechat, "startup_store",
                             AsyncMock(side_effect=RuntimeError("state lock busy"))):
            with self.assertRaisesRegex(ConfigurationError, "state lock busy"):
                await codex_wechat.start_background_service(args)

    async def test_service_commands_turn_missing_managers_into_configuration_errors(self):
        missing = FileNotFoundError("systemctl")
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(side_effect=missing)):
            with self.assertRaises(ConfigurationError):
                await codex_wechat.start_background_service(
                    SimpleNamespace(action="service-start"))
            with self.assertRaises(ConfigurationError):
                await codex_wechat.stop_background_service(Path("/state"), Path("/config"))
        with patch.object(codex_wechat.service_adapter, "restart",
                          AsyncMock(side_effect=missing)):
            with self.assertRaises(ConfigurationError):
                await codex_wechat.restart_background_service()
        with patch.object(codex_wechat.autostart, "status",
                          AsyncMock(side_effect=missing)):
            with self.assertRaises(ConfigurationError):
                await codex_wechat.manage_autostart(None)


class CommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_service_help_names_the_native_log_sources(self):
        source = (Path(__file__).parents[2] / "codex_wechat/app.py").read_text(
            encoding="utf-8")
        self.assertIn("Linux: journalctl", source)
        self.assertIn("Windows: service.log", source)
        self.assertNotIn("supervisor/service.log", source)

    async def test_autostart_value_reaches_the_shared_action(self):
        manage = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "autostart", "true"]), \
                patch.object(codex_wechat.os, "umask"), \
                patch.object(codex_wechat, "manage_autostart", manage):
            await codex_wechat.main()
        manage.assert_awaited_once_with("true")

    async def test_uninstall_is_not_a_python_router_action(self):
        with patch.object(sys, "argv", ["codex_wechat.py", "uninstall"]), \
                patch.object(codex_wechat.os, "umask"):
            with self.assertRaises(SystemExit):
                await codex_wechat.main()

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

    async def test_no_action_defaults_to_status(self):
        store = await self.store()
        with patch.object(sys, "argv", ["codex_wechat.py"]), \
                patch.object(codex_wechat.os, "umask"), \
                patch.object(codex_wechat, "startup_store", AsyncMock(return_value=store)), \
                patch.object(codex_wechat, "status_payload_with_service",
                             AsyncMock(return_value={"bound": False})), \
                patch("builtins.print") as output:
            await codex_wechat.main()
        self.assertIn('"bound": false', output.call_args.args[0])
