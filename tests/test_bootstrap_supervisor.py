"""Release-pointer contracts for the stable Windows supervisor bootstrap."""
from test_support import requires_symlinks
import os
from pathlib import Path
import tempfile
import unittest

import bootstrap_supervisor
from unittest.mock import patch


class BootstrapSupervisorTests(unittest.TestCase):
    def test_legacy_layout_uses_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "windows_service.py").write_text("", encoding="utf-8")
            self.assertEqual(bootstrap_supervisor.active_release(root), root.resolve())

    def test_current_pointer_selects_immutable_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / "abc123"
            release.mkdir(parents=True)
            (release / "windows_service.py").write_text("", encoding="utf-8")
            (root / "current").write_text("abc123\n", encoding="utf-8")
            self.assertEqual(bootstrap_supervisor.active_release(root), release.resolve())

    def test_invalid_or_incomplete_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "current").write_text("../escape\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid release id"):
                bootstrap_supervisor.active_release(root)
            (root / "current").write_text("missing\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                bootstrap_supervisor.active_release(root)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    @requires_symlinks
    def test_pointer_and_release_paths_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            (root / "current").symlink_to(external / "pointer")
            with self.assertRaisesRegex(ValueError, "pointer must not be a symlink"):
                bootstrap_supervisor.active_release(root)

            (root / "current").unlink()
            outside_releases = root / "outside"
            release = outside_releases / "abc123"
            release.mkdir(parents=True)
            (release / "windows_service.py").write_text("", encoding="utf-8")
            (root / "releases").symlink_to(outside_releases, target_is_directory=True)
            (root / "current").write_text("abc123\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "releases directory must not be a symlink"):
                bootstrap_supervisor.active_release(root)


class BootstrapSupervisorLoopTests(unittest.TestCase):
    def _root(self, directory, *identifiers):
        root = Path(directory) / "app"
        for identifier in identifiers:
            release = root / "releases" / identifier
            (release / ".venv" / "Scripts").mkdir(parents=True)
            (release / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
            (release / "windows_service.py").write_text("", encoding="utf-8")
        return root

    def _argv(self, directory, root):
        return ["supervise", "--install-root", str(root),
                "--state-dir", str(Path(directory) / ".state"),
                "--config", str(Path(directory) / "config.json"),
                "--log", str(Path(directory) / "service.log")]

    def test_switch_code_reloads_the_release_selected_by_the_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, "first", "second")
            pointer = root / "current"
            pointer.write_text("first\n", encoding="utf-8")
            first = (root / "releases" / "first").resolve()
            loaded = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, *_args, **_kwargs):
                    if project == first:
                        pointer.write_text("second\n", encoding="utf-8")
                        return 76
                    return 0

            def load(release):
                loaded.append(release.name)
                return Module()

            with patch.dict(os.environ, {}), \
                 patch.object(bootstrap_supervisor, "load_supervisor", load):
                code = bootstrap_supervisor.main(self._argv(directory, root))

            self.assertEqual(code, 0)
            self.assertEqual(loaded, ["first", "second"])

    def test_broken_pointer_keeps_the_running_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, "first")
            pointer = root / "current"
            pointer.write_text("first\n", encoding="utf-8")
            runs = []

            class Module:
                RELEASE_SWITCH_EXIT_CODE = 76

                async def supervise(self, project, *_args, **_kwargs):
                    runs.append(project.name)
                    if len(runs) == 1:
                        pointer.write_text("missing\n", encoding="utf-8")
                        return 76
                    return 0

            with patch.dict(os.environ, {}), \
                 patch.object(bootstrap_supervisor, "load_supervisor",
                              lambda release: Module()):
                code = bootstrap_supervisor.main(self._argv(directory, root))

            self.assertEqual(code, 0)
            self.assertEqual(runs, ["first", "first"])


if __name__ == "__main__":
    unittest.main()
