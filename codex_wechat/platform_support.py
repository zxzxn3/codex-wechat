"""Small OS boundaries with per-platform configuration and state roots."""
import ast
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import getproxies

from .install_layout import (InstalledLayout, RuntimePaths, default_roots,
                             runtime_paths_for, validate_roots)
from .native_codex import discover_codex

IS_WINDOWS = os.name == "nt"
ALLOW_ROOT_ENV = "CODEX_WECHAT_ALLOW_ROOT"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL_ROOT = Path(
    os.environ.get("CODEX_WECHAT_INSTALL_ROOT", str(PROJECT_ROOT))
).expanduser().resolve()


def _platform() -> str:
    return "windows" if IS_WINDOWS else "linux"


def runtime_paths(install_root: Path | None = None) -> RuntimePaths:
    """Resolve the installed contract, or validated source-tree defaults."""
    root = INSTALL_ROOT if install_root is None else Path(install_root)
    layout = InstalledLayout.load(root, platform=_platform())
    if layout is not None:
        return layout.runtime_paths()
    data, config, state = default_roots(_platform())
    validate_roots(_platform(), root, data, config, state)
    return runtime_paths_for(_platform(), data, config, state)


def subprocess_options():
    # Suppress a console flash for every poll/upload/proxy in the background.
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}


def is_elevated() -> bool:
    """True for an administrator token or a POSIX root process."""
    if not IS_WINDOWS:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False
    try:
        import ctypes
        windll = getattr(ctypes, "windll", None)
        return bool(windll.shell32.IsUserAnAdmin()) if windll is not None else False
    except Exception:  # a locked-down shell may refuse the call itself
        return False


def root_override_enabled() -> bool:
    """The documented escape hatch for containers and disposable CI hosts."""
    return os.environ.get(ALLOW_ROOT_ENV) == "1"


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
            text=True, encoding="utf-8", errors="replace", timeout=10,
            **subprocess_options())
        fields = [value.strip().strip('"') for value in result.stdout.strip().split('","')]
        if result.returncode == 0 and fields:
            sid = fields[-1].strip('"')
            if sid.startswith("S-1-"):
                identity = "*" + sid
    except (OSError, subprocess.SubprocessError, ValueError):
        # Native tools print in the OEM code page, which need not be UTF-8.
        identity = None
    if identity is None:
        domain = os.environ.get("USERDOMAIN") or ""
        identity = (domain + "\\" if domain else "") + (os.environ.get("USERNAME") or "")
    _ACCOUNT_IDENTITY = identity
    return identity


def _gsettings(schema, key):
    """Read one GVariant value without passing it through a shell."""
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=2, **subprocess_options())
    if result.returncode:
        raise ValueError("gsettings failed")
    text = result.stdout.strip()
    if text.startswith("@as "):
        text = text[4:].lstrip()
    if text in ("true", "false"):
        return text == "true"
    return ast.literal_eval(text)


def _proxy_url(scheme, host, port):
    if not isinstance(host, str) or not host.strip():
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    host = host.strip().strip("[]")
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def _gnome_proxy():
    """Read GNOME's manual proxy settings; PAC/automatic mode is unsupported."""
    if not sys.platform.startswith("linux"):
        return {}
    try:
        if _gsettings("org.gnome.system.proxy", "mode") != "manual":
            return {}
        same = _gsettings("org.gnome.system.proxy", "use-same-proxy") is True
        http = (_gsettings("org.gnome.system.proxy.http", "host"),
                _gsettings("org.gnome.system.proxy.http", "port"))
        https = http if same else (
            _gsettings("org.gnome.system.proxy.https", "host"),
            _gsettings("org.gnome.system.proxy.https", "port"))
        socks = (_gsettings("org.gnome.system.proxy.socks", "host"),
                 _gsettings("org.gnome.system.proxy.socks", "port"))
        ignore = _gsettings("org.gnome.system.proxy", "ignore-hosts")
    except (OSError, subprocess.SubprocessError, SyntaxError, TypeError, ValueError):
        return {}
    found = {}
    http_url = _proxy_url("http", *http)
    https_url = _proxy_url("http", *https)
    socks_url = _proxy_url("socks5h", *socks)
    if http_url:
        found["HTTP_PROXY"] = http_url
    if https_url:
        found["HTTPS_PROXY"] = https_url
    if not (http_url or https_url) and socks_url:
        found["ALL_PROXY"] = socks_url
    if isinstance(ignore, list):
        entries = [entry for entry in ignore if isinstance(entry, str) and entry]
        if entries:
            found["NO_PROXY"] = ",".join(entries)
    return found


def system_proxy():
    """The machine-configured proxy used for a backend and connection probe.

    Environment settings win. Linux's urllib helper only reads proxy
    environment variables, so GNOME manual settings are read as a fallback;
    PAC/automatic proxy modes are intentionally not guessed.
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
    if any(key in found for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")):
        return found
    gnome = _gnome_proxy()
    if gnome:
        if found.get("NO_PROXY") and not gnome.get("NO_PROXY"):
            gnome["NO_PROXY"] = found["NO_PROXY"]
        return gnome
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
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def endpoint_host(value):
    """Host of a configured URL, tolerating a missing scheme."""
    text = str(value).strip()
    try:
        host = urlsplit(text).hostname
        if host is None and "://" not in text and " " not in text:
            host = urlsplit("//" + text).hostname
    except ValueError:
        return ""
    return host or ""


def local_endpoint(url):
    """True for a model base URL that stays on this machine or the intranet."""
    host = endpoint_host(url)
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.casefold() == "localhost" or host.endswith(".local") or "." not in host
    return address.is_loopback or address.is_private or address.is_link_local


def apply_system_proxy(env, *, enabled=True):
    """Name the machine proxy in the environment a backend starts with.

    Codex sends the first sampling request of every turn over a WebSocket and
    only falls back to HTTP after the retries run out. Its HTTP client already
    follows the machine proxy, but the WebSocket connector reads the
    environment alone, so on a proxied machine the first turn pays one connect
    timeout per retry before anything reaches the model. Naming the proxy here
    makes both paths use it; an environment that already carries a proxy wins.
    Returns what it added, so the caller can record it.
    """
    if not enabled:
        return {}
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
    # An empty value is not a decision; a real one must survive.
    env["NO_PROXY"] = (env.get("NO_PROXY") or found.get("NO_PROXY")
                       or "localhost,127.0.0.1,::1")
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
    directory.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        directory.chmod(0o700)
        return
    # Well-known SIDs keep this working on non-English Windows.
    result = subprocess.run(
        ["icacls", str(directory), "/inheritance:r", "/grant:r",
         account_identity() + ":(OI)(CI)F",
         "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        **subprocess_options())
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
