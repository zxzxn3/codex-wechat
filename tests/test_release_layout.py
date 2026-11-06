"""Contracts for the installer's stable payload and source manifest."""
import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import installer


ROOT = Path(__file__).resolve().parents[1]


class ReleaseLayoutTests(unittest.TestCase):
    def test_stable_payloads_exist_and_are_platform_specific(self):
        for name, stable in (
                ("linux", installer.STABLE_LINUX_FILES),
                ("windows", installer.STABLE_WINDOWS_FILES)):
            with self.subTest(platform=name):
                for relative in stable:
                    self.assertTrue((ROOT / relative).is_file(), relative)
        self.assertIn(Path("windows/task-service.ps1"), installer.STABLE_WINDOWS_FILES)
        self.assertNotIn(Path("windows/task-service.ps1"), installer.STABLE_LINUX_FILES)

    def test_stable_python_imports_are_present_in_the_manifest(self):
        local_modules = {path.stem for path in ROOT.glob("*.py")}
        for name, stable in (
                ("linux", installer.STABLE_LINUX_FILES),
                ("windows", installer.STABLE_WINDOWS_FILES)):
            declared = set(stable)
            for relative in stable:
                if relative.suffix != ".py":
                    continue
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                for node in tree.body:
                    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        module = node.module.split(".", 1)[0]
                    elif isinstance(node, ast.Import):
                        module = next(
                            (alias.name.split(".", 1)[0] for alias in node.names
                             if alias.name.split(".", 1)[0] in local_modules),
                            "",
                        )
                    else:
                        continue
                    if module in local_modules:
                        with self.subTest(platform=name, file=str(relative), module=module):
                            self.assertIn(Path(module + ".py"), declared)

    def test_stable_installer_nested_imports_are_in_both_payloads(self):
        local_modules = {path.stem for path in ROOT.glob("*.py")}
        tree = ast.parse((ROOT / "installer.py").read_text(encoding="utf-8"))
        for name, stable in (
                ("linux", installer.STABLE_LINUX_FILES),
                ("windows", installer.STABLE_WINDOWS_FILES)):
            declared = set(stable)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    module = node.module.split(".", 1)[0]
                elif isinstance(node, ast.Import):
                    module = next(
                        (alias.name.split(".", 1)[0] for alias in node.names
                         if alias.name.split(".", 1)[0] in local_modules),
                        "",
                    )
                else:
                    continue
                if module in local_modules:
                    with self.subTest(platform=name, module=module):
                        self.assertIn(Path(module + ".py"), declared)

    def test_required_files_are_read_from_the_actual_payload(self):
        self.assertIn(Path("LICENSE"), installer.REQUIRED_FILES)
        missing = sorted(str(path) for path in installer.REQUIRED_FILES
                         if not (ROOT / path).is_file())
        self.assertEqual(missing, [])

    def test_install_bootstrap_creates_nested_windows_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "app"
            target.mkdir()
            with patch.object(installer, "IS_WINDOWS", True):
                installer.install_bootstrap(ROOT, target)
            self.assertTrue((target / "windows/task-service.ps1").is_file())

    def test_installed_stable_payload_imports_without_the_source_tree(self):
        cases = (
            ("linux", ("installer", "release", "platform_support", "storage",
                       "service_adapter", "service_control", "subprocess_io",
                       "uninstall")),
            ("windows", ("installer", "release", "platform_support", "storage",
                         "log_rotation", "native_codex", "bootstrap_supervisor")),
        )
        probe = (
            "import importlib, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "for name in sys.argv[2:]:\n"
            "    importlib.import_module(name)\n"
        )
        for platform_name, modules in cases:
            with self.subTest(platform=platform_name), \
                    tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "app"
                target.mkdir()
                with patch.object(installer, "IS_WINDOWS", platform_name == "windows"):
                    installer.install_bootstrap(ROOT, target)
                env = os.environ.copy()
                env["CODEX_WECHAT_INSTALL_ROOT"] = str(target)
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                result = subprocess.run(
                    [sys.executable, "-I", "-c", probe, str(target), *modules],
                    cwd=target,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(
                    result.returncode, 0,
                    f"stable payload import failed:\n{result.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
