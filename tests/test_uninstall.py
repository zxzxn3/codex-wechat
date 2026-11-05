"""Uninstall keeps data by default and only purges explicit user state."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import uninstall


class LinuxUninstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_removes_launcher_and_service_but_keeps_data(self):
        service = AsyncMock()
        command = Mock()
        with patch.object(uninstall, "IS_WINDOWS", False), \
                patch.object(uninstall, "_linux_service_removal", service), \
                patch.object(uninstall, "_linux_command_removal", command), \
                patch.object(uninstall, "data_root",
                             return_value=Path("/data/codex-wechat")):
            await uninstall.uninstall()
        service.assert_awaited_once_with()
        command.assert_called_once_with()

    def test_purge_removes_only_user_data_when_data_is_the_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("config.json", ".state", "workspace", "logs", "shared"):
                path = root / name
                if name == "config.json":
                    path.write_text("{}", encoding="utf-8")
                else:
                    path.mkdir()
            (root / "current").write_text("a" * 16, encoding="utf-8")
            with patch.object(uninstall, "INSTALL_ROOT", root), \
                    patch.object(uninstall, "data_root", return_value=root):
                uninstall._purge_linux_data()
            for name in ("config.json", ".state", "workspace", "logs", "shared"):
                self.assertFalse((root / name).exists())
            self.assertTrue((root / "current").exists())

    def test_purge_refuses_a_data_root_that_contains_the_home_directory(self):
        with patch.object(uninstall, "INSTALL_ROOT", Path("/tmp/install")), \
                patch.object(uninstall, "data_root", return_value=Path.home()):
            with self.assertRaisesRegex(ValueError, "范围过大"):
                uninstall._purge_linux_data()


class WindowsUninstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_purge_is_forwarded_to_the_powershell_helper(self):
        class Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uninstall.ps1").write_text("", encoding="utf-8")
            spawn = AsyncMock(return_value=Process())
            with patch.object(uninstall, "IS_WINDOWS", True), \
                    patch.object(uninstall, "INSTALL_ROOT", root), \
                    patch.object(uninstall.asyncio, "create_subprocess_exec", spawn):
                await uninstall.uninstall(purge=True)
        command = spawn.call_args.args
        self.assertIn("-PurgeData", command)
        self.assertIn(str(root / "uninstall.ps1"), command)
