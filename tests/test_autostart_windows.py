"""Offline contracts for Windows autostart selection; no Task Scheduler calls."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import autostart

ROOT = Path(__file__).resolve().parents[1]


def powershell():
    override = os.environ.get("CODEX_WECHAT_PWSH")
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("pwsh") or shutil.which("powershell")


class AutostartScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.entry = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.task = (ROOT / "windows/task-service.ps1").read_text(encoding="utf-8")
        self.install = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.uninstall = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")

    def test_autostart_branch_only_touches_login_triggers(self):
        branch = self.task.split("'autostart' {", 1)[1]
        self.assertIn("Get-LogonTriggers", branch)
        self.assertIn("$trigger.Enabled = $enabled", branch)
        self.assertIn(
            "Set-ScheduledTask -TaskName $TaskName -Trigger $allTriggers", branch)
        self.assertNotIn("-Action", branch)
        self.assertNotIn("-Settings", branch)
        self.assertNotIn("Stop-ScheduledTask", branch)
        self.assertNotIn("Unregister-ScheduledTask", branch)
        self.assertNotIn("Disable-ScheduledTask", self.task)
        self.assertIn("ConvertTo-Json -Compress", self.task)

    def test_logon_triggers_stay_a_collection_for_single_trigger_tasks(self):
        # PowerShell unrolls a one-element array on return, and $scalar.Count
        # throws under Set-StrictMode, so the helper must not rely on `return @()`.
        self.assertIn("Write-Output -NoEnumerate", self.task)
        self.assertNotIn("return @($Task.Triggers", self.task)

    def test_ownership_check_has_an_exact_prefix_boundary(self):
        for text in (self.task, self.install):
            self.assertIn("[StringComparison]::OrdinalIgnoreCase", text)
            self.assertTrue("[char]::IsWhiteSpace($actionText[$prefix.Length])" in text
                            or "[char]::IsWhiteSpace($text[$prefix.Length])" in text
                            or "[char]::IsWhiteSpace($arguments[$ExpectedActionPrefix.Length])" in text)
        self.assertIn("$ExpectedExecutables", self.task)
        self.assertIn("$actions[0].Execute", self.task)
        self.assertIn("$execute = [string]$actions[0].Execute", self.install)
        self.assertIn("$actions.Count -ne 1", self.task)
        self.assertIn("$actions.Count -eq 1", self.install)
        self.assertNotIn("Join-Path $Project 'windows_service.py'", self.install)

    def test_installer_keeps_autostart_and_immediate_start_independent(self):
        lowered = self.install.lower()
        self.assertIn("[switch]$nostart", lowered)
        self.assertIn("[switch]$preparedruntime", lowered)
        self.assertIn("$autostart = $false", self.install)
        self.assertIn("$trigger.Enabled = $autostart", self.install)
        self.assertNotIn("Start-ScheduledTask", self.install)
        self.assertNotIn("RestartCount", self.install)
        self.assertNotIn("RestartInterval", self.install)
        self.assertNotIn("Disable-ScheduledTask", self.install)

    def test_scheduled_task_can_target_stable_release_bootstrap(self):
        self.assertIn("bootstrap_supervisor.py", self.install)
        self.assertIn(".bootstrap-venv\\Scripts\\pythonw.exe", self.install)
        self.assertIn("'--install-root'", self.install)
        self.assertIn("[switch]$PreserveAutostart", self.install)
        self.assertIn("bootstrap_supervisor.py", self.task)
        self.assertIn(r"releases\$ReleaseId", self.entry)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", self.entry)

    def test_entry_and_task_honor_the_custom_data_root(self):
        for text in (self.entry, self.install, self.uninstall):
            self.assertIn("Read-SavedDataRoot", text)
            self.assertIn(".data-root", text)
        for text in (self.entry, self.install):
            self.assertIn("$DataRoot = if ($env:CODEX_WECHAT_DATA_ROOT)", text)
            self.assertIn("[IO.Path]::GetFullPath($env:CODEX_WECHAT_DATA_ROOT)", text)
            self.assertIn("Join-Path $env:LOCALAPPDATA 'codex-wechat'", text)
        self.assertIn("不一致", self.entry)
        self.assertIn("不一致", self.uninstall)
        self.assertNotIn("'--data-root'", self.install)
        self.assertNotIn("'--state-dir'", self.install)
        self.assertNotIn("'--config'", self.install)
        self.assertNotIn("'--log'", self.install)

    def test_fast_path_requires_the_exact_stable_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(autostart, "INSTALL_ROOT", root), \
                    patch.object(autostart, "_exec_action", side_effect=lambda action: action):
                # The Scheduling COM action exposes `Path`; it has no `Execute`
                # member, so a fake carrying one would hide a field-name bug.
                action = SimpleNamespace(
                    Path=str(root / ".bootstrap-venv/Scripts/pythonw.exe"),
                    Arguments=f'"{root / "bootstrap_supervisor.py"}" supervise --state-dir "state"')
                self.assertTrue(autostart._owned_supervisor_action(action))
                action.Arguments = f'"{root / "other" / "bootstrap_supervisor.py"}" supervise'
                self.assertFalse(autostart._owned_supervisor_action(action))
                action.Arguments = f'"{root / "bootstrap_supervisor.py"}" supervised'
                self.assertFalse(autostart._owned_supervisor_action(action))
                action.Arguments = f'"{root / "bootstrap_supervisor.py"}" supervise'
                action.Path = str(root / ".bootstrap-venv/Scripts/other.exe")
                self.assertFalse(autostart._owned_supervisor_action(action))
                action.Path = str(root / ".bootstrap-venv/Scripts/python.exe")
                self.assertTrue(autostart._owned_supervisor_action(action))

    def test_autostart_parameters_are_rejected_by_other_commands(self):
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell is not available")
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ)
            env["LOCALAPPDATA"] = temp
            for arguments in (["status", "-Value", "true"], ["status", "-Follow"]):
                result = subprocess.run(
                    [shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                     "-File", str(ROOT / "codex-wechat.ps1"), *arguments],
                    env=env, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0, arguments)
                stderr = " ".join(result.stderr.split())
                self.assertNotIn("Traceback", stderr)

    def test_scripts_parse_with_the_powershell_parser(self):
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell is not available")
        for name in ("codex-wechat.ps1", "codex-desktop.ps1", "install.ps1",
                     "uninstall.ps1", "windows/task-service.ps1"):
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
