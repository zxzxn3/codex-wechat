"""One-line entry chooses install/update without contacting a real repository."""
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import installer
from tests.support.helpers import install_platform


class BootstrapEntryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(install_platform("linux"))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "installation"

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

        def fetch(destination):
            shutil.copytree(source, destination)

        with patch.object(installer, "fetch_official_source", side_effect=fetch):
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.bootstrap(target)
        self.assertEqual((target / "installer.py").read_text(), "old")

    def test_unrelated_directory_is_not_adopted_or_overwritten(self):
        self.target.mkdir()
        (self.target / "notes.txt").write_text("keep")
        source = self.make_source(self.root / "source")

        def fetch(destination):
            shutil.copytree(source, destination)

        with patch.object(installer, "fetch_official_source", side_effect=fetch):
            with self.assertRaisesRegex(ValueError, "非空"):
                installer.bootstrap(self.target)
        self.assertEqual((self.target / "notes.txt").read_text(), "keep")

    def test_dry_run_bypasses_target_and_performs_no_download(self):
        self.target.write_text("not a directory")
        with patch.object(installer, "fetch_official_source") as fetch, \
             patch("builtins.input", return_value="y"):
            self.assertEqual(installer.bootstrap(self.target, dry_run=True), 0)
            fetch.assert_not_called()
        self.assertEqual(self.target.read_text(), "not a directory")

    def test_bootstrap_uses_the_fixed_official_source_and_rejects_overrides(self):
        for arguments in (
            ["bootstrap", "--repo", "https://github.com/example/project"],
            ["bootstrap", "--ref", "stable"],
        ):
            with self.subTest(arguments=arguments), \
                 patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    installer.main([*arguments, "--dry-run", "--non-interactive"])
                self.assertEqual(raised.exception.code, 2)
        with patch.object(installer, "default_target", return_value=self.target):
            self.assertEqual(
                installer.main(["bootstrap", "--dry-run", "--non-interactive"]),
                0,
            )

    def test_download_failure_does_not_create_installation(self):
        with patch.object(installer, "fetch_official_source", side_effect=OSError("offline")), \
             patch.object(installer, "_common_install") as common:
            with self.assertRaises(OSError):
                installer.bootstrap(self.target)
            common.assert_not_called()
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(shutil.which("bash"), "Bash unavailable")
    def test_shell_uses_fixed_source_and_rejects_overrides(self):
        entry = Path(__file__).resolve().parents[2] / "bootstrap.sh"
        result = subprocess.run(
            [shutil.which("bash"), str(entry), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://github.com/zxzxn3/codex-wechat", result.stdout)
        for argument in ("--repo", "--ref"):
            rejected = subprocess.run(
                [shutil.which("bash"), str(entry), argument, "value", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("未知参数", rejected.stderr)

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

    def test_public_entries_have_no_configurable_update_source(self):
        root = Path(__file__).resolve().parents[2]
        shell = (root / "bootstrap.sh").read_text(encoding="utf-8")
        powershell = (root / "bootstrap.ps1").read_text(encoding="utf-8")
        windows_entry = (root / "codex-wechat.ps1").read_text(encoding="utf-8")
        for text in (shell, powershell):
            self.assertIn("zxzxn3/codex-wechat", text)
            self.assertNotIn("--repo", text)
            self.assertNotIn("--ref", text)
        self.assertNotIn("[string]$Repo", windows_entry)
        self.assertNotIn("[string]$Ref", windows_entry)

    def test_public_entries_use_uv_without_host_python_or_git(self):
        root = Path(__file__).resolve().parents[2]
        shell = (root / "bootstrap.sh").read_text(encoding="utf-8")
        local_shell = (root / "install.sh").read_text(encoding="utf-8")
        powershell = (root / "bootstrap.ps1").read_text(encoding="utf-8")
        local_powershell = (root / "install.ps1").read_text(encoding="utf-8")

        for text in (shell, local_shell):
            self.assertNotIn("python3", text)
            self.assertNotIn("git clone", text)
        self.assertIn("UV_UNMANAGED_INSTALL", shell)
        self.assertIn("python find --managed-python", shell)
        self.assertIn(f"UV_VERSION={installer.UV_VERSION}", shell)
        self.assertIn('--source "$source_dir"', shell)
        for text in (powershell, local_powershell):
            self.assertNotIn("py.exe", text)
            self.assertNotIn("git clone", text)
        self.assertIn("UV_UNMANAGED_INSTALL", powershell)
        self.assertIn("python find --managed-python", powershell)
        self.assertIn(f"$UvVersion = '{installer.UV_VERSION}'", powershell)
        self.assertIn("'--source', $SourceDir", powershell)
