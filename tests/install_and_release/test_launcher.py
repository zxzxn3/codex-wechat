"""The stable launcher resolves releases only through InstalledLayout."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat.launcher import command
from tests.support.helpers import install_platform, write_installation_layout


class LauncherTests(unittest.TestCase):
    def setUp(self):
        # The fixture is a Linux layout; the launcher resolves the host platform.
        self.enterContext(install_platform("linux"))

    def test_installed_command_uses_the_layout_release_python(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install = base / "install"
            layout = write_installation_layout(
                install,
                data_root=base / "data",
                config_root=base / "config",
                state_root=base / "state",
                platform="linux",
                release_files=True,
            )
            executable, environment = command(
                install, "codex_wechat", ["status"])

            self.assertEqual(executable[:6],
                             [str(layout.python_executable), "-s", "-P", "-u", "-m",
                              "codex_wechat"])
            self.assertEqual(executable[6:], ["status"])
            self.assertEqual(environment["CODEX_WECHAT_INSTALL_ROOT"], str(install))
            self.assertEqual(environment["PYTHONPATH"], str(layout.release_path))
            self.assertNotIn("PYTHONHOME", environment)

    def test_source_tree_uses_the_current_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                    "PYTHONPATH": str(root / "ambient"),
                    "PYTHONHOME": str(root / "ambient-home"),
            }):
                executable, environment = command(root, "codex_wechat.desktop", [])

            self.assertEqual(executable[1:], ["-s", "-P", "-u", "-m", "codex_wechat.desktop"])
            self.assertEqual(environment["PYTHONPATH"], str(root))
            self.assertNotIn("PYTHONHOME", environment)

    def test_legacy_layout_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "current").write_text("a" * 16 + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "旧安装布局"):
                command(root, "codex_wechat", [])


if __name__ == "__main__":
    unittest.main()
