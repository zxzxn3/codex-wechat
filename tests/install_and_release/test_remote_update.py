"""Remote update contracts; never contact GitHub or change a real installation."""
import io
import json
from pathlib import Path
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
            installer.fetch_official_source(destination)
        request = open_url.call_args.args[0]
        self.assertEqual(
            request.full_url,
            installer.OFFICIAL_SOURCE_URL,
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
                        installer.fetch_official_source(
                            self.root / ("download-" + member.replace("/", "-")))

    def test_installed_command_uses_official_source_and_explicit_local_source_wins(self):
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
        with patch.object(installer, "SOURCE_ROOT", self.target), \
             patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "bootstrap", return_value=0) as remote, \
             patch.object(installer, "update", return_value=0) as local:
            self.assertEqual(installer.main(["update", "--non-interactive"]), 0)
            remote.assert_called_once_with(self.target, dry_run=False)
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

    def test_installed_update_ignores_legacy_source_metadata(self):
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
        legacy = self.target / ".update-source.json"
        legacy.write_text(json.dumps({
            "repo": "https://github.com/example/legacy",
            "ref": "old",
        }), encoding="utf-8")
        with patch.object(installer, "SOURCE_ROOT", self.target), \
             patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "bootstrap", return_value=0) as official:
            self.assertEqual(installer.main(["update", "--non-interactive"]), 0)
        official.assert_called_once_with(self.target, dry_run=False)
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["ref"], "old")

    def test_update_dry_run_does_not_fetch(self):
        with patch.object(installer, "SOURCE_ROOT", self.target), \
             patch.object(installer, "default_target", return_value=self.target), \
             patch.object(installer, "fetch_official_source") as fetch:
            self.assertEqual(installer.main(["update", "--dry-run", "--non-interactive"]), 0)
        fetch.assert_not_called()
        self.assertFalse(self.target.exists())
