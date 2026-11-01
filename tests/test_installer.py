"""Offline installer and update contracts using disposable source trees."""
import contextlib
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.target = self.base / "target"
        self.make_project(self.source)
        self.enterContext(patch.object(installer, "introduction", return_value=True))
        self.enterContext(patch.object(installer, "prepare_runtime"))
        self.enterContext(patch.object(installer, "next_steps"))

    def make_project(self, root, *, version="one"):
        root.mkdir(parents=True, exist_ok=True)
        for relative in installer.REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{relative}: {version}\n", encoding="utf-8")
        (root / "codex-wechat").chmod(0o755)
        (root / "install.sh").chmod(0o755)
        (root / "extra.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
        (root / "docs").mkdir(exist_ok=True)
        (root / "docs/guide.md").write_text("guide\n", encoding="utf-8")
        (root / "skills/demo").mkdir(parents=True, exist_ok=True)
        (root / "skills/demo/SKILL.md").write_text("skill\n", encoding="utf-8")
        return root

    def add_private_data(self, root):
        private = {
            ".state/account.json": "private",
            ".venv/bin/python": "private",
            ".update-backups/old/file.py": "private",
            "workspace/notes.txt": "private",
            "config.json": "private",
            "__pycache__/demo.pyc": "private",
            "dist/codex-wechat": "private",
        }
        for relative, content in private.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return private

    def test_payload_excludes_private_runtime_content(self):
        self.add_private_data(self.source)
        files = installer.project_files(self.source)
        self.assertIn(Path("extra.py"), files)
        self.assertIn(Path("docs/guide.md"), files)
        for relative in (
            Path("config.json"),
            Path(".state/account.json"),
            Path(".venv/bin/python"),
            Path(".update-backups/old/file.py"),
            Path("workspace/notes.txt"),
        ):
            self.assertNotIn(relative, files)

    def test_payload_refuses_symlink(self):
        link = self.source / "link.py"
        link.symlink_to(self.source / "extra.py")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.project_files(self.source)

    def test_payload_refuses_symlinked_source_and_directory(self):
        source_link = self.base / "source-link"
        source_link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "源码目录"):
            installer.project_files(source_link)
        directory_link = self.source / "linked-docs"
        directory_link.symlink_to(self.source / "docs", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.project_files(self.source)

    def test_install_copies_only_project_files_and_keeps_modes(self):
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        self.assertEqual((self.target / "extra.py").read_text(), "VERSION = 'one'\n")
        self.assertTrue((self.target / "docs/guide.md").is_file())
        self.assertTrue((self.target / "codex-wechat").stat().st_mode & stat.S_IXUSR)
        self.assertFalse((self.target / "config.json").exists())
        self.assertFalse((self.target / ".state").exists())

    def test_cancel_and_dry_run_do_not_write(self):
        with patch("builtins.input", return_value="n"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        self.assertFalse(self.target.exists())
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target, dry_run=True), 0)
        self.assertFalse(self.target.exists())

    def test_nonempty_target_requires_update(self):
        self.target.mkdir()
        marker = self.target / "unrelated"
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.install(self.source, self.target)
            prompt.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")

    def test_target_root_symlink_is_rejected(self):
        real_target = self.base / "real-target"
        real_target.mkdir()
        self.target.symlink_to(real_target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.install(self.source, self.target)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.update(self.source, self.target)

    def test_existing_source_can_prepare_in_place(self):
        marker = self.source / "workspace/keep.txt"
        marker.parent.mkdir()
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.source), 0)
        self.assertEqual(marker.read_text(), "keep")
        installer.prepare_runtime.assert_called_with(self.source, update=False)

    def test_update_backs_up_replacements_and_preserves_private_data(self):
        installer.install_files(self.source, self.target, installer.project_files(self.source))
        self.add_private_data(self.target)
        unknown = self.target / "local-note.txt"
        unknown.write_text("keep", encoding="utf-8")
        self.make_project(self.source, version="two")
        (self.source / "new.py").write_text("NEW = True\n", encoding="utf-8")
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.update(self.source, self.target), 0)
        self.assertEqual((self.target / "extra.py").read_text(), "VERSION = 'two'\n")
        self.assertTrue((self.target / "new.py").is_file())
        self.assertEqual(unknown.read_text(), "keep")
        self.assertEqual((self.target / "config.json").read_text(), "private")
        self.assertEqual((self.target / ".state/account.json").read_text(), "private")
        backups = sorted((self.target / ".update-backups").glob("update-*"))
        self.assertTrue(backups)
        self.assertEqual((backups[-1] / "extra.py").read_text(), "VERSION = 'one'\n")

    def test_update_is_noop_when_files_match(self):
        installer.install_files(self.source, self.target, installer.project_files(self.source))
        with patch.object(installer, "introduction") as intro:
            self.assertEqual(installer.update(self.source, self.target), 0)
            intro.assert_not_called()
        self.assertEqual((self.target / "extra.py").read_text(), "VERSION = 'one'\n")

    def test_update_rejects_same_checkout(self):
        with self.assertRaisesRegex(ValueError, "另一份"):
            installer.update(self.source, self.source)

    def test_update_rechecks_after_confirmation(self):
        installer.install_files(self.source, self.target, installer.project_files(self.source))
        self.make_project(self.source, version="two")

        def change_target(_):
            (self.target / "extra.py").write_text("changed while reading\n", encoding="utf-8")
            return "y"

        with patch("builtins.input", side_effect=change_target):
            with self.assertRaisesRegex(ValueError, "发生变化"):
                installer.update(self.source, self.target)
        self.assertEqual((self.target / "extra.py").read_text(), "changed while reading\n")

    def test_update_refuses_symlinked_target_file(self):
        installer.install_files(self.source, self.target, installer.project_files(self.source))
        extra = self.target / "extra.py"
        outside = self.base / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        extra.unlink()
        extra.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.update(self.source, self.target)
        self.assertEqual(outside.read_text(), "outside\n")

    def test_optional_logo_is_displayed_only_when_present(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            installer.logo_banner(self.source)
        self.assertEqual(output.getvalue(), "")
        logo = self.source / installer.LOGO_PATH
        logo.parent.mkdir(parents=True, exist_ok=True)
        logo.write_text("future art\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(installer.sys.stdout, "isatty", return_value=True), \
             patch.object(installer.shutil, "get_terminal_size", return_value=os.terminal_size((80, 24))), \
             patch.dict(os.environ, {"TERM": "xterm"}):
            installer.logo_banner(self.source)
        self.assertIn("future art", output.getvalue())

    def test_default_target_is_platform_specific(self):
        with patch.object(installer, "IS_WINDOWS", False), \
             patch.object(installer.Path, "home", return_value=self.base / "home"):
            self.assertEqual(
                installer.default_target(),
                self.base / "home/.local/share/codex-wechat",
            )
        with patch.object(installer, "IS_WINDOWS", True), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(self.base / "local")}):
            self.assertEqual(
                installer.default_target(),
                self.base / "local/codex-wechat/app",
            )

    def test_windows_update_only_refreshes_existing_venv(self):
        python = self.target / ".venv/Scripts/python.exe"
        python.parent.mkdir(parents=True)
        python.write_text("fixture", encoding="utf-8")
        with patch.object(installer, "IS_WINDOWS", True), \
             patch.object(installer.shutil, "which", return_value="powershell.exe"), \
             patch.object(installer, "_run") as run:
            installer.prepare_windows(self.target, update=True)
        self.assertEqual(run.call_args.args[0][0], str(python))

    def test_linux_rejects_nonstandard_install_directory(self):
        with patch.object(installer, "IS_WINDOWS", False), \
             patch.object(installer, "default_target", return_value=self.base / "canonical"):
            with self.assertRaisesRegex(ValueError, "必须是"):
                installer.main(["install", "--dir", str(self.target)])

    def test_linux_wrapper_routes_install_and_update_to_installer(self):
        text = (installer.SOURCE_ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn("install|update", text)
        self.assertIn('installer.py" "$@"', text)


if __name__ == "__main__":
    unittest.main()
