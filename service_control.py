"""Control only the installed per-user service owning this gateway process."""
import asyncio
import hmac
import json
import os
import signal
import sys
from subprocess_io import communicate
from pathlib import Path

UNIT = "codex-wechat.service"
WINDOWS = os.name == "nt"
RESTART_EXIT_CODE = 75
_restart_requested = False


async def systemctl(*args, timeout=5):
    process = await asyncio.create_subprocess_exec(
        "systemctl", "--user", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    output, _ = await communicate(process, timeout=timeout)
    if process.returncode:
        raise RuntimeError("无法控制 systemd 用户服务，请在电脑端查看服务状态。")
    return output.decode().strip()


async def require_service_owner():
    if WINDOWS:
        _require_windows_owner()
        return
    pid = await systemctl("show", UNIT, "--property=MainPID", "--value")
    if pid != str(os.getpid()):
        raise ValueError("当前桥接不是该用户服务的主进程，不能远程重启。"
                         "请退出前台桥接后在电脑执行 codex-wechat service-start。")


def _require_windows_owner():
    status_path = os.environ.get("AGENT_WECHAT_SERVICE_OWNER")
    token = os.environ.get("AGENT_WECHAT_SERVICE_TOKEN")
    if not status_path or not token:
        raise ValueError("当前桥接不是计划任务托管的服务，不能远程重启。")
    try:
        status = json.loads(Path(status_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise ValueError("服务所有者信息不可读，不能远程重启。") from None
    if not isinstance(status, dict):
        raise ValueError("服务所有者信息无效，不能远程重启。")
    recorded_token = str(status.get("token", ""))
    if not hmac.compare_digest(recorded_token, token):
        raise ValueError("服务所有者令牌不匹配，不能远程重启。")
    from windows_service import parent_pid
    current = os.getpid()
    parent = parent_pid(current)
    if status.get("child_pid") == current:
        owned = status.get("supervisor_pid") == parent
    else:
        # Windows venv redirectors remain alive as the supervisor's direct child.
        # Accept only that exact recorded launcher, never arbitrary descendants.
        owned = (status.get("child_pid") == parent and parent is not None
                 and status.get("supervisor_pid") == parent_pid(parent)
                 and _is_venv_launcher(parent))
    if not owned:
        raise ValueError("当前进程不是计划任务 supervisor 的直接子进程，"
                         "不能远程重启。")


def _is_venv_launcher(pid):
    python = Path(sys.executable)
    if python.parent.name.casefold() != "scripts" or not (python.parent.parent / "pyvenv.cfg").is_file():
        return False
    import pywintypes
    import win32api
    import win32con
    import win32process
    process = None
    try:
        process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        return os.path.samefile(win32process.GetModuleFileNameEx(process, 0), python)
    except (OSError, pywintypes.error):
        return False
    finally:
        if process is not None:
            process.Close()


async def restart():
    global _restart_requested
    if WINDOWS:
        # The supervisor starts a fresh child; restarting the ScheduledTask here
        # would deadlock or be ignored while this child is still running.
        _restart_requested = True
        signal.raise_signal(signal.SIGINT)
        return
    # Waiting for our own restart job would deadlock with systemd waiting for us to exit.
    await systemctl("--no-block", "restart", UNIT)


def exit_code():
    """Process exit code consumed by the shared entry point."""
    return RESTART_EXIT_CODE if WINDOWS and _restart_requested else 0
