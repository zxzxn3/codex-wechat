"""Uninstall keeps data by default and executes the contract's purge plan."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat import platform_support
from codex_wechat import uninstall
from codex_wechat.install_layout import INSTALLATION_FILE
from tests.support.helpers import write_installation_layout


class LinuxUninstallTests(unittest.IsolatedAsyncioTestCase):
    def make_layout(self, directory):
        base = Path(directory)
        install = base / "install"
        data = base / "data"
        config = base / "config/codex-wechat/config.json"
        state = base / "state/codex-wechat"
        config.parent.mkdir(parents=True)
        config.write_text("{}", encoding="utf-8")
        state.mkdir(parents=True)
        (data / "workspace").mkdir(parents=True)
        layout = write_installation_layout(
            install,
            data_root=data,
            config_root=config.parent,
            state_root=state,
        )
        return install, data, config, state, layout

    @contextlib.contextmanager
    def patched_root(self, install):
        with patch.object(uninstall, "IS_WINDOWS", False), \
                patch.object(uninstall, "INSTALL_ROOT", install), \
                patch.object(platform_support, "INSTALL_ROOT", install):
            yield

    async def test_default_uninstall_keeps_data_and_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            install, data, config, state, layout = self.make_layout(directory)
            service = AsyncMock()
            command = Mock()
            with self.patched_root(install), \
                    patch.object(uninstall, "_linux_service_removal", service), \
                    patch.object(uninstall, "_linux_command_removal", command), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()) as shared:
                await uninstall.uninstall()
            service.assert_awaited_once_with()
            shared.assert_not_awaited()
            command.assert_called_once_with()
            self.assertTrue((install / INSTALLATION_FILE).is_file())
            self.assertTrue(config.is_file())
            self.assertTrue(state.is_dir())
            self.assertTrue((data / "workspace").is_dir())

    async def test_purge_deletes_only_planned_leaves_and_forgets_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            install, data, config, state, layout = self.make_layout(directory)
            unrelated = data / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            with self.patched_root(install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()) as shared:
                await uninstall.uninstall(purge=True)
            self.assertFalse((install / INSTALLATION_FILE).exists())
            self.assertFalse(config.exists())
            self.assertFalse(state.exists())
            self.assertFalse((data / "workspace").exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            shared.assert_awaited_once()
            paths = shared.await_args.args[0]
            self.assertEqual(paths.data_root, data)

    async def test_purge_reports_remaining_program_files(self):
        with tempfile.TemporaryDirectory() as directory:
            install, data, *_ = self.make_layout(directory)
            out = io.StringIO()
            with self.patched_root(install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()):
                with contextlib.redirect_stdout(out):
                    await uninstall.uninstall(purge=True)
            printed = out.getvalue()
            self.assertIn(f"程序文件仍在: {install}", printed)
            self.assertIn(f"数据文件已删除: {data}", printed)
            self.assertLess(printed.index("程序文件仍在"), printed.index("数据文件已删除"))

    async def test_unsafe_purge_is_rejected_before_service_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            install, _, _, state, _ = self.make_layout(directory)
            external = Path(directory) / "external"
            external.mkdir()
            state.rmdir()
            try:
                state.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            service = AsyncMock()
            with self.patched_root(install), \
                    patch.object(uninstall, "_linux_service_removal", service):
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    await uninstall.uninstall(purge=True)
            service.assert_not_awaited()

    async def test_failed_purge_keeps_layout_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            install, *_ = self.make_layout(directory)
            with self.patched_root(install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()), \
                    patch.object(uninstall, "_purge_linux_data",
                                 side_effect=RuntimeError("purge failed")):
                with self.assertRaisesRegex(RuntimeError, "purge failed"):
                    await uninstall.uninstall(purge=True)
            self.assertTrue((install / INSTALLATION_FILE).is_file())

    async def test_purge_refuses_while_a_bridge_holds_the_state_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            install, data, _, state, _ = self.make_layout(directory)
            store = uninstall.Store(state)
            try:
                with self.patched_root(install), \
                        patch.object(uninstall, "_linux_service_removal", AsyncMock()):
                    with self.assertRaisesRegex(RuntimeError, "前台桥接仍在运行"):
                        await uninstall.uninstall(purge=True)
                self.assertTrue(data.exists())
            finally:
                store.close()

    def test_unit_link_uses_xdg_config_home(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
                self.assertEqual(
                    uninstall._unit_link(),
                    Path(directory).resolve() / "systemd/user" / uninstall.UNIT,
                )

    async def test_removal_uses_the_registered_fragment_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            install.mkdir()
            expected = install / uninstall.UNIT
            expected.write_text("[Unit]\n", encoding="utf-8")
            shell_link = root / "custom/systemd/user" / uninstall.UNIT
            registered = root / "default/systemd/user" / uninstall.UNIT
            registered.parent.mkdir(parents=True)
            try:
                registered.symlink_to(expected)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            current = uninstall.service_adapter.ServiceState(
                True, False, fragment_path=registered)
            systemctl = AsyncMock(side_effect=[None, None])
            with patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_unit_link", return_value=shell_link), \
                    patch.object(uninstall.service_adapter, "status",
                                 AsyncMock(return_value=current)), \
                    patch.object(uninstall, "systemctl", systemctl):
                await uninstall._linux_service_removal()
            self.assertFalse(registered.exists())

    async def test_shared_backend_removal_stops_only_units_from_this_data_root(self):
        first = "codex-wechat-codex-" + "a" * 20 + ".service"
        second = "codex-wechat-codex-" + "b" * 20 + ".service"
        data_root = Path("/data/codex-wechat")
        output = (
            f"{first} loaded active running shared backend\n"
            f"{second} loaded active running shared backend\n"
        )
        systemctl = AsyncMock(side_effect=[
            output,
            str(data_root / "shared"),
            None,
            "/somewhere/else",
        ])
        from tests.support.helpers import runtime_paths_at
        with patch.object(uninstall.shutil, "which", return_value="/usr/bin/systemctl"), \
                patch.object(uninstall, "systemctl", systemctl):
            await uninstall._linux_shared_backend_removal(
                runtime_paths_at(data_root))
        systemctl.assert_any_call("stop", first, timeout=20)
        self.assertFalse(any(
            item.args[:2] == ("stop", second)
            for item in systemctl.await_args_list
        ))

    async def test_windows_path_is_not_handled_by_the_linux_helper(self):
        with patch.object(uninstall, "IS_WINDOWS", True):
            with self.assertRaisesRegex(RuntimeError, "uninstall.ps1"):
                await uninstall.uninstall()


if __name__ == "__main__":
    unittest.main()
