"""One-line entry chooses install/update without contacting a real repository."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import installer
from tests.support.helpers import install_platform, write_installation_layout


class BootstrapEntryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(install_platform("linux"))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "installation"
        self.choice = installer.update_source("https://github.com/example/project")

    def make_source(self, root):
        root.mkdir()
        required = (installer.REQUIRED_FILES | set(installer.STABLE_LINUX_FILES)
                    | set(installer.STABLE_WINDOWS_FILES))
        for relative in required:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{relative}\n", encoding="utf-8")
        shutil.copy2(
            Path(__file__).resolve().parents[2] / "codex_wechat/install_layout.py",
            root / "codex_wechat/install_layout.py",
        )
        return root

    def test_fresh_install_rejects_an_unversioned_directory(self):
        target = self.root / "target"
        target.mkdir()
        (target / "codex_wechat").mkdir()
        (target / "codex_wechat/app.py").write_text("old")
        (target / "installer.py").write_text("old")
        source = self.make_source(self.root / "source")

        def fetch(_choice, destination):
            shutil.copytree(source, destination)

        with patch.object(installer, "fetch_update_source", side_effect=fetch):
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.bootstrap(target, self.choice)
        self.assertEqual((target / "installer.py").read_text(), "old")

    def test_unrelated_directory_is_not_adopted_or_overwritten(self):
        self.target.mkdir()
        (self.target / "notes.txt").write_text("keep")
        source = self.make_source(self.root / "source")

        def fetch(_choice, destination):
            shutil.copytree(source, destination)

        with patch.object(installer, "fetch_update_source", side_effect=fetch):
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.bootstrap(self.target, self.choice)
        self.assertEqual((self.target / "notes.txt").read_text(), "keep")

    def test_dry_run_bypasses_target_and_performs_no_download(self):
        self.target.write_text("not a directory")
        with patch.object(installer, "fetch_update_source") as fetch, \
             patch("builtins.input", return_value="y"):
            self.assertEqual(installer.bootstrap(self.target, self.choice, dry_run=True), 0)
            fetch.assert_not_called()
        self.assertEqual(self.target.read_text(), "not a directory")

    def test_explicit_source_is_required_and_old_repository_is_never_inferred(self):
        self.assertEqual(installer.update_source("https://github.com/example/project.git/"), self.choice)
        with patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "saved_update_source", side_effect=AssertionError("infer repo")):
            with self.assertRaisesRegex(ValueError, "需要 --repo"):
                installer.main(["bootstrap", "--non-interactive"])
            self.assertEqual(installer.main(["bootstrap", "--repo", self.choice["repo"], "--dry-run", "--non-interactive"]), 0)

    def test_download_failure_does_not_create_installation(self):
        with patch.object(installer, "fetch_update_source", side_effect=OSError("offline")), \
             patch.object(installer, "_common_install") as common:
            with self.assertRaises(OSError):
                installer.bootstrap(self.target, self.choice)
            common.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_same_version_rerun_keeps_saved_source_and_repairs_missing_one(self):
        source = self.make_source(self.root / "source")
        files = installer.project_files(source)
        identifier = installer.release_identifier(files)
        with patch.object(installer, "prepare_runtime"):
            installer.install_release(self.target, identifier, files)
        write_installation_layout(
            self.target,
            release_id=identifier,
            data_root=self.root / "data",
            config_root=self.root / "config",
            state_root=self.root / "state",
            create_release=False,
        )
        saved = self.target / installer.UPDATE_SOURCE_FILE
        old = {"repo": "https://github.com/old/repo.git", "ref": "main"}
        saved.write_text(json.dumps(old) + "\n")
        new = installer.update_source("https://github.com/new/repo")

        def fetch(_choice, destination):
            shutil.copytree(source, destination)

        with patch.object(installer, "fetch_update_source", side_effect=fetch), \
             patch.object(installer, "_common_install", return_value=0):
            self.assertEqual(installer.bootstrap(self.target, new), 0)
            self.assertEqual(json.loads(saved.read_text()), old)

            saved.unlink()
            self.assertEqual(installer.bootstrap(self.target, new), 0)
            self.assertEqual(json.loads(saved.read_text()), new)

    @unittest.skipUnless(shutil.which("bash"), "Bash unavailable")
    def test_shell_dry_run_and_invalid_source_do_not_download(self):
        entry = Path(__file__).resolve().parents[2] / "bootstrap.sh"
        for repo, valid in (("https://github.com/example/project", True),
                            ("https://github.com/user/project?token=bad", False)):
            result = subprocess.run([shutil.which("bash"), str(entry), "--repo", repo, "--dry-run"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode == 0, valid, result.stderr)
            if valid:
                self.assertIn("不下载或修改文件", result.stdout)

    @unittest.skipUnless(shutil.which("bash"), "Bash unavailable")
    def test_shell_entry_finds_a_versioned_supported_python(self):
        binary = self.root / "bin"
        binary.mkdir()
        unsupported = binary / "python3"
        unsupported.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        unsupported.chmod(0o755)
        (binary / "python3.11").symlink_to(Path(sys.executable).resolve())
        entry = Path(__file__).resolve().parents[2] / "bootstrap.sh"
        result = subprocess.run(
            [shutil.which("bash"), str(entry), "--dry-run"],
            env={**os.environ, "PATH": str(binary)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("不下载或修改文件", result.stdout)

    def test_network_entry_only_downloads_the_installer(self):
        shell = (Path(__file__).resolve().parents[2] / "bootstrap.sh").read_text(encoding="utf-8")
        powershell = (Path(__file__).resolve().parents[2] / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("installer.py", shell)
        self.assertIn("installer.py", powershell)
        self.assertNotIn("release.py", shell)
        self.assertNotIn("release.py", powershell)

    def test_public_entries_have_no_authentication_path(self):
        root = Path(__file__).resolve().parents[2]
        shell = (root / "bootstrap.sh").read_text(encoding="utf-8")
        powershell = (root / "bootstrap.ps1").read_text(encoding="utf-8")
        for text in (shell, powershell):
            for forbidden_term in (
                "CODEX_" "WECHAT_TOKEN",
                "github_" "pat_",
                "Authorization: Basic",
                "private " "repository",
            ):
                self.assertNotIn(forbidden_term, text)

    def test_dependency_errors_include_platform_install_commands(self):
        root = Path(__file__).resolve().parents[2]
        shell = (root / "bootstrap.sh").read_text(encoding="utf-8")
        local_shell = (root / "install.sh").read_text(encoding="utf-8")
        powershell = (root / "bootstrap.ps1").read_text(encoding="utf-8")
        local_powershell = (root / "install.ps1").read_text(encoding="utf-8")

        for text in (shell, local_shell):
            self.assertIn("pacman -S --needed python git curl", text)
            self.assertIn("apt-get install -y python3 git curl", text)
            self.assertIn("dnf install -y python3 git curl", text)
            self.assertIn("python3.13", text)
            self.assertIn("Ubuntu 22.04", text)
        for text in (powershell, local_powershell):
            self.assertIn("winget install --id $WingetId -e", text)
            self.assertIn("Python.Python.3.13", text)
        self.assertIn("'Git.Git'", powershell)
