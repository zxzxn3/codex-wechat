"""Installed-service adapter contracts with native calls mocked."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import service_adapter


class ServiceAdapterTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_windows_status_parses_owned_task_json(self):
        request = AsyncMock(return_value=(
            '{"installed":true,"owned":true,"running":true,"state":"Running"}'
        ))
        with patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request):
            state = await service_adapter.status()
        self.assertTrue(state.installed)
        self.assertTrue(state.running)
        request.assert_awaited_once_with(["service-status", "-Json"])

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
            "--no-block", "restart", service_adapter.UNIT)

    async def test_windows_restart_uses_the_service_entry(self):
        status = AsyncMock(return_value=service_adapter.ServiceState(True, True))
        request = AsyncMock()
        with patch.object(service_adapter, "status", status), \
                patch.object(service_adapter, "IS_WINDOWS", True), \
                patch.object(service_adapter, "_windows_request", request):
            await service_adapter.restart()
        request.assert_awaited_once_with(["service-restart"], timeout=20)

    async def test_restart_rejects_an_unregistered_service(self):
        command = AsyncMock()
        with patch.object(service_adapter, "status",
                          AsyncMock(return_value=service_adapter.ServiceState(False, False))), \
                patch.object(service_adapter, "IS_WINDOWS", False), \
                patch.object(service_adapter, "systemctl", command):
            with self.assertRaises(ValueError):
                await service_adapter.restart()
        command.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
