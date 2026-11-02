"""Native executable discovery; no model calls, credentials or user history."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess

APP_DESKTOP_ID = "chatgpt.desktop"
MIN_APP_DAEMON_VERSION = (0, 141, 0)


def supports_app_daemon(version):
    """Minimum accepted by the currently supported App's local-daemon path."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version) if isinstance(version, str) else None
    if not match:
        return False
    core = tuple(int(part) for part in match.groups()[:3])
    return core > MIN_APP_DAEMON_VERSION or (core == MIN_APP_DAEMON_VERSION and not match[4])


def discover_app_codex(env=None, *, windows=None):
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    if not windows:
        bundled = Path("/usr/lib/chatgpt/resources/codex")
        return str(bundled) if bundled.is_file() else None
    if not env.get("LOCALAPPDATA"):
        return None
    # Query the registered App, never guess a hashed package version or choose
    # the newest file in an arbitrary cache. This query does not launch the App.
    script = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
              "$p=Get-AppxPackage OpenAI.Codex | Select-Object -First 1; "
              "if($p){ConvertTo-Json -Compress -InputObject "
              "(Join-Path $p.InstallLocation 'app\\resources\\codex.exe')}")
    try:
        result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-Command", script], capture_output=True, timeout=5, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        path = json.loads(result.stdout.decode("utf-8-sig")) if result.returncode == 0 else None
        if isinstance(path, str) and Path(path).is_file() and Path(path).suffix.lower() == ".exe":
            return path
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def discover_codex(env=None, *, windows=None):
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    native = shutil.which("codex.exe" if windows else "codex", path=env.get("PATH"))
    if native and (not windows or Path(native).suffix.lower() == ".exe"):
        return native
    local = env.get("LOCALAPPDATA")
    standalone = Path(local) / "Programs/OpenAI/Codex/bin/codex.exe" if windows and local else None
    if standalone and standalone.is_file():
        return str(standalone)
    return discover_app_codex(env, windows=windows)


def discover_app(env=None, *, windows=None):
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    if windows:
        local = env.get("LOCALAPPDATA")
        alias = Path(local) / "Microsoft/WindowsApps/chatgpt-desktop.exe" if local else None
        return [str(alias)] if alias and alias.is_file() else None
    app = shutil.which("chatgpt", path=env.get("PATH"))
    return [app] if app else None
