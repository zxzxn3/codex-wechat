"""Prepare native Codex runtimes; client shutdown never stops a shared backend."""
import asyncio
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import daemon_control
from codex_connection import ConnectionSpec
from errors import BackendStartupError
from platform_support import IS_WINDOWS, PROJECT_ROOT, curl_command, resolve_command, subprocess_options
from subprocess_io import communicate


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


class BackendRuntime:
    def __init__(self, config, cwd):
        self.config, self.cwd = config, cwd

    async def ensure_external(self, env):
        startup = ConnectionSpec.from_config(self.config).startup
        if startup:
            await run(resolve_command(list(startup), env), env=env, cwd=self.cwd, timeout=30)

    async def ensure_native(self, command, env, *, is_windows):
        if is_windows:
            await run([*command, "daemon", "start"], env=env, cwd=self.cwd, timeout=20)
            return None
        return await self._systemd_start([*command, "daemon", "start"], env, identity=command)

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
        except BackendStartupError:
            state = await daemon_control.status(unit)
            if state.get("ActiveState") not in ("active", "activating", "reloading"):
                failure = daemon_control.failure(state, command)
                raise failure or BackendStartupError("Codex 后台启动失败，请查看用户服务日志。")
        return unit

    async def ensure_websocket(self, command, env, endpoint, root, *, name):
        # Reuse exactly the bridge's local endpoint validation.
        ConnectionSpec.from_config({"websocketUrl": endpoint})
        command = resolve_command(command, env)
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if IS_WINDOWS:
            # Adopt the old verified ownership record without stopping its server.
            from platform_support import data_root
            from storage import Store
            old = data_root() / "shared" / "shared-backend-process.json"
            record = root / "shared-backend-process.json"
            if not record.exists() and old.is_file():
                import json
                value = json.loads(old.read_text(encoding="utf-8-sig"))
                if value.get("url") == endpoint:
                    Store(root, lock=False).save("shared-backend-process", value)
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
            except (BackendStartupError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.2)
        raise BackendStartupError("共享后台未就绪，请查看用户服务日志。")
