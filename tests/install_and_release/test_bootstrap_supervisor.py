"""Installed-layout contracts for the stable Windows supervisor bootstrap."""
from pathlib import Path
import tempfile
import unittest

from codex_wechat import bootstrap_supervisor
from codex_wechat.install_layout import INSTALLATION_FILE
from tests.support.helpers import write_installation_layout
from unittest.mock import patch

# `installer.release_identifier()` writes 16 hex digits; the layout contract
# validates that shape before selecting a release.
FIRST_RELEASE = "0f1e2d3c4b5a6978"
SECOND_RELEASE = "8877665544332211"


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

    def test_manifest_selects_immutable_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / FIRST_RELEASE
            package = release / "codex_wechat"
            package.mkdir(parents=True)
            (package / "app.py").write_text("", encoding="utf-8")
            (package / "windows_service.py").write_text("", encoding="utf-8")
            write_installation_layout(
                root,
                release_id=FIRST_RELEASE,
                data_root=root,
                config_root=root,
                state_root=root,
                platform="windows",
                create_release=False,
            )
            self.assertEqual(bootstrap_supervisor.active_release(root), release.resolve())

    def test_missing_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "安装清单缺失"):
                bootstrap_supervisor.active_release(Path(directory))


class BootstrapSupervisorLoopTests(unittest.TestCase):
    def _root(self, directory, *identifiers):
        root = Path(directory) / "app"
        for identifier in identifiers:
            release = root / "releases" / identifier
            (release / ".venv" / "Scripts").mkdir(parents=True)
            (release / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
            package = release / "codex_wechat"
            package.mkdir()
            (package / "app.py").write_text("", encoding="utf-8")
            (package / "windows_service.py").write_text("", encoding="utf-8")
        root.mkdir(exist_ok=True)
        return root

    def _argv(self, directory, root):
        return ["supervise", "--install-root", str(root)]

    def test_manifest_selects_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            marker_root = Path(directory) / "marker-data"
            marker_root.mkdir()
            write_installation_layout(
                root,
                release_id=FIRST_RELEASE,
                data_root=marker_root,
                config_root=marker_root,
                state_root=marker_root,
                platform="windows",
                create_release=False,
            )
            seen = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, state_dir, config, log_path, **_kwargs):
                    seen.append((state_dir, config, log_path))
                    return 0

            argv = self._argv(directory, root)
            with patch.object(bootstrap_supervisor, "load_supervisor",
                                 lambda release: Module()):
                code = bootstrap_supervisor.main(argv)

            self.assertEqual(code, 0)
            self.assertEqual(seen, [(
                marker_root / ".state",
                marker_root / "config.json",
                marker_root / "logs" / "service.log",
            )])

    def test_missing_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            with self.assertRaisesRegex(ValueError, "安装清单缺失"):
                bootstrap_supervisor.main(self._argv(directory, root))

    def test_switch_code_reloads_the_release_selected_by_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE, SECOND_RELEASE)
            write_installation_layout(
                root,
                release_id=FIRST_RELEASE,
                data_root=root,
                config_root=root,
                state_root=root,
                platform="windows",
                create_release=False,
            )
            first = (root / "releases" / FIRST_RELEASE).resolve()
            loaded = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, *_args, **_kwargs):
                    if project == first:
                        write_installation_layout(
                            root,
                            release_id=SECOND_RELEASE,
                            data_root=root,
                            config_root=root,
                            state_root=root,
                            platform="windows",
                            create_release=False,
                        )
                        return 76
                    return 0

            def load(release):
                loaded.append(release.name)
                return Module()

            with patch.object(bootstrap_supervisor.time, "sleep"), \
                 patch.object(bootstrap_supervisor, "load_supervisor", load):
                code = bootstrap_supervisor.main(self._argv(directory, root))

            self.assertEqual(code, 0)
            self.assertEqual(loaded, [FIRST_RELEASE, SECOND_RELEASE])
            log = (root / "logs" / "service.log").read_text(encoding="utf-8")
            self.assertIn(f"当前版本已变化，正在加载 {SECOND_RELEASE}", log)

    def test_broken_manifest_keeps_the_running_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, FIRST_RELEASE)
            write_installation_layout(
                root,
                release_id=FIRST_RELEASE,
                data_root=root,
                config_root=root,
                state_root=root,
                platform="windows",
                create_release=False,
            )
            runs = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, *_args, **_kwargs):
                    runs.append(project.name)
                    if len(runs) == 1:
                        (root / INSTALLATION_FILE).write_text("{", encoding="utf-8")
                        return 76
                    return 0

            with patch.object(bootstrap_supervisor.time, "sleep"), \
                 patch.object(bootstrap_supervisor, "load_supervisor",
                              lambda release: Module()):
                code = bootstrap_supervisor.main(self._argv(directory, root))

            self.assertEqual(code, 0)
            self.assertEqual(runs, [FIRST_RELEASE, FIRST_RELEASE])
            log = (root / "logs" / "service.log").read_text(encoding="utf-8")
            # The same broken manifest is reported once, not on every restart.
            self.assertEqual(log.count("release switch:"), 1)


if __name__ == "__main__":
    unittest.main()
