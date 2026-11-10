"""The stable launcher resolves releases only through InstalledLayout."""
from pathlib import Path
import tempfile
import unittest

from codex_wechat.launcher import command
from tests.support.helpers import write_installation_layout


class LauncherTests(unittest.TestCase):
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

            self.assertEqual(executable[:4],
                             [str(layout.python_executable), "-u", "-m", "codex_wechat"])
            self.assertEqual(executable[4:], ["status"])
            self.assertEqual(environment["CODEX_WECHAT_INSTALL_ROOT"], str(install))
            self.assertTrue(environment["PYTHONPATH"].startswith(str(layout.release_path)))

    def test_source_tree_uses_the_current_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable, environment = command(root, "codex_wechat.desktop", [])

            self.assertEqual(executable[2:], ["-m", "codex_wechat.desktop"])
            self.assertEqual(environment["PYTHONPATH"], str(root))

    def test_legacy_layout_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "current").write_text("a" * 16 + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "旧安装布局"):
                command(root, "codex_wechat", [])


if __name__ == "__main__":
    unittest.main()
