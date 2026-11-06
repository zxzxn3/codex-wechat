"""One-line entry chooses install/update without contacting a real repository."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import installer


class BootstrapEntryTests(unittest.TestCase):
    def setUp(self):
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
        return root

    def test_fresh_install_does_not_treat_an_unversioned_directory_as_an_install(self):
        target = self.root / "target"
        target.mkdir()
        (target / "codex_wechat.py").write_text("old")
        (target / "installer.py").write_text("old")
        with patch.object(installer, "fetch_update_source") as fetch:
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.bootstrap(target, self.choice)
            fetch.assert_not_called()

    def test_unrelated_directory_is_not_adopted_or_downloaded_over(self):
        self.target.mkdir()
        (self.target / "notes.txt").write_text("keep")
        with patch.object(installer, "fetch_update_source") as fetch:
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.bootstrap(self.target, self.choice)
            fetch.assert_not_called()
        self.assertEqual((self.target / "notes.txt").read_text(), "keep")

    def test_dry_run_bypasses_target_and_performs_no_download(self):
        self.target.write_text("not a directory")
        with patch.object(installer, "fetch_update_source") as fetch, \
             patch.object(installer, "_installation_state", side_effect=AssertionError("inspect target")), \
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
        self.target.mkdir()
        files = installer.project_files(source)
        identifier = installer.release_identifier(files)
        with patch.object(installer, "prepare_runtime"):
            installer.install_release(self.target, identifier, files)
        (self.target / installer.CURRENT_FILE).write_text(identifier + "\n")
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
        entry = Path(__file__).resolve().parents[1] / "bootstrap.sh"
        for repo, valid in (("https://github.com/example/project", True),
                            ("https://github.com/user/project?token=bad", False)):
            result = subprocess.run([shutil.which("bash"), str(entry), "--repo", repo, "--dry-run"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode == 0, valid, result.stderr)
            if valid:
                self.assertIn("不下载或修改文件", result.stdout)

    def test_network_entry_fetches_the_release_helper(self):
        shell = (Path(__file__).resolve().parents[1] / "bootstrap.sh").read_text(encoding="utf-8")
        powershell = (Path(__file__).resolve().parents[1] / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("release.py", shell)
        self.assertIn("release.py", powershell)

    @unittest.skipUnless(shutil.which("bash"), "Bash unavailable")
    def test_private_shell_entry_authenticates_clone_and_clears_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            git_arguments = root / "git-arguments"
            installer_arguments = root / "installer-arguments"
            fake_installer = root / "fake-installer.py"
            fake_installer.write_text(
                "import json, os, sys\n"
                "with open(os.environ['FAKE_INSTALLER_ARGS'], 'w', encoding='utf-8') as stream:\n"
                "    json.dump({'args': sys.argv[1:], "
                "'token': os.environ.get('CODEX_WECHAT_TOKEN')}, stream)\n",
                encoding="utf-8")
            fake_git = binaries / "git"
            fake_git.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s\\n' \"$@\" > \"$FAKE_GIT_ARGS\"\n"
                "destination=''\n"
                "for argument in \"$@\"; do destination=$argument; done\n"
                "mkdir -p \"$destination\"\n"
                "cp \"$FAKE_INSTALLER\" \"$destination/installer.py\"\n",
                encoding="utf-8")
            fake_git.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(binaries) + os.pathsep + environment["PATH"]
            environment["CODEX_WECHAT_TOKEN"] = "github_pat_test"
            environment["FAKE_GIT_ARGS"] = str(git_arguments)
            environment["FAKE_INSTALLER"] = str(fake_installer)
            environment["FAKE_INSTALLER_ARGS"] = str(installer_arguments)
            entry = Path(__file__).resolve().parents[1] / "bootstrap.sh"
            result = subprocess.run(
                [shutil.which("bash"), str(entry), "--non-interactive"],
                capture_output=True, text=True, timeout=20, env=environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            git_args = git_arguments.read_text(encoding="utf-8").splitlines()
            header = next(arg for arg in git_args
                          if arg.startswith("http.extraHeader=Authorization: Basic "))
            encoded = header.removeprefix("http.extraHeader=Authorization: Basic ")
            self.assertEqual(base64.b64decode(encoded), b"x-access-token:github_pat_test")
            self.assertNotIn("github_pat_test", "\n".join(git_args))
            self.assertIn("https://github.com/zxzxn3/codex-wechat", git_args)
            self.assertIn("main", git_args)
            self.assertFalse(Path(git_args[-1]).exists())
            installed = json.loads(installer_arguments.read_text(encoding="utf-8"))
            self.assertEqual(installed, {"args": ["menu", "--non-interactive"], "token": None})

    def test_private_entry_contracts_are_present_in_both_platforms(self):
        root = Path(__file__).resolve().parents[1]
        shell = (root / "bootstrap.sh").read_text(encoding="utf-8")
        powershell = (root / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("http.extraHeader", shell)
        self.assertIn("unset auth CODEX_WECHAT_TOKEN", shell)
        self.assertIn("http.extraHeader", powershell)
        self.assertIn("Remove-Item Env:CODEX_WECHAT_TOKEN", powershell)
        self.assertNotIn("$CODEX_WECHAT_TOKEN@github.com", shell)
        self.assertNotIn("$env:CODEX_WECHAT_TOKEN@github.com", powershell)

    def test_private_install_commands_fetch_bootstrap_through_the_contents_api(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ("README.md", "docs/INSTALL.md"):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("contents/bootstrap.sh?ref=main", text)
            self.assertIn("contents/bootstrap.ps1?ref=main", text)
            self.assertIn("application/vnd.github.raw+json", text)
