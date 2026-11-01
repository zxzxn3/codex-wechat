"""Login autostart is native OS state, independent of process crash recovery."""
import asyncio
import json
from pathlib import Path
import sys

from platform_support import IS_WINDOWS, PROJECT_ROOT, subprocess_options
from service_control import UNIT, systemctl


async def _windows_request(mode):
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(PROJECT_ROOT / "codex-wechat.ps1"), "autostart"]
    if mode != "status":
        command.append(mode)
    command.append("-Json")
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
        if type(enabled) is not bool:
            raise ValueError()
        return enabled
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise RuntimeError("自启状态响应无效。") from None


async def installed():
    if IS_WINDOWS:
        await _windows_request("status")
        return True
    return await _linux_status() is not None


async def _linux_status():
    value = await systemctl("show", UNIT, "--property=FragmentPath", "--property=UnitFileState",
                           "--property=LoadState")
    fields = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
    fragment = fields.get("FragmentPath")
    if fields.get("LoadState") == "not-found" and not fragment:
        return None
    expected = (PROJECT_ROOT / UNIT).resolve()
    if not fragment or Path(fragment).resolve() != expected:
        raise ValueError("尚未安装本项目的用户服务，或该服务属于另一个目录。")
    return fields.get("UnitFileState") == "enabled"


async def status():
    return await _windows_request("status") if IS_WINDOWS else bool(await _linux_status())


async def set_enabled(enabled, *, start_if_missing=True):
    if type(enabled) is not bool:
        raise ValueError("自启只接受 on 或 off。")
    if IS_WINDOWS:
        actual = await _windows_request("on" if enabled else "off")
    else:
        previous = await _linux_status()  # Never modify a different installation's service.
        if previous is None and not enabled:
            return False
        if (previous is None and enabled and PROJECT_ROOT.resolve() !=
                (Path.home() / ".local/share/codex-wechat").resolve()):
            raise ValueError("首次注册 Linux 服务要求项目位于 ~/.local/share/codex-wechat。")
        source = str((PROJECT_ROOT / UNIT).resolve())
        if not Path(source).is_file():
            raise ValueError("本项目的服务模板不存在。")
        if enabled:
            await systemctl("enable", source)
        else:
            await systemctl("disable", UNIT)
            # disable also removes explicit unit links. Keep demand-start usable.
            await systemctl("link", source)
            await systemctl("daemon-reload")
        actual = await _linux_status()
    if actual != enabled:
        raise RuntimeError("自启设置未生效，请在电脑检查用户服务。")
    if not IS_WINDOWS and previous is None and enabled and start_if_missing:
        await systemctl("--no-block", "start", UNIT)
    return actual


async def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("status", "on", "off"):
        raise ValueError("用法: autostart.py status|on|off")
    mode = sys.argv[1]
    value = await status() if mode == "status" else await set_enabled(mode == "on")
    print("on" if value else "off")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
