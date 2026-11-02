"""Isolated PowerShell entry contracts; no real App, daemon, or WeChat calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")

@unittest.skipUnless(POWERSHELL, "PowerShell unavailable")
class DesktopEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = self.root / "codex-desktop.ps1"
        self.entry.write_bytes((ROOT / "codex-desktop.ps1").read_bytes())
        self.output = self.root / "result.json"
        self.env = dict(os.environ, CW_TEST_ENTRY=str(self.entry),
                        CW_TEST_OUTPUT=str(self.output))
        self.env.pop("CODEX_WECHAT_INSTALL_ROOT", None)

    def script(self, root, name, text):
        path = root / "windows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8-sig")

    def run_ps(self, command):
        return subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command],
                              env=self.env, capture_output=True, timeout=20)

    def test_cli_forwards_arguments_and_exit_code(self):
        self.script(self.root, "Start-Cli.ps1", """
[IO.File]::WriteAllText($env:CW_TEST_OUTPUT, (ConvertTo-Json -InputObject @($args) -Compress))
exit 7
""")
        result = self.run_ps(r"& $env:CW_TEST_ENTRY cli resume -C 'C:\space folder'; exit $LASTEXITCODE")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(self.output.read_text()), ["resume", "-C", r"C:\space folder"])

    def test_current_pointer_selects_release(self):
        identifier = "1234567890abcdef"
        (self.root / "current").write_text(identifier)
        self.script(self.root / "releases" / identifier, "Start-SharedBackend.ps1", """
[IO.File]::WriteAllText($env:CW_TEST_OUTPUT, 'release')
exit 0
""")
        result = self.run_ps("Set-StrictMode -Version Latest; & $env:CW_TEST_ENTRY ensure; exit $LASTEXITCODE")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "release")

    def test_environment_restored_after_delegation(self):
        self.script(self.root, "Start-SharedBackend.ps1", """
$env:HTTP_PROXY='changed'
$env:CODEX_APP_SERVER_WS_URL='changed'
exit 0
""")
        self.env["HTTP_PROXY"] = "original"
        self.env.pop("CODEX_APP_SERVER_WS_URL", None)
        result = self.run_ps("""
& $env:CW_TEST_ENTRY ensure
[IO.File]::WriteAllText($env:CW_TEST_OUTPUT, (@{proxy=$env:HTTP_PROXY;ws=$env:CODEX_APP_SERVER_WS_URL}|ConvertTo-Json))
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text()), {"proxy": "original", "ws": None})

    def test_invalid_version_pointer_does_not_launch(self):
        (self.root / "current").write_text("../outside")
        result = self.run_ps("& $env:CW_TEST_ENTRY ensure")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
