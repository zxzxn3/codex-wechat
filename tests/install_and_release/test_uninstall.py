"""Uninstall keeps data by default and only purges explicit user state."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat import platform_support
from codex_wechat import uninstall
from tests.support.helpers import (requires_symlinks, runtime_paths_at,
                                   write_runtime_roots_manifest)


class LinuxUninstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_removes_launcher_and_service_but_keeps_data(self):
        service = AsyncMock()
        command = Mock()
        with patch.object(uninstall, "IS_WINDOWS", False), \
                patch.object(uninstall, "_linux_service_removal", service), \
                patch.object(uninstall, "_linux_command_removal", command), \
                patch.object(uninstall, "_require_no_bridge", Mock()), \
                patch.object(uninstall, "_remove_runtime_roots_manifest", Mock()) as marker, \
                patch.object(uninstall, "runtime_paths",
                             return_value=runtime_paths_at("/data/codex-wechat")):
            await uninstall.uninstall()
        service.assert_awaited_once_with()
        command.assert_called_once_with()
        # The marker is the only pointer to preserved data: a reinstall resolves
        # the same root only if it survives a data-preserving uninstall.
        marker.assert_not_called()

    async def test_a_data_preserving_uninstall_keeps_the_marker_and_purge_drops_it(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            config = Path(directory) / "config" / "config.json"
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            state = Path(directory) / "state"
            state.mkdir()
            write_runtime_roots_manifest(
                install, data_root=custom, config_root=config.parent, state_root=state)
            marker = install / uninstall.RUNTIME_ROOTS_FILE
            paths = runtime_paths_at(
                custom, config_file=config, state_dir=state)
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()), \
                    patch.object(uninstall, "runtime_paths", return_value=paths), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                await uninstall.uninstall()
                self.assertTrue(marker.is_file())
                await uninstall.uninstall(purge=True)
                self.assertFalse(marker.exists())

    async def test_a_purge_says_where_the_remaining_program_files_are(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            config = Path(directory) / "config" / "config.json"
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            state = Path(directory) / "state"
            state.mkdir()
            write_runtime_roots_manifest(
                install, data_root=custom, config_root=config.parent, state_root=state)
            paths = runtime_paths_at(
                custom, config_file=config, state_dir=state)
            out = io.StringIO()
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()), \
                    patch.object(uninstall, "runtime_paths", return_value=paths), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with contextlib.redirect_stdout(out):
                    await uninstall.uninstall(purge=True)
            printed = out.getvalue()
            self.assertIn(f"程序文件仍在: {install}", printed)
            self.assertIn(f"数据文件已删除: {custom}", printed)
            self.assertIn("手动删除上述目录", printed)
            # A purge forgets the layout marker but the tree is still named, so
            # the user can finish the removal themselves.
            self.assertFalse((install / uninstall.RUNTIME_ROOTS_FILE).exists())

    async def test_a_data_preserving_uninstall_names_the_preserved_data(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            config = Path(directory) / "config" / "config.json"
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            state = Path(directory) / "state"
            state.mkdir()
            write_runtime_roots_manifest(
                install, data_root=custom, config_root=config.parent, state_root=state)
            paths = runtime_paths_at(
                custom, config_file=config, state_dir=state)
            out = io.StringIO()
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "runtime_paths", return_value=paths), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with contextlib.redirect_stdout(out):
                    await uninstall.uninstall()
            printed = out.getvalue()
            self.assertIn(f"程序文件仍在: {install}", printed)
            self.assertIn(f"数据文件仍在: {custom}", printed)
            self.assertIn("手动删除上述目录", printed)
            self.assertTrue((install / uninstall.RUNTIME_ROOTS_FILE).exists())
            # Same order as the Windows helper: program files, data, decision.
            self.assertLess(printed.index("程序文件仍在"), printed.index("数据文件仍在"))
            self.assertLess(printed.index("数据文件仍在"), printed.index("手动删除上述目录"))

    async def test_data_root_is_validated_before_service_removal(self):
        events = []
        paths = runtime_paths_at("/data")
        with patch.object(uninstall, "IS_WINDOWS", False), \
                patch.object(uninstall, "runtime_paths",
                             side_effect=lambda: events.append("paths") or paths), \
                patch.object(uninstall, "_linux_service_removal",
                             AsyncMock(side_effect=lambda: events.append("service"))), \
                patch.object(uninstall, "_require_no_bridge", Mock()), \
                patch.object(uninstall, "_linux_command_removal", Mock()), \
                patch.object(uninstall, "_remove_runtime_roots_manifest", Mock()):
            await uninstall.uninstall()
        self.assertEqual(events, ["paths", "service"])

    async def test_purge_uses_the_validated_root_after_marker_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            config = Path(directory) / "config-home/config.json"
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            (install / "config.json").write_text("{}", encoding="utf-8")
            state = Path(directory) / "state-home"
            state.mkdir()
            write_runtime_roots_manifest(
                install, data_root=custom, config_root=config.parent, state_root=state,
            )
            paths = runtime_paths_at(
                custom, config_file=config, state_dir=state)
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()), \
                    patch.object(uninstall, "runtime_paths", return_value=paths), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                await uninstall.uninstall(purge=True)
            self.assertFalse(config.exists())
            self.assertTrue((install / "config.json").exists())

    async def test_purge_without_a_runtime_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            (install / "current").write_text("a" * 16, encoding="utf-8")
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with self.assertRaisesRegex(ValueError, "缺少运行清单"):
                    await uninstall.uninstall(purge=True)

    async def test_failed_purge_keeps_the_marker_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            custom = Path(directory) / "custom"
            install.mkdir()
            custom.mkdir()
            config = Path(directory) / "config-home/config.json"
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            state = Path(directory) / "state-home"
            state.mkdir()
            write_runtime_roots_manifest(
                install, data_root=custom, config_root=config.parent, state_root=state)
            paths = runtime_paths_at(
                custom, config_file=config, state_dir=state)
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                    patch.object(uninstall, "_require_no_bridge", Mock()), \
                    patch.object(uninstall, "_linux_command_removal", Mock()), \
                    patch.object(uninstall, "_linux_shared_backend_removal", AsyncMock()), \
                    patch.object(uninstall, "_purge_linux_data",
                                 side_effect=RuntimeError("purge failed")), \
                    patch.object(uninstall, "runtime_paths", return_value=paths), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "purge failed"):
                    await uninstall.uninstall(purge=True)
            self.assertTrue((install / ".runtime-roots").exists())

    def test_purge_removes_config_state_and_workspace_but_keeps_program_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install = base / "install"
            data = base / "data"
            config = base / "config/config.json"
            state = base / "state"
            install.mkdir()
            data.mkdir()
            config.parent.mkdir()
            state.mkdir()
            config.write_text("{}", encoding="utf-8")
            (data / "workspace").mkdir()
            (state / "bridge.json").write_text("{}", encoding="utf-8")
            (install / "current").write_text("a" * 16, encoding="utf-8")
            uninstall._purge_linux_data(runtime_paths_at(
                data, config_file=config, state_dir=state))
            self.assertFalse(config.exists())
            self.assertFalse(config.parent.exists())
            self.assertFalse(state.exists())
            self.assertFalse((data / "workspace").exists())
            self.assertTrue((install / "current").exists())

    def test_custom_purge_removes_only_known_data_and_keeps_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "custom-data"
            install = Path(directory) / "install"
            root.mkdir()
            install.mkdir()
            (root / "unrelated.txt").write_text("keep", encoding="utf-8")
            config = Path(directory) / "config/config.json"
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            uninstall._purge_linux_data(runtime_paths_at(
                root, config_file=config, state_dir=Path(directory) / "state"))
            self.assertTrue((root / "unrelated.txt").exists())
            self.assertFalse(config.exists())

    def test_purge_refuses_state_root_overlapping_the_custom_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install = base / "install"
            data = base / "custom-data"
            config = base / "config/config.json"
            install.mkdir()
            data.mkdir()
            config.parent.mkdir()
            (data / "unrelated.txt").write_text("keep", encoding="utf-8")
            (data / "workspace").mkdir()
            with patch.object(uninstall, "INSTALL_ROOT", install):
                with self.assertRaisesRegex(ValueError, "不能重叠"):
                    uninstall._purge_linux_data(runtime_paths_at(
                        data, config_file=config, state_dir=data))
            self.assertTrue((data / "unrelated.txt").exists())

    async def test_legacy_runtime_marker_is_rejected_before_service_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            (install / "current").write_text("a" * 16, encoding="utf-8")
            (install / ".data-root").write_text("/tmp/custom-data\n", encoding="utf-8")
            service = AsyncMock()
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "_linux_service_removal", service), \
                    patch.dict(os.environ,
                               {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                with self.assertRaisesRegex(ValueError, "缺少运行清单"):
                    await uninstall.uninstall(purge=True)
            service.assert_not_awaited()

    @requires_symlinks
    def test_purge_refuses_a_symlinked_state_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            data = root / "data"
            external = root / "external"
            config = root / "config/config.json"
            install.mkdir()
            data.mkdir()
            external.mkdir()
            config.parent.mkdir()
            (data / "workspace").mkdir()
            (external / "keep.txt").write_text("keep", encoding="utf-8")
            config.write_text("{}", encoding="utf-8")
            state = root / "state"
            state.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "符号链接"):
                uninstall._purge_linux_data(runtime_paths_at(
                    data, config_file=config, state_dir=state))
            self.assertTrue((external / "keep.txt").exists())
            self.assertTrue(config.exists())

    @requires_symlinks
    def test_purge_refuses_a_state_path_through_a_symlinked_xdg_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            data = root / "data"
            external = root / "external"
            config = root / "config/config.json"
            install.mkdir()
            data.mkdir()
            external.mkdir()
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            (data / "workspace").mkdir()
            external_state = external / "codex-wechat"
            external_state.mkdir()
            (external_state / "keep.txt").write_text("keep", encoding="utf-8")
            state_home = root / "state-home"
            state_home.symlink_to(external, target_is_directory=True)
            with patch.object(uninstall, "INSTALL_ROOT", install), \
                    self.assertRaisesRegex(ValueError, "符号链接"):
                uninstall._purge_linux_data(runtime_paths_at(
                    data, config_file=config,
                    state_dir=state_home / "codex-wechat"))
            self.assertTrue((external_state / "keep.txt").exists())
            self.assertTrue(config.exists())

    def test_purge_refuses_a_target_that_contains_the_home_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            install.mkdir()
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "范围过大"):
                uninstall._purge_linux_data(runtime_paths_at(
                    install, config_file=config,
                    state_dir=Path.home().parent))
            self.assertTrue(config.exists())

    def test_purge_refuses_state_that_is_or_contains_the_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            data = root / "data"
            config = root / "config/config.json"
            install.mkdir()
            data.mkdir()
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            for state in (install, root):
                with self.subTest(state=state):
                    with patch.object(uninstall, "INSTALL_ROOT", install):
                        with self.assertRaisesRegex(ValueError, "程序安装目录"):
                            uninstall._purge_linux_data(runtime_paths_at(
                                data, config_file=config, state_dir=state))
                    self.assertTrue(install.exists())
                    self.assertTrue(config.exists())

    def test_purge_refuses_state_inside_the_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            data = root / "data"
            config = root / "config/config.json"
            install.mkdir()
            data.mkdir()
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            with patch.object(uninstall, "INSTALL_ROOT", install):
                with self.assertRaisesRegex(ValueError, "程序安装目录"):
                    uninstall._purge_linux_data(runtime_paths_at(
                        data, config_file=config,
                        state_dir=install / "codex-wechat"))
            self.assertTrue(install.exists())
            self.assertTrue(config.exists())

    async def test_unsafe_purge_is_rejected_before_service_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            data = root / "data"
            config = root / "config/config.json"
            install.mkdir()
            data.mkdir()
            config.parent.mkdir()
            config.write_text("{}", encoding="utf-8")
            paths = runtime_paths_at(
                data, config_file=config, state_dir=install / "codex-wechat")
            write_runtime_roots_manifest(
                install, data_root=data, config_root=config.parent,
                state_root=install / "codex-wechat")
            remove = AsyncMock()
            with patch.object(uninstall, "IS_WINDOWS", False), \
                    patch.object(uninstall, "INSTALL_ROOT", install), \
                    patch.object(uninstall, "runtime_paths", return_value=paths), \
                    patch.object(uninstall, "_linux_service_removal", remove):
                with self.assertRaisesRegex(ValueError, "程序安装目录"):
                    await uninstall.uninstall(purge=True)
            remove.assert_not_awaited()

    def test_purge_allows_the_expected_workspace_under_the_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            config = root / "config/config.json"
            state = root / "state"
            install.mkdir()
            config.parent.mkdir()
            state.mkdir()
            config.write_text("{}", encoding="utf-8")
            workspace = install / "workspace"
            workspace.mkdir()
            with patch.object(uninstall, "INSTALL_ROOT", install):
                uninstall._purge_linux_data(runtime_paths_at(
                    install, config_file=config, state_dir=state))
            self.assertFalse(workspace.exists())
            self.assertTrue(install.exists())

    async def test_purge_refuses_while_a_bridge_holds_the_state_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            root.mkdir()
            install = Path(directory) / "install"
            install.mkdir()
            config = Path(directory) / "config/config.json"
            config.parent.mkdir()
            state = Path(directory) / "state"
            store = uninstall.Store(state)
            paths = runtime_paths_at(
                root, config_file=config, state_dir=state)
            write_runtime_roots_manifest(
                install, data_root=root,
                config_root=config.parent, state_root=state)
            try:
                with patch.object(uninstall, "IS_WINDOWS", False), \
                        patch.object(uninstall, "INSTALL_ROOT", install), \
                        patch.object(uninstall, "_linux_service_removal", AsyncMock()), \
                        patch.object(uninstall, "runtime_paths", return_value=paths):
                    with self.assertRaisesRegex(RuntimeError, "前台桥接仍在运行"):
                        await uninstall.uninstall(purge=True)
                self.assertTrue(root.exists())
            finally:
                store.close()

    def test_unit_link_uses_xdg_config_home(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
                self.assertEqual(uninstall._unit_link(),
                                 (Path(directory).resolve() / "systemd/user" / uninstall.UNIT))

    def test_unit_link_ignores_a_relative_xdg_config_home(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative/config"}):
            self.assertEqual(
                uninstall._unit_link(),
                Path.home().resolve() / ".config/systemd/user" / uninstall.UNIT)

    async def test_unregistered_service_skips_an_unreachable_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory) / "systemd/user" / uninstall.UNIT
            default_unit = Path(directory) / "default/systemd/user" / uninstall.UNIT
            status = AsyncMock(side_effect=RuntimeError("manager unavailable"))
            with patch.object(uninstall, "_unit_link", return_value=unit), \
                    patch.object(uninstall, "_default_unit_link", return_value=default_unit), \
                    patch.object(uninstall.service_adapter, "status", status), \
                    patch.object(uninstall, "_remove_unit_dropin") as dropin:
                await uninstall._linux_service_removal()
        status.assert_awaited_once_with()
        dropin.assert_called_once_with()

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
                    patch.object(uninstall, "systemctl", systemctl), \
                    patch.object(uninstall, "_remove_unit_dropin") as dropin:
                await uninstall._linux_service_removal()
            self.assertFalse(registered.exists())
            dropin.assert_called_once_with(registered)
            self.assertEqual(
                [call.args for call in systemctl.await_args_list],
                [("disable", uninstall.UNIT), ("daemon-reload",)],
            )

    async def test_shared_backend_removal_stops_only_units_from_this_data_root(self):
        first = "codex-wechat-codex-" + "a" * 20 + ".service"
        second = "codex-wechat-codex-" + "b" * 20 + ".service"
        data_root = Path("/data/codex-wechat")
        output = (
            f"{first} loaded active running shared backend\n"
            "codex-wechat-codex-not-a-unit.service loaded active running foreign\n"
            f"{second} loaded failed failed shared backend\n"
        )
        systemctl = AsyncMock(side_effect=[
            output,
            str(data_root / "shared"),
            None,
            "/somewhere/else",
        ])
        with patch.object(uninstall.shutil, "which", return_value="/usr/bin/systemctl"), \
                patch.object(uninstall, "systemctl", systemctl):
            await uninstall._linux_shared_backend_removal(
                runtime_paths_at(data_root))
        systemctl.assert_any_call("stop", first, timeout=20)
        self.assertFalse(
            any(item.args[:2] == ("stop", second)
                for item in systemctl.await_args_list),
        )
        self.assertEqual(systemctl.await_count, 4)

    def test_managed_systemd_data_root_dropin_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            dropin = Path(directory) / "systemd/user/codex-wechat.service.d/data-root.conf"
            dropin.parent.mkdir(parents=True)
            dropin.write_text(uninstall.UNIT_DROPIN_MARKER + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
                uninstall._remove_unit_dropin()
            self.assertFalse(dropin.exists())

    def test_purge_manifest_removal_does_not_touch_ignored_legacy_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            for filename in (".runtime-roots", ".data-root", ".config-root", ".state-root"):
                (install / filename).write_text("/custom\n", encoding="utf-8")
            with patch.object(uninstall, "INSTALL_ROOT", install):
                uninstall._remove_runtime_roots_manifest()
            self.assertFalse((install / ".runtime-roots").exists())
            for filename in (".data-root", ".config-root", ".state-root"):
                self.assertTrue((install / filename).exists())

    async def test_uninstall_refuses_when_service_state_is_unreadable(self):
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory) / "systemd/user" / uninstall.UNIT
            unit.parent.mkdir(parents=True)
            unit.write_text("[Unit]\n", encoding="utf-8")
            with patch.object(uninstall, "_unit_link", return_value=unit), \
                    patch.object(uninstall.service_adapter, "status",
                                 AsyncMock(side_effect=RuntimeError("manager unavailable"))):
                with self.assertRaisesRegex(RuntimeError, "无法确认后台服务状态"):
                    await uninstall._linux_service_removal()

    async def test_windows_path_is_not_handled_by_the_linux_helper(self):
        with patch.object(uninstall, "IS_WINDOWS", True):
            with self.assertRaisesRegex(RuntimeError, "uninstall.ps1"):
                await uninstall.uninstall()
