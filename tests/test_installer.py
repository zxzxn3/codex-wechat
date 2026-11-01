"""Offline contracts for immutable release installation without rollback."""
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
        self.enterContext(patch.object(installer, "report_dependencies"))
        self.enterContext(patch.object(installer, "prepare_bootstrap_runtime"))
        self.enterContext(patch.object(installer, "preflight_command_link"))
        self.enterContext(patch.object(installer, "install_command_link"))
        self.enterContext(patch.object(installer, "configure_windows"))
        self.enterContext(patch.object(installer, "next_steps"))

    def make_project(self, root, *, version="one"):
        root.mkdir(parents=True, exist_ok=True)
        required = installer.REQUIRED_FILES | set(installer.STABLE_LINUX_FILES)
        required.update(installer.STABLE_WINDOWS_FILES)
        for relative in required:
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
            ".bootstrap-venv/bin/python": "private",
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

    def current(self):
        return installer.read_pointer(self.target, installer.CURRENT_FILE)

    def previous(self):
        return installer.read_pointer(self.target, installer.PREVIOUS_FILE)

    def test_payload_excludes_private_runtime_content(self):
        self.add_private_data(self.source)
        files = installer.project_files(self.source)
        self.assertIn(Path("extra.py"), files)
        self.assertIn(Path("docs/guide.md"), files)
        for relative in (
            Path("config.json"),
            Path(".state/account.json"),
            Path(".venv/bin/python"),
            Path(".bootstrap-venv/bin/python"),
            Path(".update-backups/old/file.py"),
            Path("workspace/notes.txt"),
        ):
            self.assertNotIn(relative, files)

    def test_payload_refuses_file_source_and_directory_symlinks(self):
        link = self.source / "link.py"
        link.symlink_to(self.source / "extra.py")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.project_files(self.source)
        link.unlink()
        source_link = self.base / "source-link"
        source_link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "源码目录"):
            installer.project_files(source_link)
        directory_link = self.source / "linked-docs"
        directory_link.symlink_to(self.source / "docs", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.project_files(self.source)

    def test_install_creates_immutable_release_and_pointer(self):
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        identifier = self.current()
        self.assertIsNotNone(identifier)
        release = self.target / installer.RELEASES_DIR / identifier
        self.assertTrue((release / "extra.py").is_file())
        self.assertTrue((release / "docs/guide.md").is_file())
        self.assertTrue((release / "codex-wechat").stat().st_mode & stat.S_IXUSR)
        self.assertTrue(installer.release_matches(self.target, identifier))
        self.assertTrue((self.target / "codex-wechat").is_file())
        self.assertTrue((self.target / "installer.py").is_file())
        self.assertFalse((self.target / "config.json").exists())
        self.assertFalse((self.target / ".state").exists())

    def test_cancel_and_dry_run_do_not_create_version_state(self):
        with patch("builtins.input", return_value="n"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        self.assertFalse((self.target / installer.CURRENT_FILE).exists())
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target, dry_run=True), 0)
        self.assertFalse((self.target / installer.RELEASES_DIR).exists())

    def test_nonempty_unrelated_target_is_rejected_before_prompt(self):
        self.target.mkdir()
        marker = self.target / "unrelated"
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.install(self.source, self.target)
            prompt.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")

    def test_update_rejects_unrelated_directory_with_forged_current_pointer(self):
        self.target.mkdir()
        (self.target / installer.CURRENT_FILE).write_text("deadbeef\n", encoding="utf-8")
        marker = self.target / "unrelated"
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "不是完整的现有"):
                installer.update(self.source, self.target)
            prompt.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(
            (self.target / installer.CURRENT_FILE).read_text(),
            "deadbeef\n",
        )
        self.assertFalse((self.target / installer.RELEASES_DIR).exists())

    def test_dry_run_accepts_conflicting_target_without_reading_or_writing_it(self):
        self.target.write_text("existing file, not an installation directory", encoding="utf-8")
        output = io.StringIO()
        with patch("builtins.input", return_value="y"), \
             patch.object(installer, "read_pointer", side_effect=AssertionError("read target")), \
             patch.object(installer, "preflight_command_link", side_effect=AssertionError("check link")), \
             patch.object(installer, "prepare_runtime") as prepare, \
             contextlib.redirect_stdout(output):
            self.assertEqual(installer.install(self.source, self.target, dry_run=True), 0)
            self.assertEqual(installer.update(self.source, self.target, dry_run=True), 0)
        prepare.assert_not_called()
        self.assertIn("因 --dry-run", output.getvalue())
        self.assertEqual(self.target.read_text(), "existing file, not an installation directory")

    def test_dry_run_main_accepts_nonstandard_target_without_versions(self):
        self.target.write_text("keep", encoding="utf-8")
        with patch("builtins.input", return_value="y"), \
             patch.object(installer, "IS_WINDOWS", False), \
             patch.object(installer, "SOURCE_ROOT", self.source), \
             patch.object(installer, "read_pointer", side_effect=AssertionError("read target")):
            for operation in ("install", "update"):
                self.assertEqual(installer.main([
                    operation, "--dir", str(self.target), "--dry-run",
                ]), 0)
        self.assertEqual(self.target.read_text(), "keep")

    def test_target_root_symlink_is_rejected(self):
        real_target = self.base / "real-target"
        real_target.mkdir()
        self.target.symlink_to(real_target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.install(self.source, self.target)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.update(self.source, self.target)

    def test_legacy_in_place_install_is_adopted_without_moving_data(self):
        self.source.rename(self.target)
        self.make_project(self.source, version="new")
        marker = self.target / "workspace/keep.txt"
        marker.parent.mkdir()
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.update(self.source, self.target), 0)
        self.assertIsNotNone(self.current())
        self.assertIsNone(self.previous())
        self.assertEqual(marker.read_text(), "keep")
        self.assertTrue((self.target / "codex_wechat.py").is_file())

    def test_update_switches_as_a_whole_and_preserves_private_data(self):
        (self.source / "removed-in-v2.py").write_text("OLD = True\n", encoding="utf-8")
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        first = self.current()
        self.add_private_data(self.target)
        unknown = self.target / "local-note.txt"
        unknown.write_text("keep", encoding="utf-8")
        (self.source / "removed-in-v2.py").unlink()
        self.make_project(self.source, version="two")
        (self.source / "new.py").write_text("NEW = True\n", encoding="utf-8")
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.update(self.source, self.target), 0)
        second = self.current()
        self.assertNotEqual(first, second)
        self.assertIsNone(self.previous())
        release = self.target / installer.RELEASES_DIR / second
        self.assertTrue((release / "new.py").is_file())
        self.assertFalse((release / "removed-in-v2.py").exists())
        self.assertEqual(unknown.read_text(), "keep")
        self.assertEqual((self.target / "config.json").read_text(), "private")
        self.assertEqual((self.target / ".state/account.json").read_text(), "private")

    def test_noop_update_does_not_prompt_for_confirmation(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        with patch.object(installer, "introduction") as intro:
            self.assertEqual(installer.update(self.source, self.target), 0)
            intro.assert_not_called()

    def test_failed_runtime_build_keeps_previous_pointer(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        first = self.current()
        self.make_project(self.source, version="two")
        with patch("builtins.input", return_value="y"), \
             patch.object(installer, "prepare_runtime",
                          side_effect=ValueError("build failed")):
            with self.assertRaisesRegex(ValueError, "build failed"):
                installer.update(self.source, self.target)
        self.assertEqual(self.current(), first)

    def test_source_change_after_confirmation_is_rejected(self):
        def change_source(_):
            (self.source / "extra.py").write_text("CHANGED = True\n", encoding="utf-8")
            return "y"

        with patch("builtins.input", side_effect=change_source):
            with self.assertRaisesRegex(ValueError, "源码发生变化"):
                installer.install(self.source, self.target)
        self.assertFalse((self.target / installer.CURRENT_FILE).exists())

    def test_rollback_is_not_an_installer_operation(self):
        with self.assertRaises(SystemExit):
            installer.parser().parse_args(["rollback"])
        self.assertFalse(hasattr(installer, "rollback_activation"))

    def test_integration_failure_does_not_roll_back_committed_version(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        first = self.current()
        self.make_project(self.source, version="two")
        with patch("builtins.input", return_value="y"), \
             patch.object(installer, "configure_windows", side_effect=OSError("integration failed")):
            with self.assertRaisesRegex(OSError, "integration failed"):
                installer.update(self.source, self.target)
        self.assertNotEqual(self.current(), first)
        self.assertTrue(installer.release_matches(self.target, self.current()))
        self.assertFalse((self.target / installer.PREVIOUS_FILE).exists())

    def test_same_version_update_repairs_missing_stable_entry(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        current = self.current()
        (self.target / "codex-wechat").unlink()
        with patch.object(installer, "introduction", return_value=True), patch("builtins.input", return_value="y"):
            self.assertEqual(installer.update(self.source, self.target), 0)
        self.assertEqual(self.current(), current)
        self.assertTrue((self.target / "codex-wechat").is_file())

    def test_update_rejects_same_versioned_root_as_source(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        with self.assertRaisesRegex(ValueError, "另一份源码"):
            installer.update(self.target, self.target)

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
             patch.object(installer.shutil, "get_terminal_size",
                          return_value=os.terminal_size((80, 24))), \
             patch.dict(os.environ, {"TERM": "xterm"}):
            installer.logo_banner(self.source)
        self.assertIn("future art", output.getvalue())

    def test_optional_logo_does_not_follow_symlinks(self):
        secret = self.base / "secret.txt"
        secret.write_text("do not print\n", encoding="utf-8")
        logo = self.source / installer.LOGO_PATH
        logo.parent.mkdir(parents=True, exist_ok=True)
        logo.symlink_to(secret)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(installer.sys.stdout, "isatty", return_value=True), \
             patch.object(installer.shutil, "get_terminal_size",
                          return_value=os.terminal_size((80, 24))), \
             patch.dict(os.environ, {"TERM": "xterm"}):
            installer.logo_banner(self.source)
        self.assertEqual(output.getvalue(), "")

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

    def test_linux_rejects_nonstandard_install_directory(self):
        with patch.object(installer, "IS_WINDOWS", False), \
             patch.object(installer, "default_target",
                          return_value=self.base / "canonical"):
            with self.assertRaisesRegex(ValueError, "必须是"):
                installer.main(["install", "--dir", str(self.target), "--non-interactive"])

    def test_linux_wrapper_routes_release_commands_to_installer(self):
        text = (installer.SOURCE_ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn("install|update|menu|components", text)
        self.assertIn('INSTALL_ROOT/installer.py', text)
        self.assertIn('INSTALL_ROOT/releases/$RELEASE_ID', text)
        self.assertIn('[ -L "$CURRENT_FILE" ]', text)


if __name__ == "__main__":
    unittest.main()
