"""Cross-platform control of the installed background service.

Authentication and data migration stay in the shared Python layer. This module
only reports service state and requests native start/stop operations.
"""
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from platform_support import INSTALL_ROOT, IS_WINDOWS, subprocess_options
from service_control import UNIT, systemctl
from subprocess_io import communicate

# One wording for "no service registered", shared by every entry point.
NOT_INSTALLED = "尚未注册后台服务，请重新运行安装器。"


@dataclass(frozen=True)
class ServiceState:
    installed: bool
    running: bool
    detail: str = ""


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
    script = INSTALL_ROOT / "codex-wechat.ps1"
    if not script.is_file():
        raise ValueError("找不到本项目的 Windows 服务入口。")
    return await _run([
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script), *command,
    ], timeout=timeout)


async def status():
    if IS_WINDOWS:
        output = await _windows_request(["service-status", "-Json"])
        try:
            value = json.loads(output)
            installed = value["installed"]
            owned = value["owned"]
            running = value["running"]
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Windows 服务状态响应无效。") from None
        if any(type(item) is not bool for item in (installed, owned, running)):
            raise RuntimeError("Windows 服务状态响应无效。")
        if installed and not owned:
            raise ValueError("名为 CodexWeChat 的计划任务不属于当前安装目录。")
        return ServiceState(installed, running)

    output = await systemctl(
        "show", UNIT,
        "--property=LoadState", "--property=ActiveState", "--property=FragmentPath",
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
    return ServiceState(True, active in {"active", "activating", "reloading", "deactivating"})


async def stop_and_wait(timeout=20):
    current = await status()
    if not current.installed or not current.running:
        return current
    if IS_WINDOWS:
        await _windows_request(["service-stop"], timeout=timeout + 5)
        return await status()

    await systemctl("--no-block", "stop", UNIT)
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
    if current.running:
        return current
    if IS_WINDOWS:
        await _windows_request(["service-start"], timeout=20)
    else:
        await systemctl("start", UNIT)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while loop.time() < deadline:
        current = await status()
        if current.running:
            return current
        await asyncio.sleep(0.25)
    raise RuntimeError("后台服务启动请求已提交，但未能确认运行状态")


async def restart():
    current = await status()
    if not current.installed:
        raise ValueError(NOT_INSTALLED)
    if IS_WINDOWS:
        await _windows_request(["service-restart"], timeout=20)
    else:
        await systemctl("--no-block", "restart", UNIT)


async def notify(title, message):
    """Persisted auth state remains authoritative when desktop delivery fails."""
    print(f"{title}: {message}", file=sys.stderr, flush=True)
    from platform_ui import notify as desktop_notify
    return await desktop_notify(title, message)
