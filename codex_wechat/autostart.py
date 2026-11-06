"""Login autostart is native OS state, independent of process crash recovery."""
import asyncio
import json
from pathlib import Path
import sys

from .diagnostics import log_event
from .platform_support import INSTALL_ROOT, IS_WINDOWS, subprocess_options
from .service_control import UNIT, systemctl

WINDOWS_TASK = "CodexWeChat"
SUPERVISOR_SCRIPT = "bootstrap_supervisor.py"
TASK_TRIGGER_LOGON = 9


async def _windows_request(mode):
    return (await _windows_json(mode))["enabled"]


async def _windows_status():
    value = await _windows_json("status")
    return bool(value["installed"]), bool(value["enabled"])


async def _windows_json(mode):
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(INSTALL_ROOT / "windows/task-service.ps1"), "autostart"]
    if mode != "status":
        command.append(mode)
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, **subprocess_options())
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 8)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise ValueError("无法管理本项目的登录自启，请先安装服务或在电脑检查计划任务。")
    try:
        value = json.loads(output.decode("utf-8-sig"))
        enabled = value["enabled"]
        installed = value.get("installed", True)
        if type(enabled) is not bool or type(installed) is not bool:
            raise ValueError()
        return {"installed": installed, "enabled": enabled}
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise RuntimeError("自启状态响应无效。") from None


async def installed():
    if IS_WINDOWS:
        try:
            installed_flag, owned, _enabled = await asyncio.to_thread(windows_task_flags)
            return installed_flag and owned
        except Exception:  # no scheduler API or a foreign task: ask the script
            return (await _windows_status())[0]
    return await _linux_status() is not None


def windows_task_flags():
    """(installed, owned, enabled) straight from the task definition.

    Asking the launcher script costs about three seconds because it starts
    PowerShell; the scheduler's own COM API answers in about forty milliseconds.
    The ownership check stays conservative: a foreign task is distinguished from
    a missing one, so status can fail instead of silently reporting disabled.
    """
    import pythoncom

    pythoncom.CoInitialize()
    try:
        return _read_task_flags()
    finally:
        pythoncom.CoUninitialize()


def _exec_action(action):
    """`Actions.Item()` hands back an IAction; only IExecAction has the details.

    With a generated gen_py cache present the IAction wrapper is typed and
    `Path`/`Arguments` do not exist on it at all, so the members have to be read
    through the IExecAction interface. Without the cache a dynamic wrapper hides
    that difference, which is why this only ever breaks on a warm install.
    """
    import win32com.client
    try:
        return win32com.client.CastTo(action, "IExecAction")
    except Exception:  # not a COM object (tests) or the cast was refused
        return action


def _owned_supervisor_action(action):
    """Match the exact stable supervisor action installed by this project."""
    action = _exec_action(action)
    root = Path(INSTALL_ROOT)
    expected = f'"{root / SUPERVISOR_SCRIPT}" supervise'
    arguments = str(getattr(action, "Arguments", "") or "")
    if not arguments.casefold().startswith(expected.casefold()):
        return False
    if (len(arguments) > len(expected)
            and not arguments[len(expected)].isspace()):
        return False
    executables = {
        str(root / ".bootstrap-venv/Scripts/pythonw.exe").casefold(),
        str(root / ".bootstrap-venv/Scripts/python.exe").casefold(),
    }
    return str(getattr(action, "Path", "") or "").casefold() in executables


def _read_task_flags():
    import win32com.client

    service = win32com.client.Dispatch("Schedule.Service")
    service.Connect()
    try:
        task = service.GetFolder("\\").GetTask(WINDOWS_TASK)
    except Exception:
        return False, False, False
    definition = task.Definition
    actions = definition.Actions
    if actions.Count != 1 or not _owned_supervisor_action(actions.Item(1)):
        return True, False, False
    triggers = [definition.Triggers.Item(index) for index in range(1, definition.Triggers.Count + 1)]
    logon = [trigger for trigger in triggers if trigger.Type == TASK_TRIGGER_LOGON]
    enabled = bool(task.Enabled) and any(bool(trigger.Enabled) for trigger in logon)
    return True, True, enabled


async def _linux_status():
    value = await systemctl("show", UNIT, "--property=FragmentPath", "--property=UnitFileState",
                           "--property=LoadState")
    fields = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
    fragment = fields.get("FragmentPath")
    if fields.get("LoadState") == "not-found" and not fragment:
        return None
    expected = (INSTALL_ROOT / UNIT).resolve()
    if not fragment or Path(fragment).resolve() != expected:
        raise ValueError("尚未安装本项目的用户服务，或该服务属于另一个目录。")
    return fields.get("UnitFileState") == "enabled"


async def status():
    if not IS_WINDOWS:
        return bool(await _linux_status())
    try:
        installed_flag, owned, enabled = await asyncio.to_thread(windows_task_flags)
    except Exception:
        # No scheduler API here: the launcher script is slower but authoritative.
        log_event("autostart.task_read_failed", level="warning", component="autostart")
        return (await _windows_status())[1]
    if not installed_flag:
        return False
    if not owned:
        raise ValueError("尚未安装本项目的计划任务，或该任务属于另一个目录。")
    return enabled


async def set_enabled(enabled):
    if type(enabled) is not bool:
        raise ValueError("自启只接受 true 或 false。")
    if IS_WINDOWS:
        actual = await _windows_request("true" if enabled else "false")
    else:
        installed = await _linux_status()
        if installed is None and not enabled:
            return False
        if installed is None:
            raise ValueError("尚未注册本项目的用户服务，请重新运行安装器。")
        source = str((INSTALL_ROOT / UNIT).resolve())
        if not Path(source).is_file():
            raise ValueError("本项目的服务模板不存在。")
        if enabled:
            await systemctl("enable", UNIT)
        else:
            await systemctl("disable", UNIT)
            # disable also removes explicit unit links. Keep demand-start usable.
            await systemctl("link", source)
            await systemctl("daemon-reload")
        actual = await _linux_status()
    if actual != enabled:
        raise RuntimeError("自启设置未生效，请在电脑检查用户服务。")
    return actual


async def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("status", "true", "false"):
        raise ValueError("用法: autostart.py status|true|false")
    mode = sys.argv[1]
    value = await status() if mode == "status" else await set_enabled(mode == "true")
    print("true" if value else "false")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (TimeoutError, ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
