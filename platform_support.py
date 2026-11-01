"""Small OS boundaries; Linux keeps its existing paths and defaults."""
import os
from pathlib import Path
import shutil
import subprocess

IS_WINDOWS = os.name == "nt"
PROJECT_ROOT = Path(__file__).resolve().parent
INSTALL_ROOT = Path(
    os.environ.get("CODEX_WECHAT_INSTALL_ROOT", str(PROJECT_ROOT))
).expanduser().resolve()


def data_root():
    override = os.environ.get("CODEX_WECHAT_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("Windows 缺少 LOCALAPPDATA，无法定位当前用户数据目录")
        return Path(local) / "codex-wechat"
    return PROJECT_ROOT


def subprocess_options():
    # Suppress a console flash for every poll/upload/proxy in the background.
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}


def resolve_command(command, env):
    """Never pass untrusted argv through cmd.exe or PowerShell interpolation."""
    if (not isinstance(command, (list, tuple)) or not command
            or not all(isinstance(value, str) and value for value in command)):
        raise ValueError("后端 command 必须是非空字符串数组")
    if not IS_WINDOWS:
        return list(command)
    name = command[0]
    executable = shutil.which(name, path=env.get("PATH"))
    # Official standalone installer adds this path for new terminals; the
    # scheduled task may have inherited PATH from before that installation.
    if name.casefold() in ("codex", "codex.exe"):
        native = shutil.which("codex.exe", path=env.get("PATH"))
        local = env.get("LOCALAPPDATA")
        installed = (Path(local) / "Programs/OpenAI/Codex/bin/codex.exe") if local else None
        executable = native or (str(installed) if installed and installed.is_file() else executable)
    if not executable:
        raise RuntimeError("找不到 Codex 启动器，请安装原生 Codex CLI 或配置 command 的绝对路径")
    if Path(executable).suffix.casefold() in (".cmd", ".bat", ".ps1"):
        raise ValueError("Windows 后端请使用原生 codex.exe，或在 command 中显式指定解释器与脚本参数；不自动执行批处理")
    return [executable, *command[1:]]


def curl_command():
    return "curl.exe" if IS_WINDOWS else "curl"
