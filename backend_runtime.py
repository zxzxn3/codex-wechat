"""Prepare native Codex runtimes; client shutdown never stops a shared backend."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import daemon_control
from codex_connection import ConnectionSpec
from diagnostics import log_event
from errors import BackendStartupError
from platform_support import IS_WINDOWS, PROJECT_ROOT, curl_command, resolve_command, subprocess_options
from subprocess_io import communicate

# A WMI-created process inherits WmiPrvSE's environment, not the caller's, so the
# variables that must survive the hop are passed through a private env file.
DETACHED_ENV_KEYS = ("CODEX_HOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                     "NO_PROXY", "OPENAI_BASE_URL")
_WMI_CREATE = (
    "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
    "-Arguments @{{ CommandLine = '{command}'; CurrentDirectory = '{cwd}' }}; "
    "exit $r.ReturnValue")
WMI_HELPER = PROJECT_ROOT / "windows" / "wmi_launch.py"


async def run(command, *, env, cwd, timeout):
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **subprocess_options())
    stdout, stderr = await communicate(proc, timeout=timeout)
    if proc.returncode:
        # Keep child output in local logs only; never forward arbitrary text or
        # credentials from a user-supplied launcher into WeChat.
        raise BackendStartupError(f"后台准备失败（退出码 {proc.returncode}），请查看本机启动日志。")
    return stdout


def _private_launch_env(env):
    """Write only the variables WMI cannot inherit to a private, short-lived file."""
    values = {}
    for key in DETACHED_ENV_KEYS:
        value = env.get(key)
        if not value:
            continue
        values[key] = value
    directory = Path(tempfile.mkdtemp(prefix="codex-wechat-wmi-"))
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    path = directory / "env.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        try:
            directory.rmdir()
        except OSError:
            pass
        raise
    return path


def wmi_command_line(command, env, *, cwd):
    """Build a shell-free helper invocation; credentials stay out of argv."""
    if not WMI_HELPER.is_file():
        raise BackendStartupError("找不到 Windows 后台启动 helper，不能安全启动共享后台。")
    env_file = _private_launch_env(env)
    line = subprocess.list2cmdline([
        sys.executable,
        str(WMI_HELPER),
        "--env-file", str(env_file),
        "--cwd", str(cwd),
        "--", *command,
    ])
    return line, env_file


def create_process_outside_job(command_line, cwd):
    """Create a process through WMI, which is not inside this process's job.

    Blocks for well under a tenth of a second; the PowerShell equivalent costs
    about a second because it has to start a shell first.
    """
    import pythoncom

    pythoncom.CoInitialize()
    try:
        return _create_via_wmi(command_line, cwd)
    finally:
        pythoncom.CoUninitialize()


def _create_via_wmi(command_line, cwd):
    # A separate scope: COM objects must be released before CoUninitialize.
    import win32com.client

    service = win32com.client.Dispatch("WbemScripting.SWbemLocator").ConnectServer(
        ".", "root\\cimv2")
    params = service.Get("Win32_Process").Methods_("Create").InParameters.SpawnInstance_()
    params.CommandLine = command_line
    if cwd:
        params.CurrentDirectory = cwd
    result = service.ExecMethod("Win32_Process", "Create", params)
    if result.ReturnValue:
        raise BackendStartupError(f"后台宿主拒绝了启动请求（{result.ReturnValue}）")
    return result.ProcessId


class BackendRuntime:
    def __init__(self, config, cwd):
        self.config, self.cwd = config, cwd

    async def ensure_external(self, env):
        startup = ConnectionSpec.from_config(self.config).startup
        if startup:
            await run(resolve_command(list(startup), env), env=env, cwd=self.cwd, timeout=30)

    async def ensure_native(self, command, env, *, is_windows):
        if is_windows:
            start = [*command, "daemon", "start"]
            try:
                await run(start, env=env, cwd=self.cwd, timeout=20)
            except (BackendStartupError, OSError, TimeoutError) as error:
                # A scheduled task runs inside a job that forbids breakaway, and
                # the Codex CLI refuses to detach its daemon from such a launcher.
                # WMI starts it outside that job; the direct reason is still the
                # one worth reporting when that fails too.
                try:
                    await self.start_detached(start, env)
                except (BackendStartupError, OSError, TimeoutError) as detached:
                    log_event("backend.detached_start_failed", level="warning",
                              component="backend", error_type=type(detached).__name__)
                    raise error from None
            return None
        return await self._systemd_start([*command, "daemon", "start"], env, identity=command)

    async def start_detached(self, command, env):
        """Start a command from outside this process's Windows job object."""
        line, env_file = wmi_command_line(command, env, cwd=self.cwd)

        def cleanup_later():
            try:
                env_file.unlink()
            except OSError:
                pass
            try:
                env_file.parent.rmdir()
            except OSError:
                pass

        try:
            await asyncio.to_thread(create_process_outside_job, line, str(self.cwd))
            return
        except Exception:  # pywin32 absent or the COM call refused; keep a slower way in
            log_event("backend.detached_com_unavailable", level="warning",
                      component="backend")
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            cleanup_later()
            raise BackendStartupError("找不到 PowerShell，无法在任务之外启动共享后台。")
        script = _WMI_CREATE.format(command=line.replace("'", "''"),
                                    cwd=str(self.cwd).replace("'", "''"))
        try:
            await run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                      env=env, cwd=self.cwd, timeout=25)
        finally:
            cleanup_later()

    async def _systemd_start(self, command, env, *, identity=None):
        executable = shutil.which(command[0], path=env.get("PATH"))
        if not executable:
            raise BackendStartupError("找不到 Codex 启动器。")
        command = [executable, *command[1:]]
        unit = daemon_control.unit_name([executable, *(identity or command)[1:]], self.cwd, env)
        state = await daemon_control.status(unit)
        if state.get("ActiveState") in ("active", "activating", "reloading"):
            return unit
        if state.get("ActiveState") == "failed":
            code, _ = await daemon_control.systemctl("reset-failed", unit)
            if code:
                raise BackendStartupError("无法清理后台启动失败状态。")
        managed = {"INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "MAINPID",
                   "MANAGERPID", "SYSTEMD_EXEC_PID", "WATCHDOG_PID", "WATCHDOG_USEC"}
        args = ["systemd-run", "--user", "--quiet", "--no-ask-password",
                "--service-type=exec", "--property=ExitType=cgroup", "--property=UMask=0077",
                "--expand-environment=no", "--unit=" + unit, "--working-directory=" + self.cwd]
        args.extend("--setenv=" + key for key in env
                    if key not in managed and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))
        absent = [key for key in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID") if key not in env]
        if absent:
            args.append("--property=UnsetEnvironment=" + " ".join(absent))
        try:
            await run([*args, "--", *command], env=env, cwd=self.cwd, timeout=15)
        except BackendStartupError as error:
            state = await daemon_control.status(unit)
            if state.get("ActiveState") not in ("active", "activating", "reloading"):
                failure = daemon_control.failure(state, command)
                raise (failure or BackendStartupError(
                    "Codex 后台启动失败，请查看用户服务日志。")) from error
        return unit

    async def ensure_websocket(self, command, env, endpoint, root, *, name):
        # Reuse exactly the bridge's local endpoint validation.
        ConnectionSpec.from_config({"websocketUrl": endpoint})
        command = resolve_command(command, env)
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            if len(command) != 1 or Path(command[0]).suffix.lower() != ".exe":
                raise ValueError("Windows 自管共享后台需要原生 exe；自定义启动器可配置独立的 websocketStartupCommand。")
            script = PROJECT_ROOT / "windows" / "Start-SharedBackend.ps1"
            await run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                       "-ExecutionPolicy", "Bypass", "-File", str(script),
                       "-Url", endpoint, "-Executable", command[0], "-RuntimeRoot", str(root)],
                      env=env, cwd=self.cwd, timeout=30)
            return
        unit = await self._systemd_start([*command, "app-server", "--listen", endpoint], env)
        parsed = urlsplit(endpoint)
        health = "http://" + parsed.netloc + "/readyz"
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            state = await daemon_control.status(unit)
            failure = daemon_control.failure(state, command)
            if failure:
                raise failure
            try:
                await run([curl_command(), "--disable", "--noproxy", "*", "--silent", "--fail",
                           "--max-time", "1", "--output", "/dev/null", health],
                          env=env, cwd=self.cwd, timeout=2)
                if state.get("ActiveState") == "active":
                    return
            except (TimeoutError, BackendStartupError):
                pass
            await asyncio.sleep(0.2)
        raise BackendStartupError("共享后台未就绪，请查看用户服务日志。")
