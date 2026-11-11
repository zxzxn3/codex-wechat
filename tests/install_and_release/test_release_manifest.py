"""Wheel release identity and source-boundary contracts."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import installer


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.target = self.base / "app"
        self.wheel = self.base / "codex_wechat-1.1.0-py3-none-any.whl"
        self.wheel.write_bytes(b"fixture wheel")
        self.identifier = installer.wheel_identifier(self.wheel)
        self.release = installer.release_path(self.target, self.identifier)
        self.release.mkdir(parents=True)
        installer._write_release_manifest(
            self.release, self.identifier, self.wheel)

    def test_wheel_identity_is_the_full_wheel_digest_prefix(self):
        expected = hashlib.sha256(self.wheel.read_bytes()).hexdigest()[:16]
        self.assertEqual(self.identifier, expected)
        self.assertTrue(installer.release_matches(self.target, self.identifier))

    def test_manifest_records_the_installed_wheel(self):
        manifest = json.loads(
            (self.release / installer.MANIFEST_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["wheel"], self.wheel.name)
        self.assertEqual(manifest["id"], self.identifier)
        self.assertEqual(manifest["schema"], installer.RELEASE_FORMAT)

    def test_release_requires_a_nonempty_wheel_name(self):
        manifest = json.loads(
            (self.release / installer.MANIFEST_FILE).read_text(encoding="utf-8")
        )
        manifest["wheel"] = ""
        (self.release / installer.MANIFEST_FILE).write_text(
            json.dumps(manifest), encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target, self.identifier))


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


if __name__ == "__main__":
    unittest.main()
