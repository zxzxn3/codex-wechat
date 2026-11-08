"""Windows adapters share the Python installer; no real task registration."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def powershell():
    path = os.environ.get("CODEX_WECHAT_PWSH")
    return path if path and Path(path).is_file() else shutil.which("pwsh") or shutil.which("powershell")


class WindowsInstallerAdapterTests(unittest.TestCase):
    def test_powershell_scripts_parse(self):
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell unavailable")
        for name in ('install.ps1', 'bootstrap.ps1', 'codex-wechat.ps1'):
            script = str(ROOT / name).replace("'", "''")
            command = ("$errors=$null; $tokens=$null; "
                       f"$null=[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tokens, [ref]$errors); "
                       "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }")
            result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_internal_mode_requires_explicit_prepared_root(self):
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell unavailable")
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LOCALAPPDATA=directory)
            result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(ROOT/'install.ps1'),
                                     '-PreparedRuntime'], env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('InstallRoot and a usable data root are required', result.stderr)
