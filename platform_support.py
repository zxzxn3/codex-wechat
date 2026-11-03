"""Small OS boundaries; Linux keeps its existing paths and defaults."""
import os
from pathlib import Path
import shutil
import subprocess
from urllib.request import getproxies
from native_codex import discover_codex

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


_ACCOUNT_IDENTITY = None


def account_identity() -> str:
    """Name the running account the way icacls needs it, preferring the SID."""
    global _ACCOUNT_IDENTITY
    if _ACCOUNT_IDENTITY:
        return _ACCOUNT_IDENTITY
    identity = None
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
            text=True, timeout=10, **subprocess_options())
        fields = [value.strip().strip('"') for value in result.stdout.strip().split('","')]
        if result.returncode == 0 and fields:
            sid = fields[-1].strip('"')
            if sid.startswith("S-1-"):
                identity = "*" + sid
    except (OSError, subprocess.SubprocessError):
        pass
    if identity is None:
        domain = os.environ.get("USERDOMAIN") or ""
        identity = (domain + "\\" if domain else "") + (os.environ.get("USERNAME") or "")
    _ACCOUNT_IDENTITY = identity
    return identity


def system_proxy():
    """The machine-configured proxy, used to explain a probe, never to route.

    The bridge keeps its own WeChat traffic on the inherited environment. A
    backend started without proxy variables follows the system setting, so the
    `/ping` connection probe mirrors that instead of claiming a direct path.
    """
    try:
        proxies = getproxies()
    except (OSError, ImportError, ValueError):
        return {}
    if not isinstance(proxies, dict):
        return {}
    found = {}
    for scheme in ("http", "https", "all"):
        value = proxies.get(scheme)
        if isinstance(value, str) and value:
            found[scheme.upper() + "_PROXY"] = value
    override = proxies.get("no")
    if isinstance(override, str) and override:
        # The Windows registry separates entries with ';', curl wants ','.
        found["NO_PROXY"] = override.replace(";", ",")
    return found


def make_private(directory) -> None:
    """Create a directory only this account and the system administrators use.

    Windows turns POSIX 0700 into a DACL for the object *owner*, and an
    elevated process owns what it creates through Administrators; the account
    that actually runs the service is then locked out. Name the account
    explicitly instead, on every platform.
    """
    directory = Path(directory)
    if directory.is_dir():
        return
    directory.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        directory.chmod(0o700)
        return
    # Well-known SIDs keep this working on non-English Windows.
    result = subprocess.run(
        ["icacls", str(directory), "/inheritance:r", "/grant:r",
         account_identity() + ":(OI)(CI)F",
         "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
        capture_output=True, text=True, **subprocess_options())
    if result.returncode:
        raise ValueError(f"无法设置目录权限，请用普通终端重试: {directory}")


def resolve_command(command, env):
    """Never pass untrusted argv through cmd.exe or PowerShell interpolation."""
    if (not isinstance(command, (list, tuple)) or not command
            or not all(isinstance(value, str) and value for value in command)):
        raise ValueError("后端 command 必须是非空字符串数组")
    if not IS_WINDOWS:
        if command[0] == "codex" and not shutil.which("codex", path=env.get("PATH")):
            native = discover_codex(env, windows=False)
            if native:
                return [native, *command[1:]]
        return list(command)
    name = command[0]
    executable = shutil.which(name, path=env.get("PATH"))
    # Official standalone installer adds this path for new terminals; the
    # scheduled task may have inherited PATH from before that installation.
    if name.casefold() in ("codex", "codex.exe"):
        executable = discover_codex(env, windows=True) or executable
    if not executable:
        raise RuntimeError("找不到 Codex 启动器，请安装 Codex CLI / App，或配置原生 command 的绝对路径")
    if Path(executable).suffix.casefold() in (".cmd", ".bat", ".ps1"):
        raise ValueError("Windows 后端请使用原生 codex.exe，或在 command 中显式指定解释器与脚本参数；不自动执行批处理")
    return [executable, *command[1:]]


def curl_command():
    return "curl.exe" if IS_WINDOWS else "curl"
