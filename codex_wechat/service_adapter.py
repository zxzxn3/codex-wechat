"""Cross-platform control of the installed background service.

Authentication and durable state transitions stay in the shared Python layer. This module
only reports service state and requests native start/stop operations.
"""
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .platform_support import INSTALL_ROOT, IS_WINDOWS, subprocess_options
from .service_control import UNIT, systemctl
from .subprocess_io import communicate

# One wording for "no service registered", shared by every entry point.
NOT_INSTALLED = "尚未注册后台服务，请重新运行安装器。"
STABLE_SECONDS = 2.0
STABLE_STATES = {"", "active", "running"}
STARTING_STATES = {"activating", "queued", "reloading"}
STOPPING_STATES = {"deactivating"}
FAILED_STATES = {"failed"}
STOP_TIMEOUT_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class ServiceState:
    installed: bool
    running: bool
    detail: str = ""
    # Native host state and last exit code, when the platform exposes them.
    state: str = ""
    last_result: int | None = None
    # Effective Linux force-stop timeout, used to observe the post-kill state.
    stop_timeout: float | None = None
    # The registered unit path reported by systemd; absence means no owned unit.
    fragment_path: Path | None = None

    @property
    def stable_running(self):
        return self.running and self.state.casefold() in STABLE_STATES

    @property
    def phase(self):
        """Stable lifecycle phases shared by start/restart decision points."""
        if not self.installed:
            return "not-installed"
        current = self.state.casefold()
        if current in STOPPING_STATES:
            return "stopping"
        if self.stable_running:
            return "running"
        if self.running or current in STARTING_STATES:
            return "starting"
        if current in FAILED_STATES:
            return "failed"
        return "stopped"


async def _run(command, *, timeout):
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_options(),
    )
    stdout, stderr = await communicate(process, timeout=timeout)
    if process.returncode:
        detail = stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or "本项目的后台服务操作失败")
    return stdout.decode("utf-8-sig", "replace").strip()


async def _windows_request(command, *, timeout=20):
    script = INSTALL_ROOT / "windows/task-service.ps1"
    if not script.is_file():
        raise ValueError("找不到本项目的 Windows 服务组件。")
    return await _run([
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script), *command,
    ], timeout=timeout)


def parse_host_json(text: str) -> ServiceState:
    """Validate the host payload the Windows entry point prints or forwards."""
    try:
        value = json.loads(text)
        installed = value["installed"]
        owned = value["owned"]
        running = value["running"]
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Windows 服务状态响应无效。") from None
    if any(type(item) is not bool for item in (installed, owned, running)):
        raise RuntimeError("Windows 服务状态响应无效。")
    if installed and not owned:
        raise ValueError("名为 CodexWeChat 的计划任务不属于当前安装目录。")
    last_result = value.get("last_result")
    if type(last_result) is not int:
        last_result = None
    return ServiceState(installed, running, state=str(value.get("state") or ""),
                        last_result=last_result)


async def status():
    if IS_WINDOWS:
        return parse_host_json(await _windows_request(["status"]))

    output = await systemctl(
        "show", UNIT,
        "--property=LoadState", "--property=ActiveState", "--property=FragmentPath",
        "--property=ExecMainStatus", "--property=TimeoutStopUSec",
    )
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    fragment = fields.get("FragmentPath", "")
    if fields.get("LoadState") == "not-found" and not fragment:
        return ServiceState(False, False)
    expected = (INSTALL_ROOT / UNIT).resolve()
    try:
        owned = bool(fragment) and Path(fragment).resolve() == expected
    except OSError:
        owned = False
    if not owned:
        raise ValueError("当前用户服务不属于这个安装目录，拒绝自动停止或启动。")
    active = fields.get("ActiveState", "")
    # ExecMainStatus is the main process exit status; empty until it has run once.
    exit_code = fields.get("ExecMainStatus", "").strip()
    return ServiceState(True, active in {"active", "activating", "reloading", "deactivating"},
                        state=active,
                        last_result=int(exit_code) if exit_code.isdigit() else None,
                        stop_timeout=_duration_seconds(fields.get("TimeoutStopUSec", "")),
                        fragment_path=Path(fragment))


