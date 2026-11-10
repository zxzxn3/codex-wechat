"""InstalledLayout is the single source of installed paths and release identity."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat import platform_support
from codex_wechat.install_layout import (INSTALLATION_FILE, InstallLayoutError,
                                         InstalledLayout)
from tests.support.helpers import (linux_install_platform, requires_symlinks,
                                   write_installation_layout)


RELEASE_ID = "a" * 16


class InstalledLayoutTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(linux_install_platform())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.target = self.base / "install"
        self.data = self.base / "data"
        self.config = self.base / "config/codex-wechat"
        self.state = self.base / "state/codex-wechat"

    def layout(self):
        return write_installation_layout(
            self.target,
            release_id=RELEASE_ID,
            data_root=self.data,
            config_root=self.config,
            state_root=self.state,
        )

    def test_round_trip_publishes_release_and_roots_as_one_fact(self):
        layout = self.layout()
        loaded = InstalledLayout.load(
            self.target, platform="linux", required=True)

        self.assertEqual(loaded, layout)
        self.assertEqual(loaded.release_id, RELEASE_ID)
        self.assertEqual(loaded.release_path,
                         self.target / "releases" / RELEASE_ID)
        self.assertEqual(loaded.runtime_paths().config_file,
                         self.config / "config.json")
        self.assertEqual(loaded.runtime_paths().state_dir, self.state)
        self.assertEqual(json.loads(
            (self.target / INSTALLATION_FILE).read_text(encoding="utf-8")
        )["releaseId"], RELEASE_ID)
        for legacy in ("current", ".runtime-roots", ".data-root",
                       ".config-root", ".state-root"):
            self.assertFalse((self.target / legacy).exists())

    def test_platform_support_reads_the_contract_instead_of_markers(self):
        self.layout()
        with patch.object(platform_support, "IS_WINDOWS", False), \
                patch.object(platform_support, "INSTALL_ROOT", self.target):
            paths = platform_support.runtime_paths()

        self.assertEqual(paths.data_root, self.data)
        self.assertEqual(paths.config_file, self.config / "config.json")
        self.assertEqual(paths.state_dir, self.state)

    def test_legacy_layout_is_rejected_without_guessing(self):
        self.target.mkdir()
        (self.target / "current").write_text("legacy\n", encoding="utf-8")
        (self.target / ".data-root").write_text(
            str(self.data) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(InstallLayoutError, "旧安装布局"):
            InstalledLayout.load(self.target, platform="linux")

    def test_legacy_markers_remain_invalid_alongside_a_manifest(self):
        self.layout()
        (self.target / "current").write_text("legacy\n", encoding="utf-8")
        with self.assertRaisesRegex(InstallLayoutError, "旧安装布局"):
            InstalledLayout.load(self.target, platform="linux")

    def test_each_legacy_marker_is_rejected(self):
        for name in (".runtime-roots", ".data-root", ".config-root", ".state-root"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / name).write_text("legacy\n", encoding="utf-8")
                with self.assertRaisesRegex(InstallLayoutError, "旧安装布局"):
                    InstalledLayout.load(root, platform="linux")

    def test_invalid_manifest_fails_at_the_contract(self):
        self.target.mkdir()
        path = self.target / INSTALLATION_FILE
        cases = {
            "not-json": "{",
            "wrong-version": json.dumps({
                "version": 2,
                "platform": "linux",
                "installRoot": str(self.target),
                "releaseId": RELEASE_ID,
                "dataRoot": str(self.data),
                "configRoot": str(self.config),
                "stateRoot": str(self.state),
            }),
            "extra-field": json.dumps({
                "version": 1,
                "platform": "linux",
                "installRoot": str(self.target),
                "releaseId": RELEASE_ID,
                "dataRoot": str(self.data),
                "configRoot": str(self.config),
                "stateRoot": str(self.state),
                "extra": True,
            }),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(InstallLayoutError):
                    InstalledLayout.load(self.target, platform="linux")

    def test_wrong_platform_and_install_root_are_rejected(self):
        layout = self.layout()
        with self.assertRaisesRegex(InstallLayoutError, "平台不匹配"):
            InstalledLayout.load(self.target, platform="windows")

        value = layout.as_json()
        value["installRoot"] = str(self.base / "other")
        (self.target / INSTALLATION_FILE).write_text(
            json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(InstallLayoutError, "其它安装目录"):
            InstalledLayout.load(self.target, platform="linux")

    def test_planned_layout_preserves_roots_and_rejects_data_override(self):
        layout = self.layout()
        with patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.base / "ambient-config"),
            "XDG_STATE_HOME": str(self.base / "ambient-state"),
            "CODEX_WECHAT_DATA_ROOT": "",
        }, clear=False):
            updated = InstalledLayout.planned(
                self.target,
                "b" * 16,
                platform="linux",
                existing=layout,
            )
        self.assertEqual(updated.data_root, layout.data_root)
        self.assertEqual(updated.config_root, layout.config_root)
        self.assertEqual(updated.state_root, layout.state_root)

        with patch.dict(os.environ, {
            "CODEX_WECHAT_DATA_ROOT": str(self.base / "other-data"),
        }, clear=False):
            with self.assertRaisesRegex(InstallLayoutError, "固定"):
                InstalledLayout.planned(
                    self.target,
                    "b" * 16,
                    platform="linux",
                    existing=layout,
                )

    def test_windows_layout_requires_one_data_root(self):
        with self.assertRaisesRegex(InstallLayoutError, "必须等于数据根"):
            InstalledLayout.create(
                self.target,
                RELEASE_ID,
                platform="windows",
                data_root=self.data,
                config_root=self.config,
                state_root=self.state,
            )

    def test_linux_purge_plan_contains_only_owned_leaves(self):
        layout = self.layout()
        plan = layout.purge_plan()
        self.assertEqual(
            [(item.role, item.path) for item in plan.items],
            [
                ("配置", self.config / "config.json"),
                ("配置临时文件", self.config / "config.json.tmp"),
                ("状态", self.state),
                ("工作区", self.data / "workspace"),
            ],
        )
        self.assertNotIn(self.data, plan.paths)

    @requires_symlinks
    def test_release_directory_must_not_be_a_link(self):
        layout = self.layout()
        external = self.base / "external-release"
        external.mkdir()
        shutil.rmtree(layout.release_path)
        layout.release_path.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(InstallLayoutError, "当前版本目录不能是符号链接"):
            layout.release_dir()

    @requires_symlinks
    def test_install_root_must_not_be_a_link(self):
        self.layout()
        alias = self.base / "install-alias"
        alias.symlink_to(self.target, target_is_directory=True)
        with self.assertRaisesRegex(InstallLayoutError, "安装根目录不能是符号链接"):
            InstalledLayout.load(alias, platform="linux")

    @requires_symlinks
    def test_purge_plan_rejects_a_reparse_component(self):
        external = self.base / "external"
        external.mkdir()
        state_home = self.base / "state-home"
        state_home.symlink_to(external, target_is_directory=True)
        layout = InstalledLayout.create(
            self.target,
            RELEASE_ID,
            platform="linux",
            data_root=self.data,
            config_root=self.config,
            state_root=state_home / "codex-wechat",
        )
        with self.assertRaisesRegex(InstallLayoutError, "符号链接"):
            layout.purge_plan()


if __name__ == "__main__":
    unittest.main()
