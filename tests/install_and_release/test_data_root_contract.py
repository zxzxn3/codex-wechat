"""Cross-implementation contract for the install-root data marker."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer
from codex_wechat import platform_support
from codex_wechat import windows_service

from tests.support.helpers import (requires_symlinks, write_runtime_root_markers,
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

    def test_written_marker_round_trips_for_default_and_custom_roots(self):
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
                        patch.object(platform_support, "INSTALL_ROOT", target):
                    installer._write_data_root_marker(target)
                    self.assertEqual(installer.saved_data_root(target), expected.resolve())
                with self.environment(""), self.runtime_views(), \
                        patch.object(platform_support, "INSTALL_ROOT", target):
                    self.assertEqual(platform_support.saved_data_root(target),
                                     expected.resolve())
                    self.assertEqual(platform_support.data_root(target), expected.resolve())
                    self.assertEqual(windows_service.data_root(), expected.resolve())
                    self.assertEqual(installer.configured_data_root(target),
                                     expected.resolve())

    def test_saved_marker_wins_over_a_changed_platform_default(self):
        target = self.base / "install"
        target.mkdir()
        with self.environment(""), self.runtime_views():
            installer._write_data_root_marker(target)
            saved = installer.saved_data_root(target)

        other_xdg = self.base / "other-xdg"
        with patch.dict(os.environ, {
                "CODEX_WECHAT_DATA_ROOT": "",
                "XDG_DATA_HOME": str(other_xdg),
        }, clear=False), self.runtime_views(), \
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

    def test_runtime_manifest_is_canonical_over_legacy_markers(self):
        target = self.base / "install"
        target.mkdir()
        data = self.base / "manifest-data"
        config = self.base / "manifest-config/codex-wechat"
        state = self.base / "manifest-state/codex-wechat"
        write_runtime_root_markers(
            target, data_root=data, config_root=config, state_root=state, manifest=True)
        (target / platform_support.DATA_ROOT_FILE).write_text(
            str(self.base / "legacy-data") + "\n", encoding="utf-8")
        with self.environment(""), patch.object(platform_support, "INSTALL_ROOT", target):
            self.assertEqual(platform_support.saved_data_root(target), data)
            self.assertEqual(platform_support.saved_config_root(target), config)
            self.assertEqual(platform_support.saved_state_root(target), state)
            self.assertEqual(installer.saved_data_root(target), data)
            self.assertEqual(installer.saved_config_root(target), config)
            self.assertEqual(installer.saved_state_root(target), state)
            self.assertEqual(
                platform_support.saved_runtime_roots(target, require_complete=True),
                {"data": data, "config": config, "state": state},
            )

    def test_incomplete_legacy_runtime_markers_fail_the_strict_reader(self):
        target = self.base / "install"
        target.mkdir()
        (target / platform_support.DATA_ROOT_FILE).write_text(
            str(self.base / "data") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "路径标记不完整"):
            platform_support.saved_runtime_roots(target, require_complete=True)

    def test_invalid_runtime_manifest_fails_closed(self):
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
                    platform_support.saved_runtime_roots(target, require_complete=True)
                with self.assertRaisesRegex(ValueError, "运行时路径清单"):
                    installer.saved_data_root(target)

    def test_installer_can_replace_a_marker_but_runtime_rejects_unapplied_conflict(self):
        target = self.base / "install"
        target.mkdir()
        old = self.base / "old-data"
        new = self.base / "new-data"
        (target / platform_support.DATA_ROOT_FILE).write_text(
            str(old) + "\n", encoding="utf-8")

        with self.environment(str(new)), self.runtime_views(), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            with self.assertRaisesRegex(ValueError, "--reconfigure-roots"):
                installer.configured_data_root(target)
        with self.environment(str(new)), self.runtime_views(), \
                patch.object(platform_support, "INSTALL_ROOT", target), \
                patch.object(installer, "RECONFIGURE_ROOTS", True):
            self.assertEqual(installer.configured_data_root(target), new.resolve())
            with self.assertRaisesRegex(ValueError, "不一致"):
                platform_support.data_root(target)
            installer._write_data_root_marker(target)

        with self.environment(""), self.runtime_views(), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            self.assertEqual(installer.saved_data_root(target), new.resolve())
            self.assertEqual(platform_support.data_root(target), new.resolve())
            self.assertEqual(windows_service.data_root(), new.resolve())

    def test_invalid_markers_fail_closed_in_every_reader(self):
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
                patch.object(platform_support, "INSTALL_ROOT", target):
            with self.assertRaises(ValueError):
                installer.saved_data_root(target)
            with self.assertRaises(ValueError):
                platform_support.data_root(target)
            with self.assertRaises(ValueError):
                windows_service.data_root()

    @requires_symlinks
    def test_symlink_marker_fails_closed_in_every_reader(self):
        target = self.base / "install"
        target.mkdir()
        outside = self.base / "outside"
        outside.write_text(str(self.base / "data") + "\n", encoding="utf-8")
        marker = target / platform_support.DATA_ROOT_FILE
        marker.symlink_to(outside)
        with self.environment(""), self.runtime_views(), \
                patch.object(platform_support, "INSTALL_ROOT", target):
            with self.assertRaises(ValueError):
                installer.saved_data_root(target)
            with self.assertRaises(ValueError):
                platform_support.data_root(target)
            with self.assertRaises(ValueError):
                windows_service.data_root()


if __name__ == "__main__":
    unittest.main()
