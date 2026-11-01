"""Offline contracts for soft Windows dependency checks; no real Windows calls."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def powershell():
    override = os.environ.get("CODEX_WECHAT_PWSH")
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("pwsh") or shutil.which("powershell")


FAKE_BOOTSTRAP = """\
#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args[:2] == ["-m", "venv"]:
    if os.environ.get("FAKE_VENV_MODE") == "fail":
        sys.exit(1)
    target = Path(args[2])
    target.mkdir(parents=True, exist_ok=True)
    python = target / "Scripts" / "python.exe"
    python.write_text(Path(os.environ["FAKE_VENV_SOURCE"]).read_text(), encoding="utf-8")
    python.chmod(0o755)
    sys.exit(0)
if args and args[0] == "-c":
    sys.exit(0 if os.environ.get("FAKE_BOOTSTRAP_VERSION_OK", "1") == "1" else 1)
sys.exit(0)
"""


FAKE_VENV_PYTHON = """\
#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]

def log(value):
    path = os.environ.get("FAKE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(value + "\\n")

if args[:2] == ["-m", "pip"]:
    log("PIP")
    sys.exit(int(os.environ.get("FAKE_PIP_EXIT", "0")))
if args and args[0] == "-c":
    code = args[1]
    if "sys.version_info" in code:
        sys.exit(0 if os.environ.get("FAKE_VERSION_OK", "1") == "1" else 1)
    if "cryptography" in code:
        sys.exit(0 if os.environ.get("FAKE_DEPS_OK", "1") == "1" else 1)
    sys.exit(0)
if args and args[0] == "-u":
    log("LOGIN")
    if os.environ.get("FAKE_LOGIN_OK", "1") != "1":
        sys.exit(1)
    state = Path(args[args.index("--state-dir") + 1])
    state.mkdir(parents=True, exist_ok=True)
    (state / "account.json").write_text("{}\\n", encoding="utf-8")
    sys.exit(0)
sys.exit(0)
"""


FAKE_CODEX = """\
#!/usr/bin/env python3
import os
import sys

if "--version" in sys.argv[1:]:
    print("codex 0.166.0")
sys.exit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))
"""


FAKE_CURL = """\
#!/usr/bin/env python3
import os
import sys

print(os.environ.get("FAKE_CURL_VERSION", "curl 8.4.0"))
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")))
"""


WRAPPER = """\
$env:OS = 'Windows_NT'
$script:Tasks = @{{}}

function Log-Call([string]$Name) {{
    Add-Content -LiteralPath $env:FAKE_LOG -Value $Name
}}

function Get-Command {{
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)][string]$Name,
        [string]$CommandType
    )
    if ($Name -eq 'py.exe') {{ return $null }}
    if ($Name -eq 'python.exe' -and $env:FAKE_PYTHON) {{
        return [pscustomobject]@{{ Source = $env:FAKE_PYTHON }}
    }}
    if ($Name -eq 'codex.exe' -and $env:FAKE_CODEX) {{
        return [pscustomobject]@{{ Source = $env:FAKE_CODEX }}
    }}
    if ($Name -eq 'curl.exe' -and $env:FAKE_CURL) {{
        return [pscustomobject]@{{ Source = $env:FAKE_CURL }}
    }}
    return $null
}}

function Get-ScheduledTask {{
    [CmdletBinding()]
    param([string]$TaskName)
    return $script:Tasks[$TaskName]
}}

function New-ScheduledTaskAction {{
    [CmdletBinding()]
    param([string]$Execute, [string]$Argument, [string]$WorkingDirectory)
    return [pscustomobject]@{{
        Execute = $Execute
        Arguments = $Argument
        WorkingDirectory = $WorkingDirectory
    }}
}}

function New-ScheduledTaskTrigger {{
    [CmdletBinding()]
    param([switch]$AtLogOn, [string]$User)
    return [pscustomobject]@{{
        CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskLogonTrigger' }}
        Enabled = $true
        UserId = $User
    }}
}}

function New-ScheduledTaskPrincipal {{
    [CmdletBinding()]
    param([string]$UserId, [string]$LogonType, [string]$RunLevel)
    return [pscustomobject]@{{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel }}
}}

function New-ScheduledTaskSettingsSet {{
    [CmdletBinding()]
    param(
        [switch]$AllowStartIfOnBatteries,
        [switch]$DontStopIfGoingOnBatteries,
        [TimeSpan]$ExecutionTimeLimit,
        [string]$MultipleInstances,
        [switch]$StartWhenAvailable,
        [switch]$Hidden
    )
    return [pscustomobject]@{{ Enabled = $true }}
}}