def _duration_seconds(value):
    """Parse systemd's TimeoutStopUSec property without depending on locale."""
    text = value.strip()
    if not text or text == "infinity":
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text) / 1_000_000
    units = {"us": 0.000001, "ms": 0.001, "s": 1.0,
             "min": 60.0, "h": 3600.0, "d": 86400.0}
    total = 0.0
    end = 0
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(us|ms|min|s|h|d)", text):
        if text[end:match.start()].strip():
            return None
        total += float(match.group(1)) * units[match.group(2)]
        end = match.end()
    if end == 0 or text[end:].strip():
        return None
    return total


async def stop_and_wait(timeout=20):
    current = await status()
    if not current.installed or not current.running:
        return current
    await _request_stop(timeout=timeout)
    wait_timeout = timeout
    if not IS_WINDOWS:
        # systemd kills at its effective stop timeout. Observe the resulting
        # inactive/failed state after that boundary instead of racing it.
        wait_timeout = max(
            timeout,
            (current.stop_timeout or timeout) + STOP_TIMEOUT_GRACE_SECONDS,
        )
    return await _wait_stopped(timeout=wait_timeout)


async def _request_stop(*, timeout=20):
    if IS_WINDOWS:
        await _windows_request(["stop"], timeout=timeout + 5)
    else:
        await systemctl("--no-block", "stop", UNIT)


async def _request_start(*, timeout=20):
    if IS_WINDOWS:
        await _windows_request(["start"], timeout=timeout)
    else:
        await systemctl("start", UNIT)


async def _wait_stopped(timeout):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        current = await status()
        if not current.running:
            return current
        await asyncio.sleep(0.25)
    raise RuntimeError(f"后台服务未能在 {timeout} 秒内停止")


async def start():
    current = await status()
    if not current.installed:
        raise ValueError(NOT_INSTALLED)
    if current.phase == "running":
        return current
    if current.phase == "stopping":
        await _wait_stopped(timeout=20)
        await _request_start(timeout=20)
    elif current.phase in {"stopped", "failed"}:
        await _request_start(timeout=20)
    return await _wait_running_stable(timeout=20, settle=STABLE_SECONDS)


async def restart():
    current = await status()
    if not current.installed:
        raise ValueError(NOT_INSTALLED)
    if IS_WINDOWS:
        if current.running:
            await _request_stop(timeout=20)
            await _wait_stopped(timeout=20)
        await _request_start(timeout=20)
    else:
        await systemctl("restart", UNIT, timeout=30)
    return await _wait_running_stable(timeout=20, settle=STABLE_SECONDS)


async def _wait_running_stable(*, timeout, settle):
    """Wait out a crash-on-start window instead of trusting one Running sample."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    running_since = None
    while True:
        current = await status()
        stable_state = current.stable_running
        if stable_state:
            if settle <= 0:
                return current
            if running_since is None:
                running_since = loop.time()
            elif loop.time() - running_since >= settle:
                return current
        else:
            if running_since is not None:
                raise RuntimeError(_unstable_message(current))
            running_since = None
        if loop.time() >= deadline:
            raise RuntimeError(
                "后台服务启动请求已提交，但未能稳定运行"
                f"（state={current.state or 'unknown'}）；{_log_hint()}")
        await asyncio.sleep(0.25)


def _unstable_message(current):
    result = (f" last_result={current.last_result}"
              if current.last_result is not None else "")
    return (f"后台服务启动后立即退出（state={current.state or 'unknown'}{result}）；"
            f"{_log_hint()}")


def _log_hint():
    return ("请运行 codex-wechat logs -Service 查看服务日志。"
            if IS_WINDOWS else "请运行 codex-wechat logs --service 查看服务日志。")


async def notify(title, message):
    """Persisted auth state remains authoritative when desktop delivery fails."""
    print(f"{title}: {message}", file=sys.stderr, flush=True)
    from .platform_ui import notify as desktop_notify
    return await desktop_notify(title, message)
