"""Remote update contracts; never contact GitHub or change a real installation."""
import io
from pathlib import Path
import json
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import installer
from tests.support.helpers import install_platform, write_installation_layout


class RemoteUpdateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(install_platform("linux"))
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

    def make_archive(self, files, *, root="codex-wechat-stable"):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
            for name, data in files.items():
                encoded = data.encode("utf-8")
                member = tarfile.TarInfo(f"{root}/{name}")
                member.size = len(encoded)
                member.mode = 0o644
                bundle.addfile(member, io.BytesIO(encoded))
        return stream.getvalue()

    def test_fetch_uses_the_official_github_archive(self):
        destination = self.root / "download"
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = self.make_archive({
            "LICENSE": "license\n",
            "codex_wechat/install_layout.py": "layout\n",
        })
        with patch.object(installer, "urlopen", return_value=response) as open_url:
            installer.fetch_update_source(self.choice, destination)
        request = open_url.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://codeload.github.com/example/codex-wechat/tar.gz/stable",
        )
        self.assertEqual((destination / "LICENSE").read_text(encoding="utf-8"),
                         "license\n")
        self.assertEqual(
            (destination / "codex_wechat/install_layout.py").read_text(encoding="utf-8"),
            "layout\n",
        )

    def test_fetch_rejects_archive_traversal_and_links(self):
        for member in ("../escape", "root/link"):
            with self.subTest(member=member):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
                    info = tarfile.TarInfo(
                        member if "link" in member else "root/../escape"
                    )
                    if "link" in member:
                        info.type = tarfile.SYMTYPE
                        info.linkname = "/outside"
                    else:
                        payload = b"x"
                        info.size = len(payload)
                    bundle.addfile(info, io.BytesIO(b"x") if info.isfile() else None)
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = stream.getvalue()
                with patch.object(installer, "urlopen", return_value=response):
                    with self.assertRaisesRegex(ValueError, "不安全路径|不是普通文件"):
                        installer.fetch_update_source(
                            self.choice, self.root / ("download-" + member.replace("/", "-")))

    def test_remember_only_after_activation_and_exclude_metadata_from_payload(self):
        self.target.mkdir()
        installer.remember_update_source(self.target, self.source, self.choice)
        self.assertFalse((self.target / installer.UPDATE_SOURCE_FILE).exists())
        identifier = installer.release_identifier(installer.project_files(self.source))
        write_installation_layout(
            self.target,
            release_id=identifier,
            data_root=self.root / "data",
            config_root=self.root / "config",
            state_root=self.root / "state",
        )
        installer.remember_update_source(self.target, self.source, self.choice)
        self.assertEqual(installer.saved_update_source(self.target), self.choice)
        (self.source / installer.UPDATE_SOURCE_FILE).write_text(json.dumps(self.choice))
        self.assertNotIn(Path(installer.UPDATE_SOURCE_FILE), installer.project_files(self.source))

    def test_installed_command_uses_saved_source_and_explicit_local_source_wins(self):
        write_installation_layout(
            self.target,
            data_root=self.root / "data",
            config_root=self.root / "config",
            state_root=self.root / "state",
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
                self.source,
                self.target,
                dry_run=False,
                repair_stable_runtime=False,
            )

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
