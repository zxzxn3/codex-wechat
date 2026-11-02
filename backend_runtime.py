"""Native backend preparation; never owns or stops an attached shared server."""
import asyncio
import re
import shutil
import daemon_control
from errors import BackendStartupError
from platform_support import resolve_command, subprocess_options

class BackendRuntime:
    def __init__(self, config, cwd):
        self.config, self.cwd = config, cwd

    async def ensure_external(self, env):
        startup = self.config.get("websocketStartupCommand")
        if not startup:
            return
        process = await asyncio.create_subprocess_exec(
            *resolve_command(startup, env), cwd=self.cwd, env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, **subprocess_options())
        try:
            if await asyncio.wait_for(process.wait(), 30):
                raise BackendStartupError("本机共享后台启动失败，请查看共享后台启动日志")
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    async def ensure_native(self, command, env, *, is_windows):
        if is_windows:
            # Native CLI owns daemon discovery and startup. No systemd, shell,
            # network listener, or bridge-owned daemon termination on Windows.
            proc = await asyncio.create_subprocess_exec(
                *command, "daemon", "start", cwd=self.cwd, env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, **subprocess_options())
            try:
                code = await asyncio.wait_for(proc.wait(), 15)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            if code:
                raise RuntimeError("原生 Codex 共享后台启动失败；可配置 sharedDaemon=false 使用独立后端")
            return None
        # Let systemd own the daemon's cgroup, independently of the gateway.
        # Native `daemon start` reuses a running daemon; it never requests a restart.
        executable = shutil.which(command[0], path=env.get("PATH"))
        if not executable:
            raise RuntimeError("找不到 Codex 启动器，无法自动启动共享后台")
        command = [executable, *command[1:]]
        unit = daemon_control.unit_name(command, self.cwd, env)
        state = await daemon_control.status(unit)
        if state.get("ActiveState") in ("active", "activating", "reloading"):
            return unit  # Reuse the existing startup; never stack keyring prompts.
        if state.get("ActiveState") == "failed":
            code, _ = await daemon_control.systemctl("reset-failed", unit)
            if code:
                raise RuntimeError("无法清理消息桥后台启动失败状态")
        managed = {"INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "MAINPID",
                   "MANAGERPID", "SYSTEMD_EXEC_PID", "WATCHDOG_PID", "WATCHDOG_USEC"}
        args = ["systemd-run", "--user", "--quiet", "--no-ask-password",
                "--service-type=exec", "--property=ExitType=cgroup", "--property=UMask=0077",
                "--expand-environment=no", "--unit=" + unit,
                "--working-directory=" + self.cwd]
        # Copy values from the caller's environment, never put secret values in argv.
        args.extend("--setenv=" + key for key in env
                    if key not in managed and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))
        absent = [key for key in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
                  if key not in env]
        if absent:
            args.append("--property=UnsetEnvironment=" + " ".join(absent))
        proc = await asyncio.create_subprocess_exec(
            *args, "--", *command, "daemon", "start", env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            code = await asyncio.wait_for(proc.wait(), 10)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if code:
            # Another client may have started the same native launcher concurrently.
            state = await daemon_control.status(unit)
            if state.get("ActiveState") not in ("active", "activating", "reloading"):
                failure = daemon_control.failure(state, command)
                if failure:
                    raise failure
                raise RuntimeError("无法自动启动共享后台，请检查用户 systemd 和 systemd-run；微信控制仍可用")
        return unit

