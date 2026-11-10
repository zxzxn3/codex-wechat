"""Cross-implementation contract for the platform-specific runtime layout."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer
from codex_wechat import platform_support
from codex_wechat import windows_service
from tests.support.helpers import (requires_symlinks, write_runtime_roots_manifest,
                                   xdg_user_data_dir)


class DataRootContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.xdg = self.base / "xdg"

    def environment(self, override=""):
        return patch.dict(os.environ, {
            "CODEX_WECHAT_DATA_ROOT": override,
            "XDG_DATA_HOME": str(self.xdg),
        }, clear=False)

    def runtime_views(self):
        return patch.object(platform_support, "_platform_user_data_dir",
                            xdg_user_data_dir)

    def test_windows_marker_round_trips_for_default_and_custom_roots(self):
        cases = (
            ("default", "", self.xdg / "codex-wechat"),
            ("custom", str(self.base / "custom data"), self.base / "custom data"),
            ("unicode", str(self.base / "自定义 数据"), self.base / "自定义 数据"),
        )
        for name, override, expected in cases:
            with self.subTest(name=name):
                target = self.base / name
                target.mkdir()
                with self.environment(override), self.runtime_views(), \
                        patch.object(installer, "IS_WINDOWS", True), \
                        patch.object(platform_support, "IS_WINDOWS", True), \
                        patch.object(platform_support, "INSTALL_ROOT", target):
                    installer._write_data_root_marker(target)
                    self.assertEqual(installer.saved_data_root(target), expected.resolve())
                with self.environment(""), self.runtime_views(), \
                        patch.object(platform_support, "IS_WINDOWS", True), \
                        patch.object(platform_support, "INSTALL_ROOT", target):
                    self.assertEqual(platform_support.saved_data_root(target),
                                     expected.resolve())
                    self.assertEqual(platform_support.data_root(target), expected.resolve())
                    self.assertEqual(windows_service.data_root(), expected.resolve())
                    self.assertEqual(installer.configured_data_root(target),
                                     expected.resolve())

    def test_saved_windows_marker_wins_over_a_changed_platform_default(self):
        target = self.base / "install"
        target.mkdir()
        with self.environment(""), self.runtime_views(), \
                patch.object(installer, "IS_WINDOWS", True):
            installer._write_data_root_marker(target)
            saved = installer.saved_data_root(target)

        other_xdg = self.base / "other-xdg"
        with patch.dict(os.environ, {
                "CODEX_WECHAT_DATA_ROOT": "",
                "XDG_DATA_HOME": str(other_xdg),
        }, clear=False), self.runtime_views(), \
                patch.object(installer, "IS_WINDOWS", True), \
                patch.object(platform_support, "IS_WINDOWS", True), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            self.assertEqual(saved, (self.xdg / "codex-wechat").resolve())
            self.assertEqual(platform_support.data_root(target), saved)
            self.assertEqual(windows_service.data_root(), saved)
            self.assertEqual(installer.configured_data_root(target), saved)

    def test_relative_data_root_override_is_made_absolute(self):
        target = self.base / "install"
        target.mkdir()
        expected = Path("relative-data").absolute()
        with patch.dict(os.environ, {
                "CODEX_WECHAT_DATA_ROOT": "relative-data",
        }, clear=False), patch.object(platform_support, "INSTALL_ROOT", target):
            self.assertEqual(platform_support.data_root(target), expected)
            self.assertEqual(installer.configured_data_root(target), expected)

    def test_linux_manifest_is_the_only_linux_layout_contract(self):
        target = self.base / "install"
        target.mkdir()
        data = self.base / "manifest-data"
        config = self.base / "manifest-config/codex-wechat"
        state = self.base / "manifest-state/codex-wechat"
        write_runtime_roots_manifest(
            target, data_root=data, config_root=config, state_root=state)
        with self.environment(""), \
                patch.object(platform_support, "IS_WINDOWS", False), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            self.assertEqual(platform_support.saved_data_root(target), data)
            self.assertEqual(platform_support.saved_config_root(target), config)
            self.assertEqual(platform_support.saved_state_root(target), state)
            self.assertEqual(installer.saved_data_root(target), data)
            self.assertEqual(installer.configured_config_root(target), config)
            self.assertEqual(installer.configured_state_root(target), state)
            self.assertEqual(
                platform_support.saved_runtime_roots(target),
                {"data": data, "config": config, "state": state},
            )
            paths = platform_support.runtime_paths()
            self.assertEqual(paths.data_root, data)
            self.assertEqual(paths.config_file, config / "config.json")
            self.assertEqual(paths.state_dir, state)
        self.assertFalse((target / platform_support.DATA_ROOT_FILE).exists())
        self.assertFalse((target / ".config-root").exists())
        self.assertFalse((target / ".state-root").exists())

    def test_linux_legacy_markers_do_not_complete_an_installed_layout(self):
        target = self.base / "install"
        target.mkdir()
        (target / platform_support.CURRENT_FILE).write_text("release\n", encoding="utf-8")
        (target / platform_support.DATA_ROOT_FILE).write_text(
            str(self.base / "data") + "\n", encoding="utf-8")
        with self.environment(""), self.runtime_views(), \
                patch.object(platform_support, "IS_WINDOWS", False), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            for resolver in (
                    platform_support.saved_data_root,
                    platform_support.runtime_paths,
                    platform_support.config_root,
                    platform_support.state_root):
                with self.subTest(resolver=resolver.__name__):
                    with self.assertRaisesRegex(ValueError, "缺少运行清单"):
                        resolver()

    def test_invalid_linux_manifest_fails_closed(self):
        target = self.base / "install"
        target.mkdir()
        manifest = target / platform_support.RUNTIME_ROOTS_FILE
        for contents in (
                "[]\n",
                '{"version": true}\n',
                '{"version": 1, "data": "relative"}\n',
        ):
            with self.subTest(contents=contents):
                manifest.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "运行时路径清单"):
                    platform_support.saved_runtime_roots(target)
                with self.assertRaisesRegex(ValueError, "运行时路径清单"):
                    installer.saved_data_root(target)

    def test_linux_update_keeps_saved_roots_and_rejects_a_data_override(self):
        target = self.base / "install"
        target.mkdir()
        old_data = self.base / "old-data"
        old_config = self.base / "old-config"
        old_state = self.base / "old-state"
        write_runtime_roots_manifest(
            target,
            data_root=old_data,
            config_root=old_config / "codex-wechat",
            state_root=old_state / "codex-wechat",
        )
        with patch.object(installer, "IS_WINDOWS", False), \
                patch.dict(os.environ, {
                    "CODEX_WECHAT_DATA_ROOT": "",
                    "XDG_CONFIG_HOME": str(self.base / "ambient-config"),
                    "XDG_STATE_HOME": str(self.base / "ambient-state"),
                }, clear=False):
            self.assertEqual(installer.configured_data_root(target), old_data)
            self.assertEqual(installer.configured_config_root(target),
                             old_config / "codex-wechat")
            self.assertEqual(installer.configured_state_root(target),
                             old_state / "codex-wechat")
        with patch.object(installer, "IS_WINDOWS", False), \
                patch.dict(os.environ, {
                    "CODEX_WECHAT_DATA_ROOT": str(self.base / "new-data"),
                }, clear=False):
            with self.assertRaisesRegex(ValueError, "固定"):
                installer.configured_data_root(target)

    def test_windows_update_rejects_a_different_data_root(self):
        target = self.base / "install"
        target.mkdir()
        (target / installer.DATA_ROOT_FILE).write_text(
            str(self.base / "old-data") + "\n", encoding="utf-8")
        with patch.object(installer, "IS_WINDOWS", True), \
                patch.dict(os.environ, {
                    "CODEX_WECHAT_DATA_ROOT": str(self.base / "new-data"),
                }, clear=False):
            with self.assertRaisesRegex(ValueError, "固定"):
                installer.configured_data_root(target)

    def test_invalid_windows_marker_fails_closed_in_every_reader(self):
        target = self.base / "install"
        target.mkdir()
        marker = target / platform_support.DATA_ROOT_FILE
        cases = {
            "empty": b"",
            "relative": b"relative/path\n",
            "embedded-newline": b"/tmp/one\ntwo\n",
            "embedded-cr": b"/tmp/one\rtwo\n",
            "embedded-nul": b"/tmp/one\x00two\n",
        }
        for name, contents in cases.items():
            with self.subTest(name=name):
                marker.write_bytes(contents)
                with self.environment(""), self.runtime_views(), \
                        patch.object(installer, "IS_WINDOWS", True), \
                        patch.object(platform_support, "IS_WINDOWS", True), \
                        patch.object(platform_support, "INSTALL_ROOT", target):
                    with self.assertRaises(ValueError):
                        installer.saved_data_root(target)
                    with self.assertRaises(ValueError):
                        platform_support.saved_data_root(target)
                    with self.assertRaises(ValueError):
                        platform_support.data_root(target)
                    with self.assertRaises(ValueError):
                        windows_service.data_root()

        marker.unlink()
        marker.mkdir()
        with self.environment(""), self.runtime_views(), \
                patch.object(installer, "IS_WINDOWS", True), \
                patch.object(platform_support, "IS_WINDOWS", True), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            with self.assertRaises(ValueError):
                installer.saved_data_root(target)
            with self.assertRaises(ValueError):
                platform_support.data_root(target)
            with self.assertRaises(ValueError):
                windows_service.data_root()

    @requires_symlinks
    def test_symlink_windows_marker_fails_closed_in_every_reader(self):
        target = self.base / "install"
        target.mkdir()
        outside = self.base / "outside"
        outside.write_text(str(self.base / "data") + "\n", encoding="utf-8")
        marker = target / platform_support.DATA_ROOT_FILE
        marker.symlink_to(outside)
        with self.environment(""), self.runtime_views(), \
                patch.object(installer, "IS_WINDOWS", True), \
                patch.object(platform_support, "IS_WINDOWS", True), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            with self.assertRaises(ValueError):
                installer.saved_data_root(target)
            with self.assertRaises(ValueError):
                platform_support.data_root(target)
            with self.assertRaises(ValueError):
                windows_service.data_root()


if __name__ == "__main__":
    unittest.main()
