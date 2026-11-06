"""Release-pointer contracts for the stable Windows supervisor bootstrap."""
from test_support import requires_symlinks
import os
from pathlib import Path
import tempfile
import unittest

from codex_wechat import bootstrap_supervisor
from unittest.mock import patch

# `installer.release_identifier()` writes 16 hex digits; the pointer is validated
# against exactly that shape.
FIRST_RELEASE = "0f1e2d3c4b5a6978"
SECOND_RELEASE = "8877665544332211"
MISSING_RELEASE = "0123456789abcdef"


class BootstrapSupervisorTests(unittest.TestCase):
    def test_release_package_is_loaded_without_colliding_with_stable_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = []
            for identifier, value in ((FIRST_RELEASE, "one"), (SECOND_RELEASE, "two")):
                release = root / "releases" / identifier
                package = release / "codex_wechat"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("", encoding="utf-8")
                (package / "helper.py").write_text(
                    f"VALUE = {value!r}\n", encoding="utf-8")
                (package / "windows_service.py").write_text(
                    "from .helper import VALUE\n"
                    "def marker():\n"
                    "    return VALUE\n",
                    encoding="utf-8",
                )
                module = bootstrap_supervisor.load_supervisor(release)
                self.assertTrue(module.__name__.startswith("_codex_wechat_release_"))
                self.assertEqual(module.marker(), value)
                loaded.append(module)
            self.assertEqual([module.marker() for module in loaded], ["one", "two"])

    def test_missing_current_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "当前版本指针 current 缺失"):
                bootstrap_supervisor.active_release(Path(directory))

    def test_current_pointer_selects_immutable_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / FIRST_RELEASE
            package = release / "codex_wechat"
            package.mkdir(parents=True)
            (package / "windows_service.py").write_text("", encoding="utf-8")
            (root / "current").write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            self.assertEqual(bootstrap_supervisor.active_release(root), release.resolve())

    def test_invalid_or_incomplete_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "current").write_text("../escape\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无效版本标识"):
                bootstrap_supervisor.active_release(root)
            (root / "current").write_text("ABC123\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无效版本标识"):
                bootstrap_supervisor.active_release(root)
            (root / "current").write_text(MISSING_RELEASE + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "当前版本不完整"):
                bootstrap_supervisor.active_release(root)

    def test_dotdot_in_the_pointer_cannot_escape_to_the_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "windows_service.py").write_text("", encoding="utf-8")
            (root / "releases").mkdir()
            (root / "current").write_text("..\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无效版本标识"):
                bootstrap_supervisor.active_release(root)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    @requires_symlinks
    def test_pointer_and_release_paths_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            (root / "current").symlink_to(external / "pointer")
            with self.assertRaisesRegex(ValueError, "不能是符号链接或重解析点"):
                bootstrap_supervisor.active_release(root)

            (root / "current").unlink()
            outside_releases = root / "outside"
            release = outside_releases / FIRST_RELEASE
            package = release / "codex_wechat"
            package.mkdir(parents=True)
            (package / "windows_service.py").write_text("", encoding="utf-8")
            (root / "releases").symlink_to(outside_releases, target_is_directory=True)
            (root / "current").write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "releases 目录不能是符号链接"):
                bootstrap_supervisor.active_release(root)


