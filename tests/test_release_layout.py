"""Contracts for the installer's stable payload and source manifest."""
import ast
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
