"""Contracts between the uv-built wheel and stable service assets."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer


ROOT = Path(__file__).resolve().parents[2]


class ReleaseLayoutTests(unittest.TestCase):
    def test_packaging_metadata_declares_the_application_distribution(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "codex-wechat"', text)
        self.assertIn('packages = ["codex_wechat"]', text)
        self.assertIn('build-backend = "hatchling.build"', text)

    def test_stable_assets_exist_for_each_platform(self):
        cases = (
            ("linux", installer.STABLE_LINUX_FILES),
            ("windows", installer.STABLE_WINDOWS_FILES),
        )
        for platform, entries in cases:
            with self.subTest(platform=platform):
                for relative in entries:
                    self.assertTrue((ROOT / relative).is_file(), relative)
                self.assertIn(Path("config.default.json"), entries)
        self.assertIn(Path("windows/task-service.ps1"),
                      installer.STABLE_WINDOWS_FILES)
        self.assertNotIn(Path("windows/task-service.ps1"),
                         installer.STABLE_LINUX_FILES)

    def test_install_bootstrap_copies_scripts_and_runtime_resources(self):
        for platform in ("linux", "windows"):
            with self.subTest(platform=platform), \
                    tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "app"
                target.mkdir()
                with patch.object(installer, "IS_WINDOWS", platform == "windows"):
                    installer.install_bootstrap(ROOT, target)
                self.assertTrue((target / "config.default.json").is_file())
                self.assertTrue(
                    (target / "skills/wechat-attachments/SKILL.md").is_file())
                if platform == "windows":
                    self.assertTrue(
                        (target / "windows/task-service.ps1").is_file())

    def test_required_files_describe_packaging_inputs(self):
        self.assertIn(Path("pyproject.toml"), installer.REQUIRED_FILES)
        self.assertFalse(any(
            path.name.startswith("requirements")
            for path in installer.REQUIRED_FILES
        ))


if __name__ == "__main__":
    unittest.main()
