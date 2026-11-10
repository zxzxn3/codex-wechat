"""Remote update contracts; never contact GitHub or change a real installation."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import installer
from tests.support.helpers import write_installation_layout


class RemoteUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "installed"
        self.source = self.root / "source"
        self.source.mkdir()
        for name in installer.REQUIRED_FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public payload\n")
        shutil.copy2(
            Path(__file__).resolve().parents[2] / "codex_wechat/install_layout.py",
            self.source / "codex_wechat/install_layout.py",
        )
        self.choice = installer.update_source("https://github.com/example/codex-wechat", "stable")

    def test_source_validation_rejects_credentials_paths_and_options(self):
        self.assertEqual(self.choice, {"repo": "https://github.com/example/codex-wechat.git", "ref": "stable"})
        for url in ("file:///tmp/repo", "https://token@github.com/user/repo",
                    "https://github.com/user/repo?token=secret", "https://other.example/user/repo",
                    "https://github.com/user/repo/tree/main"):
            with self.assertRaises(ValueError):
                installer.update_source(url)
        for ref in ("", "-option", "branch\nother", "branch name", 123):
            with self.assertRaises(ValueError):
                installer.update_source(self.choice["repo"], ref)

    def test_fetch_is_shallow_public_clone_with_clean_git_context(self):
        destination = self.root / "download"
        with patch.object(installer.shutil, "which", return_value="/fixture/git"), \
             patch.object(installer.subprocess, "run") as run, \
             patch.dict(os.environ, {"GIT_DIR": "/private/cfg", "GIT_WORK_TREE": "/private/home"}):
            installer.fetch_update_source(self.choice, destination)
        args = run.call_args.args[0]
        self.assertEqual(args[-3:], ["--", self.choice["repo"], str(destination)])
        self.assertIn("--depth", args)
        self.assertIn("stable", args)
        env = run.call_args.kwargs["env"]
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_remote_dry_run_neither_fetches_nor_inspects_installation(self):
        with patch.object(installer, "fetch_update_source") as fetch:
            self.assertEqual(installer.remote_update(self.target, self.choice, dry_run=True), 0)
        fetch.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_download_failure_preserves_version_and_previous_source(self):
        layout = write_installation_layout(
            self.target,
            data_root=self.root / "data",
        )
        manifest = layout.manifest_path.read_bytes()
        saved = self.target / installer.UPDATE_SOURCE_FILE
        saved.write_text(json.dumps(self.choice))
        before = saved.read_bytes()
        with patch.object(installer, "fetch_update_source", side_effect=subprocess.CalledProcessError(1, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.remote_update(self.target, self.choice)
        self.assertEqual(layout.manifest_path.read_bytes(), manifest)
        self.assertEqual(saved.read_bytes(), before)

    def test_remember_only_after_activation_and_exclude_metadata_from_payload(self):
        self.target.mkdir()
        installer.remember_update_source(self.target, self.source, self.choice)
        self.assertFalse((self.target / installer.UPDATE_SOURCE_FILE).exists())
        identifier = installer.release_identifier(installer.project_files(self.source))
        write_installation_layout(
            self.target,
            release_id=identifier,
            data_root=self.root / "data",
        )
        installer.remember_update_source(self.target, self.source, self.choice)
        self.assertEqual(installer.saved_update_source(self.target), self.choice)
        (self.source / installer.UPDATE_SOURCE_FILE).write_text(json.dumps(self.choice))
        self.assertNotIn(Path(installer.UPDATE_SOURCE_FILE), installer.project_files(self.source))

    def test_installed_command_uses_saved_source_and_explicit_local_source_wins(self):
        write_installation_layout(
            self.target,
            data_root=self.root / "data",
        )
        (self.target / "codex_wechat").mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            Path(__file__).resolve().parents[2] / "codex_wechat/install_layout.py",
            self.target / "codex_wechat/install_layout.py",
        )
        (self.target / installer.UPDATE_SOURCE_FILE).write_text(json.dumps(self.choice))
        with patch.object(installer, "SOURCE_ROOT", self.target), \
             patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "bootstrap", return_value=0) as remote, \
             patch.object(installer, "update", return_value=0) as local:
            self.assertEqual(installer.main(["update", "--non-interactive"]), 0)
            remote.assert_called_once_with(
                self.target, self.choice, dry_run=False)
            remote.reset_mock()
            with self.assertRaises(SystemExit):
                installer.main([
                    "update", "--non-interactive", "--reconfigure-roots",
                ])
            remote.assert_not_called()
            self.assertEqual(installer.main(["update", "--source", str(self.source), "--non-interactive"]), 0)
            local.assert_called_once_with(
                self.source, self.target, dry_run=False)

    def test_missing_source_does_not_infer_any_git_remote(self):
        with patch.object(installer, "SOURCE_ROOT", self.target), \
             patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "fetch_update_source") as fetch:
            with self.assertRaisesRegex(ValueError, "尚未配置更新来源"):
                installer.main(["update", "--non-interactive"])
        fetch.assert_not_called()

    def test_unconfigured_source_still_allows_dry_run(self):
        with patch.object(installer, "SOURCE_ROOT", self.target), \
             patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "fetch_update_source") as fetch:
            self.assertEqual(installer.main(["update", "--dry-run", "--non-interactive"]), 0)
        fetch.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_update_source_symlink_and_invalid_ref_are_rejected(self):
        self.target.mkdir()
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.choice))
        link = self.target / installer.UPDATE_SOURCE_FILE
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            installer.saved_update_source(self.target)

    def test_same_version_remote_noop_keeps_saved_source(self):
        identifier = installer.release_identifier(installer.project_files(self.source))
        write_installation_layout(
            self.target,
            release_id=identifier,
            data_root=self.root / "data",
        )
        saved = self.target / installer.UPDATE_SOURCE_FILE
        saved.write_text(json.dumps(self.choice) + "\n")
        new = installer.update_source("https://github.com/other/repo")
        with patch.object(
                installer, "fetch_update_source",
                side_effect=lambda _choice, destination: shutil.copytree(
                    self.source, destination)), \
             patch.object(installer, "update", return_value=0):
            self.assertEqual(installer.remote_update(self.target, new), 0)
        self.assertEqual(json.loads(saved.read_text()), self.choice)
