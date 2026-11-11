"""Old release identity is independent of a newer source packaging policy."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import installer


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.source=self.base/"source"
        self.source.mkdir()
        self.target=self.base/"app"
        for name in installer.REQUIRED_FILES:
            p=self.source/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text("fixture",encoding="utf-8")
        self.files=installer.project_files(self.source)
        old=self.source/"experiments/old-example.txt"
        old.parent.mkdir()
        old.write_text("old public file",encoding="utf-8")
        self.files[Path("experiments/old-example.txt")]=(old.read_bytes(),old.stat().st_mode & 0o777)
        self.identifier=installer.release_identifier(self.files)
        self.release=installer.release_path(self.target,self.identifier)
        installer._copy_release(self.release,self.files)
    def test_new_exclusions_do_not_reject_an_old_valid_release(self):
        with patch.object(installer,"PRIVATE_NAMES",installer.PRIVATE_NAMES|{"experiments"}):
            self.assertTrue(installer.release_matches(self.target,self.identifier))
    def test_new_source_requirements_do_not_redefine_old_release(self):
        with patch.object(installer,"REQUIRED_FILES",installer.REQUIRED_FILES|{Path("new-module.py")}):
            self.assertTrue(installer.release_matches(self.target,self.identifier))
    def test_excluded_but_declared_old_file_is_still_checked(self):
        (self.release/"experiments/old-example.txt").write_text("tampered",encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target,self.identifier))
    def test_undeclared_program_file_is_rejected(self):
        (self.release/"injected.py").write_text("fixture",encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target,self.identifier))
    def test_manifest_cannot_escape_release_root(self):
        path=self.release/installer.MANIFEST_FILE
        manifest=json.loads(path.read_text(encoding="utf-8"))
        manifest["files"].insert(0,"../outside")
        path.write_text(json.dumps(manifest),encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target,self.identifier))

    def test_windows_release_identity_ignores_posix_execute_bits(self):
        source = {Path("codex-wechat"): (b"fixture", 0o755)}
        copied = {Path("codex-wechat"): (b"fixture", 0o666)}
        with patch.object(installer, "IS_WINDOWS", True):
            self.assertEqual(
                installer.release_identifier(source),
                installer.release_identifier(copied),
            )
        with patch.object(installer, "IS_WINDOWS", False):
            self.assertNotEqual(
                installer.release_identifier(source),
                installer.release_identifier(copied),
            )


class GitSourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source"
        self.source.mkdir()
        self.git("init", "--quiet")

    def git(self, *arguments):
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.source,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_git_source_uses_head_and_excludes_untracked_files(self):
        tracked = self.source / "tracked.py"
        tracked.write_text("TRACKED = True\n", encoding="utf-8")
        self.git("add", "tracked.py")
        self.git(
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "-m", "fixture",
        )
        untracked = self.source / "tools/not-in-release.py"
        untracked.parent.mkdir()
        untracked.write_text("UNTRACKED = True\n", encoding="utf-8")
        files = installer.project_files(self.source, require_complete=False)
        self.assertIn(Path("tracked.py"), files)
        self.assertNotIn(Path("tools/not-in-release.py"), files)

    def test_git_source_rejects_uncommitted_tracked_changes(self):
        tracked = self.source / "tracked.py"
        tracked.write_text("TRACKED = True\n", encoding="utf-8")
        self.git("add", "tracked.py")
        self.git(
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "-m", "fixture",
        )
        tracked.write_text("TRACKED = False\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未提交"):
            installer.project_files(self.source, require_complete=False)