class BootstrapSupervisorLoopTests(unittest.TestCase):
    def _root(self, directory, *identifiers):
        root = Path(directory) / "app"
        for identifier in identifiers:
            release = root / "releases" / identifier
            (release / ".venv" / "Scripts").mkdir(parents=True)
            (release / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
            package = release / "codex_wechat"
            package.mkdir()
            (package / "windows_service.py").write_text("", encoding="utf-8")
        root.mkdir(exist_ok=True)
        (root / bootstrap_supervisor.DATA_ROOT_FILE).write_text(
            str(root) + "\n", encoding="utf-8")
        return root

    def _argv(self, directory, root):
        return ["supervise", "--install-root", str(root)]

    def test_marker_selects_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            (root / "current").write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            marker_root = Path(directory) / "marker-data"
            marker_root.mkdir()
            (root / bootstrap_supervisor.DATA_ROOT_FILE).write_text(
                str(marker_root) + "\n", encoding="utf-8")
            seen = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, state_dir, config, log_path, **_kwargs):
                    seen.append((state_dir, config, log_path))
                    return 0

            argv = self._argv(directory, root)
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(bootstrap_supervisor, "data_root",
                                 return_value=marker_root), \
                    patch.object(bootstrap_supervisor, "load_supervisor",
                                 lambda release: Module()):
                os.environ.pop("CODEX_WECHAT_DATA_ROOT", None)
                code = bootstrap_supervisor.main(argv)

            self.assertEqual(code, 0)
            self.assertEqual(seen, [(
                marker_root / ".state",
                marker_root / "config.json",
                marker_root / "logs" / "service.log",
            )])

    def test_default_root_marker_resolves_under_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            (root / "current").write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            (root / bootstrap_supervisor.DATA_ROOT_FILE).write_text(
                str(root) + "\n", encoding="utf-8")
            seen = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, state_dir, config, log_path, **_kwargs):
                    seen.append((state_dir, config, log_path))
                    return 0

            argv = self._argv(directory, root)
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(bootstrap_supervisor, "data_root", return_value=root), \
                    patch.object(bootstrap_supervisor, "load_supervisor",
                                 lambda release: Module()):
                os.environ.pop("CODEX_WECHAT_DATA_ROOT", None)
                code = bootstrap_supervisor.main(argv)

            self.assertEqual(code, 0)
            self.assertEqual(seen, [(
                root / ".state",
                root / "config.json",
                root / "logs" / "service.log",
            )])

    def test_missing_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            (root / "current").write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            (root / bootstrap_supervisor.DATA_ROOT_FILE).unlink()
            with self.assertRaisesRegex(ValueError, "缺少数据根标记"):
                bootstrap_supervisor.main(self._argv(directory, root))

    def test_switch_code_reloads_the_release_selected_by_the_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE, SECOND_RELEASE)
            pointer = root / "current"
            pointer.write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            first = (root / "releases" / FIRST_RELEASE).resolve()
            loaded = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, *_args, **_kwargs):
                    if project == first:
                        pointer.write_text(SECOND_RELEASE + "\n", encoding="utf-8")
                        return 76
                    return 0

            def load(release):
                loaded.append(release.name)
                return Module()

            with patch.dict(os.environ, {"CODEX_WECHAT_DATA_ROOT": ""}), \
                 patch.object(bootstrap_supervisor.time, "sleep"), \
                 patch.object(bootstrap_supervisor, "load_supervisor", load):
                code = bootstrap_supervisor.main(self._argv(directory, root))

            self.assertEqual(code, 0)
            self.assertEqual(loaded, [FIRST_RELEASE, SECOND_RELEASE])
            log = (root / "logs" / "service.log").read_text(encoding="utf-8")
            self.assertIn(f"版本指针已变化，正在加载 {SECOND_RELEASE}", log)

    def test_broken_pointer_keeps_the_running_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            pointer = root / "current"
            pointer.write_text(FIRST_RELEASE + "\n", encoding="utf-8")
            runs = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, *_args, **_kwargs):
                    runs.append(project.name)
                    if len(runs) == 1:
                        pointer.write_text(MISSING_RELEASE + "\n", encoding="utf-8")
                        return 76
                    return 0

            with patch.dict(os.environ, {"CODEX_WECHAT_DATA_ROOT": ""}), \
                 patch.object(bootstrap_supervisor.time, "sleep"), \
                 patch.object(bootstrap_supervisor, "load_supervisor",
                              lambda release: Module()):
                code = bootstrap_supervisor.main(self._argv(directory, root))

            self.assertEqual(code, 0)
            self.assertEqual(runs, [FIRST_RELEASE, FIRST_RELEASE])
            log = (root / "logs" / "service.log").read_text(encoding="utf-8")
            # The same broken pointer is reported once, not on every restart.
            self.assertEqual(log.count("release switch:"), 1)


if __name__ == "__main__":
    unittest.main()
