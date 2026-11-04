"""Autostart contracts with native operations replaced; no service mutations."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import autostart
from codex_wechat import Bridge
from commands import dispatch
from storage import Store


class AutostartTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_reads_the_task_without_starting_powershell(self):
        with patch.object(autostart, "IS_WINDOWS", True), \
                patch.object(autostart, "windows_task_flags", return_value=(True, True)), \
                patch.object(autostart, "_windows_request", AsyncMock()) as script:
            self.assertTrue(await autostart.status())
            self.assertTrue(await autostart.installed())
        script.assert_not_called()

    async def test_windows_falls_back_to_the_launcher_script(self):
        with patch.object(autostart, "IS_WINDOWS", True), \
                patch.object(autostart, "windows_task_flags", side_effect=OSError("no scheduler")), \
                patch.object(autostart, "_windows_request", AsyncMock(return_value=False)) as script, \
                patch.object(autostart, "log_event"):
            self.assertFalse(await autostart.status())
        script.assert_awaited_once_with("status")

    async def test_windows_without_the_task_reports_unavailable(self):
        with patch.object(autostart, "IS_WINDOWS", True), \
                patch.object(autostart, "windows_task_flags", return_value=(False, False)):
            with self.assertRaises(ValueError):
                await autostart.status()
            self.assertFalse(await autostart.installed())

    async def test_linux_reads_native_state_and_checks_installation_identity(self):
        for value, expected in (("enabled", True), ("disabled", False), ("linked", False)):
            output = f"FragmentPath={autostart.INSTALL_ROOT / autostart.UNIT}\nUnitFileState={value}"
            with patch.object(autostart, "IS_WINDOWS", False), \
                    patch.object(autostart, "systemctl", AsyncMock(return_value=output)):
                self.assertIs(await autostart.status(), expected)
        with patch.object(autostart, "systemctl", AsyncMock(return_value=
                "FragmentPath=/other/install.service\nUnitFileState=enabled")):
            with self.assertRaises(ValueError):
                await autostart._linux_status()

    async def test_disable_keeps_demand_start_and_never_stops_current_process(self):
        native = AsyncMock()
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(side_effect=[True, False])), \
                patch.object(autostart, "systemctl", native):
            self.assertFalse(await autostart.set_enabled(False))
        args = [call.args for call in native.await_args_list]
        self.assertEqual(args, [("disable", autostart.UNIT),
            ("link", str((autostart.INSTALL_ROOT / autostart.UNIT).resolve())), ("daemon-reload",)])
        self.assertFalse(any("stop" in arg or "--now" in arg for arg in args))

    async def test_enable_only_toggles_an_installed_service(self):
        native = AsyncMock()
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(side_effect=[False, True])), \
                patch.object(autostart, "systemctl", native):
            self.assertTrue(await autostart.set_enabled(True))
        native.assert_awaited_once_with("enable", autostart.UNIT)

    async def test_invalid_installation_prevents_mutation(self):
        native = AsyncMock()
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(side_effect=ValueError("not ours"))), \
                patch.object(autostart, "systemctl", native):
            with self.assertRaises(ValueError):
                await autostart.set_enabled(True)
        native.assert_not_called()

    async def test_failed_readback_is_not_reported_as_success(self):
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(return_value=False)), \
                patch.object(autostart, "systemctl", AsyncMock()):
            with self.assertRaises(RuntimeError):
                await autostart.set_enabled(True)

    async def test_windows_uses_fixed_script_argv_and_parses_boolean(self):
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"enabled":false}', b'')))
        spawn = AsyncMock(return_value=process)
        with patch.object(autostart, "IS_WINDOWS", True), \
                patch.object(autostart.asyncio, "create_subprocess_exec", spawn):
            self.assertFalse(await autostart.set_enabled(False))
        args = spawn.call_args.args
        self.assertEqual(args[-3:], ("autostart", "off", "-Json"))
        self.assertIn(str(autostart.INSTALL_ROOT / "codex-wechat.ps1"), args)

    async def test_windows_failure_and_malformed_response_are_not_success(self):
        for code, output in ((1, b''), (0, b'{"enabled":"true"}'), (0, b'bad')):
            process = SimpleNamespace(returncode=code, communicate=AsyncMock(return_value=(output, b'')))
            with patch.object(autostart.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
                with self.assertRaises((ValueError, RuntimeError)):
                    await autostart._windows_request("status")

    async def test_settings_are_live_and_do_not_call_backend_or_write_config(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "context"
                bridge.ensure_backend = AsyncMock(side_effect=AssertionError("no backend"))
                with patch("commands.autostart.status", AsyncMock(return_value=True)), \
                        patch("commands.autostart.set_enabled", AsyncMock(return_value=False)) as setter:
                    await dispatch(bridge, "/settings")
                    self.assertIn("autostart: true", bridge.state["outbox"][-1]["text"])
                    settings_text = bridge.state["outbox"][-1]["text"]
                    self.assertIn(
                        "\n\n以上设置可通过 `/settings [option] [value]` 更改。",
                        settings_text)
                    self.assertLess(settings_text.index("slicing.smart:"),
                                    settings_text.index("sourceHint:"))
                    self.assertLess(settings_text.index("sourceHint:"),
                                    settings_text.index("[无法直接修改]"))
                    self.assertLess(settings_text.index("[无法直接修改]"),
                                    settings_text.index("quotaWarningThresholds.primary:"))
                    self.assertLess(settings_text.index("quotaWarningThresholds.primary:"),
                                    settings_text.index("以上设置"))
                    await dispatch(bridge, "/settings autostart false")
                    self.assertEqual(bridge.state["outbox"][-1]["text"], "autostart: false")
                    await dispatch(bridge, "/settings autostart maybe")
                    self.assertIn("用法", bridge.state["outbox"][-1]["text"])
                    await dispatch(bridge, "/settings autostart on")
                    self.assertIn("用法", bridge.state["outbox"][-1]["text"])
                    setter.assert_awaited_once_with(False)
                bridge.ensure_backend.assert_not_called()
                self.assertNotIn("autostart", bridge.config)
                self.assertNotIn("autostart", bridge.state)
            finally:
                store.lock.close()

    async def test_settings_failure_is_reported_without_success_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "context"
                with patch("commands.autostart.set_enabled", AsyncMock(side_effect=RuntimeError("denied"))):
                    await dispatch(bridge, "/settings autostart true")
                self.assertIn("未确认", bridge.state["outbox"][-1]["text"])
            finally:
                store.lock.close()

    async def test_settings_enable_only_toggles_autostart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "context"
                bridge.running = True
                with patch("commands.autostart.set_enabled", AsyncMock(return_value=True)) as setter:
                    await dispatch(bridge, "/settings autostart true")
                setter.assert_awaited_once_with(True)
                self.assertEqual(bridge.state["outbox"][-1]["text"], "autostart: true")
                self.assertFalse(bridge.restarting)
            finally:
                store.lock.close()