function Register-ScheduledTask {{
    [CmdletBinding()]
    param(
        [string]$TaskName,
        [object]$Action,
        [object]$Trigger,
        [object]$Principal,
        [object]$Settings,
        [switch]$Force
    )
    Log-Call 'REGISTER'
    $script:Tasks[$TaskName] = [pscustomobject]@{{
        Actions = @($Action)
        Triggers = @($Trigger)
        Settings = [pscustomobject]@{{ Enabled = $true }}
        State = 'Ready'
    }}
}}

function Set-ScheduledTask {{
    [CmdletBinding()]
    param(
        [string]$TaskName,
        [object]$Action,
        [object]$Trigger,
        [object]$Principal,
        [object]$Settings
    )
    Log-Call 'SET'
    $script:Tasks[$TaskName].Actions = @($Action)
    $script:Tasks[$TaskName].Triggers = @($Trigger)
    $script:Tasks[$TaskName].Settings = [pscustomobject]@{{ Enabled = $true }}
}}

function Enable-ScheduledTask {{
    [CmdletBinding()]
    param([string]$TaskName)
    Log-Call 'ENABLE'
    $script:Tasks[$TaskName].Settings.Enabled = $true
}}

function Start-ScheduledTask {{
    [CmdletBinding()]
    param([string]$TaskName)
    Log-Call 'START'
    $script:Tasks[$TaskName].State = 'Running'
}}

if ($env:FAKE_EXISTING_TASK) {{
    $actionText = if ($env:FAKE_EXISTING_TASK -eq 'owned') {{
        $env:FAKE_OWNED_ACTION
    }} else {{
        '"C:\\other\\app.exe" run'
    }}
    $script:Tasks[$env:FAKE_TASK_NAME] = [pscustomobject]@{{
        Actions = @([pscustomobject]@{{ Arguments = $actionText }})
        Triggers = @([pscustomobject]@{{
            CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskLogonTrigger' }}
            Enabled = $true
        }})
        Settings = [pscustomobject]@{{ Enabled = $true }}
        State = 'Ready'
    }}
}}

