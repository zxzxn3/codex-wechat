"""Small OS boundaries; Linux keeps its existing paths and defaults."""
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit
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


PROXY_ENV_KEYS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                  "HTTP_PROXY", "http_proxy")


def proxy_label(value):
    """Only host:port, so a credential in the setting never reaches the log."""
    try:
        parts = urlsplit(str(value))
        port = parts.port
    except ValueError:
        return ""
    host = parts.hostname or ""
    if not host:
        return ""
    return f"{host}:{port}" if port else host


def local_endpoint(url):
    """True for a model base URL that stays on this machine or the intranet."""
    try:
        host = urlsplit(str(url)).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.casefold() == "localhost" or host.endswith(".local") or "." not in host
    return address.is_loopback or address.is_private or address.is_link_local


def apply_system_proxy(env):
    """Name the machine proxy in the environment a backend starts with.

    Codex sends the first sampling request of every turn over a WebSocket and
    only falls back to HTTP after the retries run out. Its HTTP client already
    follows the machine proxy, but the WebSocket connector reads the
    environment alone, so on a proxied machine the first turn pays one connect
    timeout per retry before anything reaches the model. Naming the proxy here
    makes both paths use it; an environment that already carries a proxy wins.
    Returns what it added, so the caller can record it.
    """
    if any(env.get(key) for key in PROXY_ENV_KEYS):
        return {}
    base = env.get("OPENAI_BASE_URL") or env.get("openai_base_url")
    if base and local_endpoint(base):
        # A local model gateway never reaches the internet, and naming a proxy
        # would divert it; leave that backend exactly as its owner set it up.
        return {}
    found = system_proxy()
    proxy = found.get("HTTPS_PROXY") or found.get("ALL_PROXY") or found.get("HTTP_PROXY")
    if not proxy:
        return {}
    env["HTTPS_PROXY"] = proxy
    env["HTTP_PROXY"] = found.get("HTTP_PROXY") or proxy
    env.setdefault("NO_PROXY", found.get("NO_PROXY") or "localhost,127.0.0.1,::1")
    return {"HTTPS_PROXY": proxy, "HTTP_PROXY": env["HTTP_PROXY"],
            "NO_PROXY": env["NO_PROXY"]}


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
