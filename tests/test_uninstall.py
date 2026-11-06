"""Uninstall keeps data by default and only purges explicit user state."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import platform_support
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

    async def test_data_root_is_validated_before_service_removal(self):
        events = []
        with patch.object(uninstall, "IS_WINDOWS", False), \
                patch.object(uninstall, "data_root",
                             side_effect=lambda: events.append("data-root") or Path("/data")), \
                patch.object(uninstall, "_linux_service_removal",
                             AsyncMock(side_effect=lambda: events.append("service"))), \
                patch.object(uninstall, "_require_no_bridge", Mock()), \
                patch.object(uninstall, "_linux_command_removal", Mock()), \
                patch.object(uninstall, "_remove_data_root_marker", Mock()):
            await uninstall.uninstall()
        self.assertEqual(events, ["data-root", "service"])

    async def test_purge_uses_the_validated_root_after_marker_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            (install / ".data-root").write_text(str(custom) + "\n", encoding="utf-8")
            (custom / "config.json").write_text("{}", encoding="utf-8")
            (install / "config.json").write_text("{}", encoding="utf-8")
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.dict(uninstall.os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                await uninstall.uninstall(purge=True)
            self.assertFalse((custom / "config.json").exists())
            self.assertTrue((install / "config.json").exists())

    async def test_purge_without_a_marker_or_explicit_root_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.dict(uninstall.os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "缺少数据根标记"):
                    await uninstall.uninstall(purge=True)

    async def test_failed_purge_keeps_the_marker_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            (install / ".data-root").write_text(str(custom) + "\n", encoding="utf-8")
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_purge_linux_data",
                                 side_effect=RuntimeError("purge failed")), \
                    patch.dict(uninstall.os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "purge failed"):
                    await uninstall.uninstall(purge=True)
            self.assertTrue((install / ".data-root").exists())

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

    def test_custom_purge_removes_only_known_data_and_keeps_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "custom-data"
            install = Path(directory) / "install"
            root.mkdir()
            install.mkdir()
            (root / "unrelated.txt").write_text("keep", encoding="utf-8")
            (root / "config.json").write_text("{}", encoding="utf-8")
            with patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "data_root", return_value=root):
                uninstall._purge_linux_data()
            self.assertTrue((root / "unrelated.txt").exists())
            self.assertFalse((root / "config.json").exists())

    def test_purge_refuses_a_data_root_that_contains_the_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            install.mkdir()
            with patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "data_root", return_value=root):
                with self.assertRaisesRegex(ValueError, "包含安装目录"):
                    uninstall._purge_linux_data()

    async def test_purge_refuses_while_a_bridge_holds_the_state_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            root.mkdir()
            store = uninstall.Store(root / ".state")
            try:
                with patch.object(uninstall, "IS_WINDOWS", False), \
                        patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                        patch.object(uninstall, "data_root", return_value=root):
                    with self.assertRaisesRegex(RuntimeError, "前台桥接仍在运行"):
                        await uninstall.uninstall(purge=True)
                self.assertTrue(root.exists())
            finally:
                store.close()

    def test_unit_link_uses_xdg_config_home(self):
        with patch.dict(uninstall.os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(uninstall._unit_link(),
                             Path("/tmp/xdg/systemd/user/codex-wechat.service"))

    def test_managed_systemd_data_root_dropin_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            dropin = Path(directory) / "systemd/user/codex-wechat.service.d/data-root.conf"
            dropin.parent.mkdir(parents=True)
            dropin.write_text(uninstall.UNIT_DROPIN_MARKER + "\n", encoding="utf-8")
            with patch.dict(uninstall.os.environ, {"XDG_CONFIG_HOME": directory}):
                uninstall._remove_unit_dropin()
            self.assertFalse(dropin.exists())

    def test_data_root_marker_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            (install / ".data-root").write_text("/custom\n", encoding="utf-8")
            with patch.object(uninstall, "INSTALL_ROOT", install):
                uninstall._remove_data_root_marker()
            self.assertFalse((install / ".data-root").exists())

    def test_purge_refuses_a_data_root_that_contains_the_home_directory(self):
        with patch.object(uninstall, "INSTALL_ROOT", Path("/tmp/install")), \
                patch.object(uninstall, "data_root", return_value=Path.home()):
            with self.assertRaisesRegex(ValueError, "范围过大"):
                uninstall._purge_linux_data()

    async def test_uninstall_refuses_when_service_state_is_unreadable(self):
        with patch.object(uninstall.service_adapter, "status",
                          AsyncMock(side_effect=RuntimeError("manager unavailable"))):
            with self.assertRaisesRegex(RuntimeError, "无法确认后台服务状态"):
                await uninstall._linux_service_removal()

    async def test_windows_path_is_not_handled_by_the_linux_helper(self):
        with patch.object(uninstall, "IS_WINDOWS", True):
            with self.assertRaisesRegex(RuntimeError, "uninstall.ps1"):
                await uninstall.uninstall()