$installParams = @{{}}
if ($env:FAKE_AUTOSTART) {{ $installParams['Autostart'] = $env:FAKE_AUTOSTART }}
if ($env:FAKE_NO_START -eq '1') {{ $installParams['NoStart'] = $true }}
if ($env:FAKE_FORCE -eq '1') {{ $installParams['Force'] = $true }}
. '__INSTALL__' @installParams
"""


class DependencyScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.install = (ROOT / "install.ps1").read_text(encoding="utf-8")

    def test_dependency_problems_are_warnings_not_install_aborts(self):
        for text in (
                "Add-Warning 'Python 3.11+ is unavailable",
                "Add-Warning 'Native Codex CLI",
                "Add-Warning 'curl.exe 8.4"):
            self.assertIn(text, self.install)
        self.assertNotIn("Fail 'Python 3.11", self.install)
        self.assertNotIn("Fail 'curl.exe", self.install)
        self.assertNotIn("Fail 'native Codex", self.install)
        self.assertNotIn("Fail 'could not create the Python virtual environment'", self.install)

    def test_safety_and_parameter_errors_still_abort(self):
        for text in (
                "Fail 'this installer is for Windows only'",
                "Fail 'Windows 11 is required'",
                "non-interactive install requires an explicit -Autostart on|off choice",
                "data path must not be a reparse point",
                "exists but is not this installation"):
            self.assertIn(text, self.install)

    def test_final_summary_distinguishes_task_login_and_start(self):
        for text in (
                "Task: registered",
                "Task: not registered (no usable Python)",
                "Task: existing task preserved; not updated",
                "Login: ",
                "Start: ",
                "Result: preparation incomplete"):
            self.assertIn(text, self.install)
        self.assertNotIn("Write-Host 'Installed.", self.install)


class InstallerFlowTests(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            self.skipTest("POSIX fake executables are used for this harness")
        self.shell = powershell()
        if not self.shell:
            self.skipTest("PowerShell is not available")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.local = self.root / "local"
        self.local.mkdir()
        self.data = self.local / "codex-wechat"
        self.log = self.root / "calls.log"

        source = (ROOT / "install.ps1").read_text(encoding="utf-8")
        os_checks = textwrap.dedent("""\
            if ($env:OS -ne 'Windows_NT') {
                Fail 'this installer is for Windows only'
            }
            if ([Environment]::OSVersion.Version.Build -lt 22000) {
                Fail 'Windows 11 is required'
            }
        """)
        self.assertIn(os_checks, source)
        source = source.replace(os_checks, "# test fixture: OS checks bypassed\n")
        identity = "[Security.Principal.WindowsIdentity]::GetCurrent().Name"
        self.assertIn(identity, source)
        source = source.replace(identity, "'test-user'")
        self.install = self.project / "install.ps1"
        self.install.write_text(source, encoding="utf-8")

        (self.project / "codex_wechat.py").write_text("", encoding="utf-8")
        (self.project / "windows_service.py").write_text("", encoding="utf-8")
        (self.project / "requirements.txt").write_text("cryptography>=38\n", encoding="utf-8")

        self.bootstrap = self.make_executable("python-bootstrap", FAKE_BOOTSTRAP)
        self.codex = self.make_executable("codex.exe", FAKE_CODEX)
        self.curl = self.make_executable("curl.exe", FAKE_CURL)
        self.venv_source = self.make_executable("venv-python-source", FAKE_VENV_PYTHON)
        self.venv_python = self.project / ".venv" / "Scripts" / "python.exe"

    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def make_executable(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def install_venv(self):
        self.venv_python.parent.mkdir(parents=True, exist_ok=True)
        self.venv_python.write_text(FAKE_VENV_PYTHON, encoding="utf-8")
        self.venv_python.chmod(0o755)

    def run_installer(self, *, python="ready", venv="ready", deps=True, pip=True,
                      codex=True, curl=True, account=False, autostart="on",
                      no_start=False, interactive=False, login=False,
                      existing=None, force=False, bootstrap_version=True,
                      venv_fail=False):
        if venv == "ready":
            self.install_venv()
        elif venv == "broken":
            (self.project / ".venv").mkdir()

        if account:
            state = self.data / ".state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "account.json").write_text("{}\n", encoding="utf-8")
        elif (self.data / ".state" / "account.json").exists():
            (self.data / ".state" / "account.json").unlink()

        wrapper = (WRAPPER.replace("{{", "{").replace("}}", "}")
                   .replace("__INSTALL__", str(self.install).replace("'", "''")))
        wrapper_path = self.root / "run-install.ps1"
        wrapper_path.write_text(wrapper, encoding="utf-8")

        env = dict(os.environ)
        env.update({
            "LOCALAPPDATA": str(self.local),
            "FAKE_LOG": str(self.log),
            "FAKE_PYTHON": str(self.bootstrap) if python != "missing" else "",
            "FAKE_BOOTSTRAP_VERSION_OK": "1" if bootstrap_version else "0",
            "FAKE_VENV_SOURCE": str(self.venv_source),
            "FAKE_VENV_MODE": "fail" if venv_fail else "ok",
            "FAKE_VERSION_OK": "1",
            "FAKE_DEPS_OK": "1" if deps else "0",
            "FAKE_PIP_EXIT": "0" if pip else "1",
            "FAKE_LOGIN_OK": "1" if login else "0",
            "FAKE_CODEX": str(self.codex) if codex else "",
            "FAKE_CURL": str(self.curl) if curl else "",
            "FAKE_AUTOSTART": autostart,
            "FAKE_TASK_NAME": "CodexWeChat",
            "FAKE_OWNED_ACTION": f'"{self.project / "windows_service.py"}" supervise --project x',
        })
        if no_start:
            env["FAKE_NO_START"] = "1"
        if force:
            env["FAKE_FORCE"] = "1"
        if existing:
            env["FAKE_EXISTING_TASK"] = existing
        if interactive:
            fixture = self.install.read_text(encoding="utf-8")
            fixture = fixture.replace("[Console]::IsInputRedirected", "$false")
            fixture = fixture.replace("[Console]::IsOutputRedirected", "$false")
            fixture = fixture.replace("[Environment]::UserInteractive", "$true")
            self.install.write_text(fixture, encoding="utf-8")

        result = subprocess.run(
            [self.shell, "-NoLogo", "-NoProfile", "-NonInteractive",
             "-File", str(wrapper_path)],
            env=env, capture_output=True, text=True, timeout=120)
        calls = self.log.read_text(encoding="utf-8").split() if self.log.exists() else []
        return result, calls

    def output(self, result):
        return " ".join((result.stdout + result.stderr).split())

    def test_missing_codex_and_curl_warn_but_register_task_without_start(self):
        result, calls = self.run_installer(
            codex=False, curl=False, account=True, autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Task: registered", out)
        self.assertIn("Start: not started (dependencies missing)", out)
        self.assertIn("Result: preparation incomplete", out)
        self.assertIn("Codex CLI", out)
        self.assertIn("curl.exe", out)
        self.assertIn("REGISTER", calls)
        self.assertNotIn("START", calls)
        self.assertTrue((self.data / "config.json").exists())

    def test_missing_python_keeps_config_but_registers_no_task(self):
        result, calls = self.run_installer(
            python="missing", venv="missing", codex=True, curl=True,
            account=False, autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Task: not registered (no usable Python)", out)
        self.assertIn("Login: skipped (no usable Python)", out)
        self.assertIn("Start: not started (task not registered)", out)
        self.assertIn("Result: preparation incomplete", out)
        self.assertNotIn("REGISTER", calls)
        self.assertNotIn("SET", calls)
        self.assertNotIn("LOGIN", calls)
        self.assertTrue((self.data / "config.json").exists())

    def test_old_python_warns_without_registering_task(self):
        result, calls = self.run_installer(
            python="old", venv="missing", bootstrap_version=False,
            codex=True, curl=True, account=True, autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Python 3.11+ is unavailable", out)
        self.assertIn("Task: not registered", out)
        self.assertNotIn("REGISTER", calls)

    def test_venv_creation_failure_is_soft(self):
        result, calls = self.run_installer(
            venv="missing", venv_fail=True, codex=True, curl=True,
            account=True, autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Could not create a usable .venv", out)
        self.assertIn("Task: not registered", out)
        self.assertNotIn("REGISTER", calls)
        self.assertTrue((self.data / "config.json").exists())

    def test_pip_failure_with_existing_deps_still_starts(self):
        result, calls = self.run_installer(
            pip=False, deps=True, codex=True, curl=True, account=True,
            autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("pip could not install", out)
        self.assertIn("Task: registered", out)
        self.assertIn("Start: start request sent", out)
        self.assertIn("START", calls)

    def test_missing_python_dependencies_register_task_but_do_not_start(self):
        result, calls = self.run_installer(
            deps=False, pip=False, codex=True, curl=True, account=True,
            autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Python dependencies are missing", out)
        self.assertIn("Task: registered", out)
        self.assertIn("Start: not started (dependencies missing)", out)
        self.assertIn("REGISTER", calls)
        self.assertNotIn("START", calls)

    def test_missing_curl_skips_login_and_does_not_start(self):
        result, calls = self.run_installer(
            codex=True, curl=False, account=False, autostart="on")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Task: registered", out)
        self.assertIn("Login: skipped (curl missing or too old)", out)
        self.assertIn("Start: not started (WeChat not bound)", out)
        self.assertNotIn("LOGIN", calls)
        self.assertNotIn("START", calls)

    def test_interactive_login_success_then_start(self):
        result, calls = self.run_installer(
            codex=True, curl=True, account=False, autostart="on",
            interactive=True, login=True)
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Login: completed", out)
        self.assertIn("Start: start request sent", out)
        self.assertIn("LOGIN", calls)
        self.assertIn("START", calls)
        self.assertTrue((self.data / ".state" / "account.json").exists())

    def test_no_start_preserves_task_and_autostart(self):
        result, calls = self.run_installer(
            codex=True, curl=True, account=True, autostart="off", no_start=True)
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Task: registered", out)
        self.assertIn("Autostart: off", out)
        self.assertIn("Start: not requested (-NoStart)", out)
        self.assertIn("Result: ready; start was not requested", out)
        self.assertIn("REGISTER", calls)
        self.assertNotIn("START", calls)

    def test_noninteractive_without_autostart_fails_as_parameter_error(self):
        result, calls = self.run_installer(
            codex=True, curl=True, account=True, autostart="")
        out = self.output(result)
        self.assertNotEqual(result.returncode, 0, out)
        self.assertIn("non-interactive install requires an explicit -Autostart", out)
        self.assertNotIn("REGISTER", calls)
        self.assertNotIn("START", calls)

    def test_unowned_task_aborts_before_registration(self):
        result, calls = self.run_installer(
            codex=True, curl=True, account=True, autostart="on",
            existing="foreign")
        out = self.output(result)
        self.assertNotEqual(result.returncode, 0, out)
        self.assertIn("exists but is not this", out)
        self.assertNotIn("REGISTER", calls)
        self.assertNotIn("SET", calls)

    def test_existing_owned_task_is_preserved_when_python_missing(self):
        result, calls = self.run_installer(
            python="missing", venv="missing", codex=True, curl=True,
            account=True, autostart="on", existing="owned")
        out = self.output(result)
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Task: existing task preserved; not updated", out)
        self.assertIn("Autostart: not applied", out)
        self.assertIn("Start: not started (task not updated; Python missing)", out)
        self.assertNotIn("REGISTER", calls)
        self.assertNotIn("SET", calls)


if __name__ == "__main__":
    unittest.main()
