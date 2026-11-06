"""Contracts for the installer's stable payload and source manifest."""
import ast
import os
import re
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
                for relative in installer._stable_files(ROOT):
                    self.assertTrue((ROOT / relative).is_file(), relative)
                self.assertIn(Path("codex_wechat/app.py"), stable + installer._package_files(ROOT))
        self.assertIn(Path("windows/task-service.ps1"), installer.STABLE_WINDOWS_FILES)
        self.assertNotIn(Path("windows/task-service.ps1"), installer.STABLE_LINUX_FILES)

    def test_stable_python_imports_are_present_in_the_manifest(self):
        package_files = installer._package_files(ROOT)
        for name, stable in (
                ("linux", installer.STABLE_LINUX_FILES),
                ("windows", installer.STABLE_WINDOWS_FILES)):
            declared = set(stable) | set(package_files)
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
                             if alias.name == "codex_wechat"),
                            "",
                        )
                    else:
                        continue
                    if module == "codex_wechat":
                        with self.subTest(platform=name, file=str(relative), module=module):
                            self.assertIn(Path("codex_wechat/__init__.py"), declared)

    def test_stable_installer_nested_imports_are_in_both_payloads(self):
        package_files = set(installer._package_files(ROOT))
        tree = ast.parse((ROOT / "installer.py").read_text(encoding="utf-8"))
        for name, stable in (
                ("linux", installer.STABLE_LINUX_FILES),
                ("windows", installer.STABLE_WINDOWS_FILES)):
            declared = set(stable) | package_files
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    module = node.module.split(".", 1)[0]
                elif isinstance(node, ast.Import):
                    module = next(
                        (alias.name.split(".", 1)[0] for alias in node.names
                         if alias.name.split(".", 1)[0] == "codex_wechat"),
                        "",
                    )
                else:
                    continue
                if module == "codex_wechat":
                    with self.subTest(platform=name, module=module):
                        self.assertIn(Path("codex_wechat/__init__.py"), declared)

    def test_runtime_version_requirements_match_the_manifests(self):
        # The installer probe hard-codes the pins because it runs before the
        # release can import anything; keep the two from drifting apart.
        declared = {}
        for name in ("requirements.txt", "requirements-windows.txt"):
            for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("-") or ";" in line:
                    continue
                package = re.split(r"[<>=!~\[]", line, 1)[0].strip()
                declared[package] = line[len(package):].strip()
        probed = installer.RUNTIME_VERSION_REQUIREMENTS
        for package, specifier in probed.items():
            with self.subTest(package=package):
                self.assertEqual(declared.get(package), specifier)
        for package, specifier in declared.items():
            if specifier and package not in probed:
                self.fail(f"{package}{specifier} is pinned but not probed at install time")

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
            self.assertTrue((target / "codex_wechat/app.py").is_file())

    def test_installed_stable_payload_imports_without_the_source_tree(self):
        cases = (
            ("linux", ("installer", "codex_wechat.platform_support",
                       "codex_wechat.storage", "codex_wechat.service_adapter",
                       "codex_wechat.service_control", "codex_wechat.subprocess_io",
                       "codex_wechat.uninstall")),
            ("windows", ("installer", "codex_wechat.platform_support",
                         "codex_wechat.storage", "codex_wechat.log_rotation",
                         "codex_wechat.native_codex",
                         "codex_wechat.bootstrap_supervisor",
                         "codex_wechat.windows_service")),
        )
        probe = (
            "import importlib, pathlib, sys\n"
            "root = pathlib.Path(sys.argv[1]).resolve()\n"
            "sys.path.insert(0, str(root))\n"
            "for name in sys.argv[2:]:\n"
            "    module = importlib.import_module(name)\n"
            "    if module.__file__ is None or not pathlib.Path(module.__file__).resolve().is_relative_to(root):\n"
            "        raise SystemExit(f'{name} loaded outside the stable payload: {module.__file__}')\n"
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
                    [sys.executable, "-I", "-S", "-c", probe, str(target), *modules],
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
