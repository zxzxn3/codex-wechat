"""Offline contracts for immutable release installation without rollback."""
from tests.support.helpers import requires_symlinks
import contextlib
import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import installer
from codex_wechat.install_layout import INSTALLATION_FILE, InstalledLayout


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.target = self.base / "target"
        self.make_project(self.source)
        self.enterContext(patch.object(installer, "introduction", return_value=True))
        self.enterContext(patch.object(
            installer, "ensure_uv", side_effect=lambda target: target / installer.UV_DIR / installer.UV_VERSION / "uv"))
        self.enterContext(patch.object(
            installer, "ensure_managed_python", side_effect=lambda target: Path("/managed/python")))
        self.enterContext(patch.object(
            installer, "_managed_python", side_effect=lambda target: Path("/managed/python")))
        self.enterContext(patch.object(installer, "bootstrap_runtime_ready", return_value=True))
        self.enterContext(patch.object(installer, "release_runtime_ready", return_value=True))
        self.enterContext(patch.object(
            installer, "prepare_runtime", side_effect=self.prepare_runtime))
        self.enterContext(patch.object(
            installer,
            "build_wheel",
            side_effect=self.build_wheel,
        ))
        self.enterContext(patch.object(installer, "report_dependencies"))
        def bootstrap(target, wheel):
            if installer.IS_WINDOWS:
                executable = target / ".bootstrap-venv/Scripts/python.exe"
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_bytes(b"fake interpreter")
        self.enterContext(patch.object(installer, "prepare_bootstrap_runtime", side_effect=bootstrap))
        self.enterContext(patch.object(installer, "preflight_command_link"))
        self.enterContext(patch.object(installer, "install_command_link"))
        self.enterContext(patch.object(installer, "configure_platform_service"))
        self.enterContext(patch.object(installer, "next_steps"))
        # The compatibility gate reads the active install's config; keep the
        # developer's own data root out of these fixtures.
        self.enterContext(patch.dict(
            os.environ, {
                "CODEX_WECHAT_DATA_ROOT": str(self.base / "data"),
                "XDG_CONFIG_HOME": str(self.base / "config"),
                "XDG_STATE_HOME": str(self.base / "state"),
            }))

    def build_wheel(self, target, source, output):
        wheel = output / "codex_wechat-1.1.0-py3-none-any.whl"
        wheel.write_bytes((source / "extra.py").read_bytes())
        return wheel

    def prepare_runtime(self, root, wheel):
        del wheel
        python = installer._venv_python(root)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"fixture")

    def fixture_wheel(self, content=b"wheel"):
        wheel = self.base / "codex_wechat-1.1.0-py3-none-any.whl"
        wheel.write_bytes(content)
        return wheel

    def make_project(self, root, *, version="one"):
        root.mkdir(parents=True, exist_ok=True)
        required = installer.REQUIRED_FILES | set(installer.STABLE_LINUX_FILES)
        required.update(installer.STABLE_WINDOWS_FILES)
        for relative in required:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{relative}: {version}\n", encoding="utf-8")
        shutil.copy2(
            installer.SOURCE_ROOT / "codex_wechat/install_layout.py",
            root / "codex_wechat/install_layout.py",
        )
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
            ".bootstrap/uv/0.13.7/uv": "private",
            ".bootstrap-venv/bin/python": "private",
            ".update-backups/old/file.py": "private",
            "workspace/notes.txt": "private",
            "config.json": "{}",
            "config.json.tmp": '{"env":{"PROXY_TOKEN":"secret"}}',
            "__pycache__/demo.pyc": "private",
            "dist/codex-wechat": "private",
        }
        for relative, content in private.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return private

    def current(self):
        layout = InstalledLayout.load(
            self.target, platform="windows" if installer.IS_WINDOWS else "linux")
        return layout.release_id if layout is not None else None

    def previous(self):
        try:
            return (self.target / installer.PREVIOUS_FILE).read_text(
                encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def test_payload_excludes_private_runtime_content(self):
        self.add_private_data(self.source)
        files = installer.project_files(self.source)
        self.assertIn(Path("extra.py"), files)
        self.assertIn(Path("docs/guide.md"), files)
        for relative in (
            Path("config.json"),
            Path("config.json.tmp"),
            Path(".state/account.json"),
            Path(".venv/bin/python"),
            Path(".bootstrap/uv/0.13.7/uv"),
            Path(".bootstrap-venv/bin/python"),
            Path(".update-backups/old/file.py"),
            Path("workspace/notes.txt"),
        ):
            self.assertNotIn(relative, files)

    def test_payload_keeps_nested_files_named_like_runtime_config(self):
        nested = (Path("docs/config.json"), Path("docs/config.json.tmp"))
        for relative in nested:
            path = self.source / relative
            path.write_text("public fixture\n", encoding="utf-8")

        files = installer.project_files(self.source)

        for relative in nested:
            self.assertIn(relative, files)

    @requires_symlinks
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
        self.assertTrue((release / installer.MANIFEST_FILE).is_file())
        self.assertFalse((release / "extra.py").exists())
        if not installer.IS_WINDOWS:
            self.assertTrue((self.target / "codex-wechat").stat().st_mode & stat.S_IXUSR)
        self.assertTrue(installer.release_matches(self.target, identifier))
        self.assertTrue((self.target / installer._stable_files(self.source)[0]).is_file())
        self.assertTrue((self.target / "installer.py").is_file())
        self.assertFalse((self.target / "codex_wechat/app.py").exists())
        self.assertFalse((self.target / "config.json").exists())
        self.assertFalse((self.target / ".state").exists())

    def test_stable_entries_are_copied_from_the_source_bundle(self):
        with patch("builtins.input", return_value="y"), \
                patch.object(
                    installer,
                    "install_bootstrap",
                    wraps=installer.install_bootstrap,
                ) as install_bootstrap:
            self.assertEqual(installer.install(self.source, self.target), 0)
        source = install_bootstrap.call_args.args[0]
        self.assertEqual(source, self.source)

    def test_windows_bootstrap_creates_nested_stable_directories(self):
        self.target.mkdir()
        with patch.object(installer, "IS_WINDOWS", True):
            installer.install_bootstrap(self.source, self.target)
        self.assertTrue((self.target / "windows/task-service.ps1").is_file())

    def test_cancel_and_dry_run_do_not_create_version_state(self):
        with patch("builtins.input", return_value="n"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        self.assertFalse((self.target / INSTALLATION_FILE).exists())
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target, dry_run=True), 0)
        self.assertFalse((self.target / installer.RELEASES_DIR).exists())

    def test_install_and_update_show_the_actual_operation(self):
        with contextlib.redirect_stdout(io.StringIO()) as install_output, \
                patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        self.assertIn(":: 安装", install_output.getvalue())
        self.assertNotIn("安装 / 更新", install_output.getvalue())
        self.make_project(self.source, version="two")
        with contextlib.redirect_stdout(io.StringIO()) as update_output, \
                patch("builtins.input", return_value="y"):
            installer.update(self.source, self.target)
        self.assertIn(":: 更新", update_output.getvalue())
        self.assertNotIn("安装 / 更新", update_output.getvalue())

    def test_nonempty_unrelated_target_is_rejected_before_prompt(self):
        self.target.mkdir()
        marker = self.target / "unrelated"
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.install(self.source, self.target)
            prompt.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")

    def test_update_rejects_the_legacy_pointer_layout(self):
        self.target.mkdir()
        (self.target / "current").write_text("deadbeef\n", encoding="utf-8")
        marker = self.target / "unrelated"
        marker.write_text("keep", encoding="utf-8")
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "旧安装布局"):
                installer.update(self.source, self.target)
            prompt.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(
            (self.target / "current").read_text(),
            "deadbeef\n",
        )
        self.assertFalse((self.target / installer.RELEASES_DIR).exists())

    def test_dry_run_accepts_conflicting_target_without_reading_or_writing_it(self):
        self.target.write_text("existing file, not an installation directory", encoding="utf-8")
        output = io.StringIO()
        with patch("builtins.input", return_value="y"), \
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
             patch.object(installer, "SOURCE_ROOT", self.source):
            for operation in ("install", "update"):
                self.assertEqual(installer.main([
                    operation, "--dir", str(self.target), "--dry-run",
                ]), 0)
        self.assertEqual(self.target.read_text(), "keep")

    def test_main_refuses_an_elevated_terminal(self):
        with patch.object(installer, "_elevated", return_value=True), \
             patch.object(installer, "install", side_effect=AssertionError("installed")):
            with self.assertRaisesRegex(ValueError, "普通用户"):
                installer.main(["install", "--dir", str(self.target), "--non-interactive"])

    def test_root_override_allows_disposable_containers(self):
        with patch.object(installer, "_elevated", return_value=True), \
                patch.dict(os.environ, {"CODEX_WECHAT_ALLOW_ROOT": "1"}), \
                patch.object(installer, "default_target", return_value=self.target), \
                patch.object(installer, "install", return_value=0) as install, \
                patch.object(installer, "logo_banner"), \
                patch.object(installer, "declaration"):
            self.assertEqual(
                installer.main(["install", "--dir", str(self.target), "--non-interactive"]),
                0,
            )
        install.assert_called_once()

    @requires_symlinks
    def test_target_root_symlink_is_rejected(self):
        real_target = self.base / "real-target"
        real_target.mkdir()
        self.target.symlink_to(real_target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.install(self.source, self.target)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.update(self.source, self.target)

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
        self.assertTrue((release / installer.MANIFEST_FILE).is_file())
        self.assertFalse((release / "new.py").exists())
        self.assertFalse((release / "removed-in-v2.py").exists())
        self.assertEqual(unknown.read_text(), "keep")
        self.assertEqual((self.target / "config.json").read_text(), "{}")
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

    def test_changed_data_root_is_rejected_before_the_update_changes_state(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        first = self.current()
        old_root = (self.base / "data").resolve()
        new_root = self.base / "new-data"
        self.make_project(self.source, version="two")
        with patch.dict(os.environ, {"CODEX_WECHAT_DATA_ROOT": str(new_root)}):
            with self.assertRaisesRegex(ValueError, "固定"):
                installer.update(self.source, self.target)
        self.assertEqual(self.current(), first)
        self.assertEqual(
            InstalledLayout.load(
                self.target,
                platform="windows" if installer.IS_WINDOWS else "linux",
            ).data_root,
            old_root,
        )

    def test_failed_first_install_removes_the_disposable_target(self):
        with patch("builtins.input", return_value="y"), \
                patch.object(installer, "install_bootstrap",
                             side_effect=OSError("entry failed")):
            with self.assertRaisesRegex(OSError, "entry failed"):
                installer.install(self.source, self.target)
        self.assertFalse(self.target.exists())
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        self.assertIsNotNone(self.current())

    def test_failed_first_activation_without_manifest_removes_the_target(self):
        contract = installer.install_layout(self.source)
        with patch("builtins.input", return_value="y"), \
                patch.object(contract.InstalledLayout, "publish",
                             side_effect=OSError("manifest failed")):
            with self.assertRaisesRegex(OSError, "manifest failed"):
                installer.install(self.source, self.target)
        self.assertFalse(self.target.exists())
        with patch("builtins.input", return_value="y"):
            self.assertEqual(installer.install(self.source, self.target), 0)
        self.assertIsNotNone(self.current())
        self.assertTrue((self.target / INSTALLATION_FILE).is_file())

    def test_source_change_after_confirmation_is_rejected(self):
        def change_source(_):
            (self.source / "extra.py").write_text("CHANGED = True\n", encoding="utf-8")
            return "y"

        with patch("builtins.input", side_effect=change_source):
            with self.assertRaisesRegex(ValueError, "源码发生变化"):
                installer.install(self.source, self.target)
        self.assertFalse((self.target / INSTALLATION_FILE).exists())

    def test_prune_keeps_the_newest_and_the_release_that_may_still_run(self):
        releases = self.target / installer.RELEASES_DIR
        for name in ("aaa111", "bbb222", "ccc333"):
            (releases / name).mkdir(parents=True)
        (releases / ".staging-1234").mkdir()
        (releases / ".trash-old").mkdir()
        removed = installer.prune_releases(self.target, keep=("ccc333", "bbb222"))
        self.assertEqual(removed, ["aaa111"])
        self.assertEqual(sorted(entry.name for entry in releases.iterdir()),
                         [".staging-1234", "bbb222", "ccc333"])

    def test_prune_leaves_a_release_it_cannot_move(self):
        releases = self.target / installer.RELEASES_DIR
        (releases / "aaa111").mkdir(parents=True)
        with patch("installer.os.replace", side_effect=OSError("locked")), \
                patch("installer.PRUNE_RETRY_SECONDS", 0):
            self.assertEqual(installer.prune_releases(self.target, keep=()), [])
        self.assertTrue((releases / "aaa111").is_dir())

    def test_prune_retries_a_transiently_held_release(self):
        releases = self.target / installer.RELEASES_DIR
        (releases / "aaa111").mkdir(parents=True)
        real_replace = os.replace
        attempts = []

        def flaky_replace(source, destination):
            attempts.append(source.name)
            if len(attempts) == 1:
                raise OSError(5, "access denied")
            return real_replace(source, destination)

        with patch("installer.os.replace", side_effect=flaky_replace), \
                patch("installer.PRUNE_RETRY_SECONDS", 0):
            self.assertEqual(installer.prune_releases(self.target, keep=()), ["aaa111"])
        self.assertEqual(len(attempts), 2)
        self.assertFalse((releases / "aaa111").exists())

    def test_transiently_held_staging_tree_is_retried(self):
        wheel = self.fixture_wheel(b"transient")
        identifier = installer.wheel_identifier(wheel)
        final = self.target / installer.RELEASES_DIR / identifier
        real_replace = os.replace
        attempts = []

        def flaky_replace(source, destination):
            if Path(source).name.startswith(".staging-"):
                attempts.append(Path(source))
                if len(attempts) == 1:
                    raise PermissionError(5, "access denied")
            return real_replace(source, destination)

        with patch("installer.os.replace", side_effect=flaky_replace), \
                patch.object(installer, "STAGING_REPLACE_RETRY_SECONDS", 0):
            release, created = installer.install_release(
                self.target, identifier, wheel)
        self.assertTrue(created)
        self.assertEqual(release, final)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(installer.release_matches(self.target, identifier))

    def test_staging_rename_that_stays_locked_is_reported_and_cleaned(self):
        wheel = self.fixture_wheel(b"locked")
        identifier = installer.wheel_identifier(wheel)
        final = self.target / installer.RELEASES_DIR / identifier
        real_replace = os.replace

        def locked_replace(source, destination):
            if Path(source).name.startswith(".staging-"):
                raise PermissionError(5, "access denied")
            return real_replace(source, destination)

        with patch("installer.os.replace", side_effect=locked_replace), \
                patch.object(installer, "STAGING_REPLACE_RETRY_SECONDS", 0), \
                patch.object(installer, "STAGING_REPLACE_ATTEMPTS", 2):
            with self.assertRaisesRegex(ValueError, "暂存目录仍被占用"):
                installer.install_release(self.target, identifier, wheel)
        self.assertFalse(final.exists())
        self.assertEqual(list((self.target / installer.RELEASES_DIR).glob(
            ".staging-*")), [])

    def test_release_published_by_another_installer_is_reused(self):
        wheel = self.fixture_wheel(b"competing")
        identifier = installer.wheel_identifier(wheel)
        final = self.target / installer.RELEASES_DIR / identifier
        real_replace = os.replace

        def competing_replace(source, destination):
            if Path(source).name.startswith(".staging-"):
                shutil.copytree(source, destination)
                raise PermissionError(5, "access denied")
            return real_replace(source, destination)

        with patch("installer.os.replace", side_effect=competing_replace), \
                patch.object(installer, "STAGING_REPLACE_RETRY_SECONDS", 0):
            release, created = installer.install_release(
                self.target, identifier, wheel)
        self.assertFalse(created)
        self.assertEqual(release, final)
        self.assertTrue(installer.release_matches(self.target, identifier))

    def test_rollback_is_not_an_installer_operation(self):
        with self.assertRaises(SystemExit):
            installer.parser().parse_args(["rollback"])
        self.assertFalse(hasattr(installer, "rollback_activation"))

    def test_same_version_update_repairs_missing_stable_entry(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        current = self.current()
        (self.target / installer._stable_files(self.source)[0]).unlink()
        with patch.object(installer, "introduction", return_value=True), patch("builtins.input", return_value="y"):
            self.assertEqual(installer.update(self.source, self.target), 0)
        self.assertEqual(self.current(), current)
        self.assertTrue((self.target / installer._stable_files(self.source)[0]).is_file())

    def test_same_version_update_repairs_the_command_entry(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        with patch("builtins.input", return_value="y"), \
                patch.object(installer, "install_command_link") as link:
            self.assertEqual(installer.update(self.source, self.target), 0)
        link.assert_called_once_with(self.target)

    def test_same_version_update_does_not_touch_the_platform_service(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        current = self.current()
        with patch("builtins.input", return_value="y"), \
                patch.object(installer, "configure_platform_service") as configure:
            self.assertEqual(installer.update(self.source, self.target), 0)
        configure.assert_not_called()
        self.assertEqual(self.current(), current)

    def test_same_version_update_rejects_a_missing_release_runtime(self):
        with patch("builtins.input", return_value="y"):
            installer.install(self.source, self.target)
        current = self.current()
        with patch.object(installer, "release_runtime_ready", return_value=False):
            with self.assertRaisesRegex(ValueError, "Python 环境不完整"):
                installer.update(self.source, self.target)
        self.assertEqual(self.current(), current)

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

    @requires_symlinks
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
        self.assertIn("codex_wechat.launcher", text)
        self.assertNotIn("CURRENT_FILE", text)


class BootstrapRuntimeTests(unittest.TestCase):
    def test_stable_runtime_repair_requires_bootstrap_or_first_install(self):
        with patch.object(installer, "ensure_uv"), \
                patch.object(installer, "ensure_managed_python"), \
                patch.object(installer, "bootstrap_runtime_ready", return_value=False), \
                patch.object(installer, "prepare_bootstrap_runtime") as prepare:
            wheel = Path("/codex_wechat-1.1.0-py3-none-any.whl")
            with self.assertRaisesRegex(ValueError, "重新运行 bootstrap"):
                installer._ensure_stable_runtime(
                    Path("/target"), wheel, create_runtime=False)
            prepare.assert_not_called()

            installer._ensure_stable_runtime(
                Path("/target"), wheel, create_runtime=True)
            prepare.assert_called_once_with(Path("/target"), wheel)

    def test_stable_runtime_installs_the_wheel_with_uv(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            python = target / ".bootstrap-venv/Scripts/python.exe"
            wheel = target / "codex_wechat-1.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            uv_calls = []

            def run_uv(current, arguments, *, cwd):
                uv_calls.append((current, arguments, cwd))
                if arguments[0] == "venv":
                    python.parent.mkdir(parents=True)
                    python.write_bytes(b"fixture")

            with patch.object(installer, "IS_WINDOWS", True), \
                    patch.object(installer, "_run_uv", side_effect=run_uv), \
                    patch.object(installer, "_run"), \
                    patch.object(installer, "bootstrap_runtime_ready", return_value=True):
                installer.prepare_bootstrap_runtime(target, wheel)
            self.assertEqual(uv_calls[0][1][:4], [
                "venv", "--no-project", "--managed-python", "--python",
            ])
            self.assertEqual(uv_calls[1][1][-1], str(wheel))


class RuntimeDiscoveryTests(unittest.TestCase):
    """The dry-run report must not claim a missing runtime it never looked for."""

    def test_local_source_discovery_is_used_in_every_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            helper = source / "codex_wechat/native_codex.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "def discover_codex(env=None, *, windows=None):\n"
                "    return '/found/by/source'\n", encoding="utf-8")
            self.assertEqual(installer._discover_runtime(source), "/found/by/source")

    def test_discovery_does_not_cache_bytecode_in_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            helper = source / "codex_wechat/native_codex.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "def discover_codex(env=None, *, windows=None):\n"
                "    return '/found/by/source'\n", encoding="utf-8")
            with patch.object(installer.sys, "dont_write_bytecode", False):
                installer._discover_runtime(source)
            self.assertFalse((source / "codex_wechat/__pycache__").exists())

    def test_missing_or_broken_helper_falls_back_to_path(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with patch.object(installer.shutil, "which", return_value="/from/path"):
                self.assertEqual(installer._discover_runtime(source), "/from/path")
                helper = source / "codex_wechat/native_codex.py"
                helper.parent.mkdir(parents=True)
                helper.write_text(
                    "raise ValueError('broken helper')\n", encoding="utf-8")
                self.assertEqual(installer._discover_runtime(source), "/from/path")


class InstallerCommandTests(unittest.TestCase):
    def test_hung_install_command_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(installer.subprocess, "run",
                              side_effect=installer.subprocess.TimeoutExpired("pip", 900)):
                with self.assertRaisesRegex(ValueError, "分钟未完成"):
                    installer._run(["pip"], cwd=Path(directory))

    @unittest.skipUnless(os.name == "nt", "Windows ACL semantics")
    def test_staging_directory_inherits_access(self):
        # mkdtemp's 0700 becomes an owner-only DACL; os.replace then moves it
        # onto the installed entries and the service account cannot read them.
        with tempfile.TemporaryDirectory() as directory:
            staging = installer._staging_directory(Path(directory), ".staging-")
            result = installer.subprocess.run(["icacls", str(staging)],
                                              capture_output=True, text=True)
            self.assertIn("(I)", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows ACL semantics")
    def test_private_directory_names_the_account(self):
        try:
            import win32api
            import win32security
        except ImportError:
            self.skipTest("pywin32 未安装")
        from codex_wechat import platform_support
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state"
            platform_support.make_private(target)
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
            account = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            descriptor = win32security.GetNamedSecurityInfo(
                str(target), win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION)
            dacl = descriptor.GetSecurityDescriptorDacl()
            owners = {win32security.ConvertSidToStringSid(dacl.GetAce(index)[2])
                      for index in range(dacl.GetAceCount())}
            self.assertIn(win32security.ConvertSidToStringSid(account), owners)
            (target / "probe").write_text("x", encoding="utf-8")

    def test_unreadable_install_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "install"
            target.mkdir()
            files = installer._stable_files(installer.SOURCE_ROOT)
            contract = installer.install_layout(installer.SOURCE_ROOT)
            data = root / "data"
            if installer.IS_WINDOWS:
                config = state = data
            else:
                config = root / "config"
                state = root / "state"
            layout = contract.InstalledLayout.create(
                target,
                "a" * 16,
                platform="windows" if installer.IS_WINDOWS else "linux",
                data_root=data,
                config_root=config,
                state_root=state,
            )
            for name in (*files, contract.INSTALLATION_FILE):
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
            (target / "releases" / "abc").mkdir(parents=True)
            installer.check_user_access(target, "abc", installer.SOURCE_ROOT, layout)
            with patch.object(installer, "_readable",
                              side_effect=lambda path: path.name != contract.INSTALLATION_FILE):
                with self.assertRaisesRegex(ValueError, "chmod|icacls") as caught:
                    installer.check_user_access(
                        target, "abc", installer.SOURCE_ROOT, layout)
            self.assertIn(contract.INSTALLATION_FILE, str(caught.exception))


class LinuxServiceRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.xdg = Path(self.temporary.name) / "config"
        self.enterContext(patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.xdg)}, clear=False))

    def test_registration_links_without_enabling_or_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "install"
            target.mkdir()
            unit = target / "codex-wechat.service"
            unit.write_text("[Unit]\n", encoding="utf-8")
            with patch.object(installer, "IS_WINDOWS", False), \
                    patch.object(installer.shutil, "which", return_value="/usr/bin/systemctl"), \
                    patch.object(installer, "_linux_service_state", return_value="missing"), \
                    patch.object(installer, "_run") as run:
                self.assertTrue(installer.configure_linux_service(target))
            self.assertEqual(
                run.call_args_list[0].args[0],
                ["/usr/bin/systemctl", "--user", "link", str(unit.resolve())])
            self.assertEqual(
                run.call_args_list[1].args[0],
                ["/usr/bin/systemctl", "--user", "daemon-reload"])
            self.assertFalse(any(
                "start" in part or "enable" in part
                for call in run.call_args_list
                for part in call.args[0]
            ))

    def test_registration_refuses_a_foreign_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "codex-wechat.service").write_text("[Unit]\n", encoding="utf-8")
            with patch.object(installer, "IS_WINDOWS", False), \
                    patch.object(installer.shutil, "which", return_value="/usr/bin/systemctl"), \
                    patch.object(installer, "_linux_service_state", return_value="foreign"), \
                    patch.object(installer, "_run") as run:
                with self.assertRaisesRegex(ValueError, "不属于本安装目录"):
                    installer.configure_linux_service(target)
            run.assert_not_called()

    def test_unavailable_systemd_is_reported_as_unregistered(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "codex-wechat.service").write_text("[Unit]\n", encoding="utf-8")
            with patch.object(installer, "IS_WINDOWS", False), \
                    patch.object(installer.shutil, "which", return_value="/usr/bin/systemctl"), \
                    patch.object(installer, "_linux_service_state", return_value="unavailable"), \
                    patch.object(installer, "_run") as run:
                self.assertFalse(installer.configure_linux_service(target))
            run.assert_not_called()


class RuntimeRepairTests(unittest.TestCase):
    def test_uv_version_accepts_the_installer_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            uv = Path(directory) / "uv"
            uv.write_text("fixture", encoding="utf-8")
            with patch.object(installer.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=f"uv {installer.UV_VERSION} (abc 2026-11-11)\n")):
                self.assertTrue(installer._uv_version_matches(uv))

    def test_manifest_publication_precedes_service_configuration(self):
        text = (Path(__file__).resolve().parents[2] / "installer.py").read_text(
            encoding="utf-8")
        publication = text.index("planned.publish()")
        configuration = text.index("service_ready = configure_platform_service", publication)
        self.assertLess(publication, configuration)

    def test_uv_environment_stays_inside_the_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            environment = installer._uv_environment(target)
        self.assertEqual(environment["UV_PYTHON_INSTALL_DIR"],
                         str((target / installer.UV_PYTHON_DIR).absolute()))
        self.assertEqual(environment["UV_CACHE_DIR"],
                         str((target / installer.UV_CACHE_DIR).absolute()))
        self.assertEqual(environment["UV_MANAGED_PYTHON"], "1")
        self.assertEqual(environment["UV_PYTHON_INSTALL_BIN"], "0")

    def test_release_runtime_ready_checks_runtime_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = installer._venv_python(root)
            python.parent.mkdir(parents=True)
            python.write_text("fake", encoding="utf-8")
            with patch.object(installer, "_runtime_python_works", return_value=True), \
                    patch.object(installer.subprocess, "run",
                                 return_value=SimpleNamespace(returncode=0)) as run:
                self.assertTrue(installer.release_runtime_ready(root))
            import_command = run.call_args.args[0][2]
            for module in installer.RUNTIME_IMPORTS:
                self.assertIn(module, import_command)

    def test_prepare_runtime_uses_uv_managed_python_and_import_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = installer._venv_python(root)
            calls = []

            def run_uv(target, arguments, *, cwd):
                calls.append(arguments)
                if arguments[0] == "venv":
                    python.parent.mkdir(parents=True, exist_ok=True)
                    python.write_text("managed", encoding="utf-8")

            with patch.object(installer, "_run_uv", side_effect=run_uv), \
                    patch.object(installer, "_run"), \
                    patch.object(installer, "project_files", return_value={}):
                wheel = root / "codex_wechat-1.1.0-py3-none-any.whl"
                wheel.write_bytes(b"wheel")
                installer.prepare_runtime(root, wheel)
            self.assertEqual(calls[0][:5], [
                "venv", "--no-project", "--managed-python", "--python", installer.UV_PYTHON,
            ])
            self.assertEqual(calls[1][:3], [
                "pip", "install", "--python",
            ])
            self.assertEqual(calls[1][-1], str(wheel))


if __name__ == "__main__":
    unittest.main()
