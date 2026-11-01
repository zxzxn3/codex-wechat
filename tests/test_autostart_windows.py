"""Offline contracts for Windows autostart selection; no Task Scheduler calls."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def powershell():
    override = os.environ.get("CODEX_WECHAT_PWSH")
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("pwsh") or shutil.which("powershell")


class AutostartScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.entry = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.install = (ROOT / "install.ps1").read_text(encoding="utf-8")

    def test_autostart_branch_only_touches_login_triggers(self):
        branch = self.entry.split("'autostart' {", 1)[1].split("'logs' {", 1)[0]
        self.assertIn("Get-AutostartTask", branch)
        self.assertIn("$trigger.Enabled = $enabled", branch)
        self.assertIn(
            "Set-ScheduledTask -TaskName $TaskName -Trigger $triggers", branch)
        self.assertNotIn("-Action", branch)
        self.assertNotIn("-Settings", branch)
        self.assertNotIn("Stop-ScheduledTask", branch)
        self.assertNotIn("Unregister-ScheduledTask", branch)
        self.assertNotIn("Disable-ScheduledTask", self.entry)
        self.assertIn("ConvertTo-Json -Compress", self.entry)

    def test_ownership_check_has_an_exact_prefix_boundary(self):
        for text in (self.entry, self.install):
            self.assertIn("[StringComparison]::OrdinalIgnoreCase", text)
            self.assertIn(
                "[char]::IsWhiteSpace($actionText[$prefix.Length])",
                text)
        self.assertIn("$actions.Count -ne 1", self.entry)
        self.assertIn("$actions.Count -ne 1", self.install)

    def test_installer_keeps_autostart_and_immediate_start_independent(self):
        lowered = self.install.lower()
        self.assertIn("[switch]$nostart", lowered)
        self.assertIn("[validateset('on', 'off')]", lowered)
        self.assertIn("explicit -autostart on|off choice", lowered)
        self.assertIn("$trigger.Enabled = [bool]$autostartEnabled", self.install)
        self.assertIn("Enable-ScheduledTask", self.install)
        self.assertNotIn("RestartCount", self.install)
        self.assertNotIn("RestartInterval", self.install)
        self.assertNotIn("Disable-ScheduledTask", self.install)

    def test_scheduled_task_can_target_stable_release_bootstrap(self):
        self.assertIn("bootstrap_supervisor.py", self.install)
        self.assertIn(".bootstrap-venv\\Scripts\\pythonw.exe", self.install)
        self.assertIn("'--install-root'", self.install)
        self.assertIn("[switch]$PreserveAutostart", self.install)
        self.assertIn("bootstrap_supervisor.py", self.entry)
        self.assertIn(r"releases\$ReleaseId", self.entry)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", self.entry)

    def test_autostart_parameters_are_rejected_by_other_commands(self):
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell is not available")
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ)
            env["LOCALAPPDATA"] = temp
            for arguments in (["status", "on"], ["status", "-Json"]):
                result = subprocess.run(
                    [shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                     "-File", str(ROOT / "codex-wechat.ps1"), *arguments],
                    env=env, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0, arguments)
                stderr = " ".join(result.stderr.split())
                self.assertIn(
                    "only valid with the autostart", stderr, arguments)

    def test_scripts_parse_with_the_powershell_parser(self):
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell is not available")
        for name in ("codex-wechat.ps1", "install.ps1"):
            path = str(ROOT / name).replace("'", "''")
            command = (
                "$errors = $null; $tokens = $null; "
                f"$null = [System.Management.Automation.Language.Parser]::ParseFile("
                f"'{path}', [ref]$tokens, [ref]$errors); "
                "if ($errors.Count -gt 0) { "
                "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }")
            result = subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-Command", command],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
