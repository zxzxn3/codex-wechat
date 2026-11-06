"""Installed-service adapter contracts with native calls mocked."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import service_adapter


class ServiceAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_service_phase_matrix(self):
        cases = (
            (service_adapter.ServiceState(False, False), "not-installed"),
            (service_adapter.ServiceState(True, True, state="Queued"), "starting"),
            (service_adapter.ServiceState(True, True, state="activating"), "starting"),
            (service_adapter.ServiceState(True, True, state="Running"), "running"),
            (service_adapter.ServiceState(True, True, state="active"), "running"),
            (service_adapter.ServiceState(True, True, state="deactivating"), "stopping"),
            (service_adapter.ServiceState(True, False, state="failed"), "failed"),
            (service_adapter.ServiceState(True, False, state="inactive"), "stopped"),
        )
        for state, expected in cases:
            with self.subTest(state=state):
                self.assertEqual(state.phase, expected)

    async def test_start_requests_a_failed_service(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(
            True, False, state="failed"))
        request = AsyncMock()
        wait = AsyncMock(return_value=service_adapter.ServiceState(
            True, True, state="active"))
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "_request_start", request), \
                patch.object(service_adapter, "_wait_running_stable", wait):
            await service_adapter.start()
        request.assert_awaited_once_with(timeout=20)
        wait.assert_awaited_once_with(
            timeout=20, settle=service_adapter.STABLE_SECONDS)

    async def test_linux_status_accepts_only_this_installations_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unit = root / "codex-wechat.service"
            unit.write_text("", encoding="utf-8")
            command = AsyncMock(return_value=(
                f"LoadState=loaded\nActiveState=active\nFragmentPath={unit}"
            ))
            with patch.object(service_adapter, "IS_WINDOWS", False), \
                    patch.object(service_adapter, "INSTALL_ROOT", root), \
                    patch.object(service_adapter, "systemctl", command):
                state = await service_adapter.status()
        self.assertTrue(state.installed)
        self.assertTrue(state.running)

    async def test_linux_status_rejects_foreign_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "codex-wechat.service").write_text("", encoding="utf-8")
            command = AsyncMock(return_value=(
                "LoadState=loaded\nActiveState=active\nFragmentPath=/other/service"
            ))
            with patch.object(service_adapter, "IS_WINDOWS", False), \
                    patch.object(service_adapter, "INSTALL_ROOT", root), \
                    patch.object(service_adapter, "systemctl", command):
                with self.assertRaises(ValueError):
                    await service_adapter.status()

    async def test_linux_status_reports_the_native_state_and_exit_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unit = root / "codex-wechat.service"
            unit.write_text("", encoding="utf-8")
            command = AsyncMock(return_value=(
                f"LoadState=loaded\nActiveState=failed\nFragmentPath={unit}\n"
                "Result=exit-code\nExecMainStatus=78"))
            with patch.object(service_adapter, "IS_WINDOWS", False), \
                    patch.object(service_adapter, "INSTALL_ROOT", root), \
                    patch.object(service_adapter, "systemctl", command):
                state = await service_adapter.status()
        self.assertEqual((state.installed, state.running), (True, False))
        self.assertEqual((state.state, state.last_result), ("failed", 78))
        self.assertIn("--property=ExecMainStatus", command.await_args.args)

    async def test_linux_status_leaves_the_exit_code_empty_before_the_first_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unit = root / "codex-wechat.service"
            unit.write_text("", encoding="utf-8")
            command = AsyncMock(return_value=(
                f"LoadState=loaded\nActiveState=inactive\nFragmentPath={unit}\n"
                "Result=success\nExecMainStatus="))
            with patch.object(service_adapter, "IS_WINDOWS", False), \
                    patch.object(service_adapter, "INSTALL_ROOT", root), \
                    patch.object(service_adapter, "systemctl", command):
                state = await service_adapter.status()
        self.assertEqual((state.state, state.last_result), ("inactive", None))

    async def test_windows_status_parses_owned_task_json(self):
        request = AsyncMock(return_value=(
            '{"installed":true,"owned":true,"running":true,"state":"Running"}'
        ))
        with patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request):
            state = await service_adapter.status()
        self.assertTrue(state.installed)
        self.assertTrue(state.running)
        request.assert_awaited_once_with(["status"])

    async def test_windows_status_rejects_foreign_task(self):
        request = AsyncMock(return_value=(
            '{"installed":true,"owned":false,"running":false,"state":"Ready"}'
        ))
        with patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request):
            with self.assertRaises(ValueError):
                await service_adapter.status()

    async def test_linux_restart_requests_the_owned_unit(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(True, True))
        command = AsyncMock()
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "IS_WINDOWS", False), \
                patch.object(service_adapter, "systemctl", command):
            await service_adapter.restart()
        command.assert_awaited_once_with(
            "restart", service_adapter.UNIT, timeout=30)

    async def test_windows_start_asks_only_for_the_task(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(True, False))
        request = AsyncMock()
        wait = AsyncMock(return_value=service_adapter.ServiceState(True, True, state="Running"))
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request), \
                patch.object(service_adapter, "_wait_running_stable", wait):
            await service_adapter.start()
        request.assert_awaited_once_with(["start"], timeout=20)
        wait.assert_awaited_once_with(timeout=20, settle=service_adapter.STABLE_SECONDS)

    async def test_start_waits_when_the_host_reports_a_transitional_state(self):
        for windows, state in ((True, "Queued"), (False, "activating")):
            with self.subTest(windows=windows, state=state):
                status = AsyncMock(return_value=service_adapter.ServiceState(
                    True, True, state=state))
                request = AsyncMock()
                wait = AsyncMock(return_value=service_adapter.ServiceState(
                    True, True, state="Running"))
                with patch.object(service_adapter, "status", status), \
                        patch.object(service_adapter, "IS_WINDOWS", windows), \
                        patch.object(service_adapter, "_windows_request", request), \
                        patch.object(service_adapter, "_wait_running_stable", wait):
                    await service_adapter.start()
                request.assert_not_awaited()
                wait.assert_awaited_once_with(
                    timeout=20, settle=service_adapter.STABLE_SECONDS)

    async def test_start_restarts_after_the_service_finishes_stopping(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(
            True, True, state="deactivating"))
        events = []
        stopped = AsyncMock(side_effect=lambda **_: events.append("stopped"))
        request = AsyncMock(side_effect=lambda **_: events.append("started"))
        wait = AsyncMock(return_value=service_adapter.ServiceState(
            True, True, state="active"))
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "_wait_stopped", stopped), \
                patch.object(service_adapter, "_request_start", request), \
                patch.object(service_adapter, "_wait_running_stable", wait):
            await service_adapter.start()
        self.assertEqual(events, ["stopped", "started"])
        stopped.assert_awaited_once_with(timeout=20)
        request.assert_awaited_once_with(timeout=20)
        wait.assert_awaited_once_with(
            timeout=20, settle=service_adapter.STABLE_SECONDS)

    async def test_windows_restart_uses_only_primitive_host_operations(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(True, True))
        request = AsyncMock()
        stopped = AsyncMock()
        wait = AsyncMock(return_value=service_adapter.ServiceState(True, True, state="Running"))
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request), \
                patch.object(service_adapter, "_wait_stopped", stopped), \
                patch.object(service_adapter, "_wait_running_stable", wait):
            await service_adapter.restart()
        self.assertEqual(
            [call.args[0] for call in request.await_args_list],
            [["stop"], ["start"]],
        )
        stopped.assert_awaited_once_with(timeout=20)
        wait.assert_awaited_once_with(timeout=20, settle=service_adapter.STABLE_SECONDS)

    async def test_stop_uses_the_same_wait_on_both_platforms(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(True, True))
        request = AsyncMock()
        stopped = AsyncMock(return_value=service_adapter.ServiceState(True, False))
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request), \
                patch.object(service_adapter, "_wait_stopped", stopped):
            await service_adapter.stop_and_wait()
        request.assert_awaited_once_with(["stop"], timeout=25)
        stopped.assert_awaited_once_with(timeout=20)

    async def test_restart_rejects_an_unregistered_service(self):
        command = AsyncMock()
        with patch.object(service_adapter, "status",
                          AsyncMock(return_value=service_adapter.ServiceState(False, False))), \
                patch.object(service_adapter, "IS_WINDOWS", False), \
                patch.object(service_adapter, "systemctl", command):
            with self.assertRaises(ValueError):
                await service_adapter.restart()
        command.assert_not_awaited()

    async def test_linux_restart_waits_for_a_stable_running_state(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(True, True, state="active"))
        wait = AsyncMock(return_value=status.return_value)
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "IS_WINDOWS", False), \
                patch.object(service_adapter, "systemctl", AsyncMock()), \
                patch.object(service_adapter, "_wait_running_stable", wait):
            result = await service_adapter.restart()
        self.assertTrue(result.running)
        wait.assert_awaited_once_with(timeout=20, settle=service_adapter.STABLE_SECONDS)

    async def test_stable_wait_ignores_an_activating_sample(self):
        status = AsyncMock(side_effect=[
            service_adapter.ServiceState(True, True, state="activating"),
            service_adapter.ServiceState(True, True, state="active"),
        ])
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter.asyncio, "sleep", AsyncMock()):
            current = await service_adapter._wait_running_stable(timeout=1, settle=0)
        self.assertEqual(current.state, "active")
        self.assertEqual(status.await_count, 2)

    async def test_stable_wait_fails_fast_after_a_running_sample_drops(self):
        status = AsyncMock(side_effect=[
            service_adapter.ServiceState(True, True, state="Running"),
            service_adapter.ServiceState(True, False, state="Ready", last_result=78),
        ])
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter.asyncio, "sleep", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "Ready.*78"):
                await service_adapter._wait_running_stable(timeout=5, settle=2)


if __name__ == "__main__":
    unittest.main()
