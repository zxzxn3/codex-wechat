"""Small, best-effort native UI adapters shared by both entry points."""
from __future__ import annotations

import asyncio
import base64
import importlib
import os
import shutil
import subprocess

from .platform_support import IS_WINDOWS, subprocess_options


NOTIFY_TITLE_ENV = "CODEX_WECHAT_NOTIFY_TITLE_B64"
NOTIFY_BODY_ENV = "CODEX_WECHAT_NOTIFY_BODY_B64"

_WINDOWS_NOTIFY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$title = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String($env:CODEX_WECHAT_NOTIFY_TITLE_B64))
$body = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String($env:CODEX_WECHAT_NOTIFY_BODY_B64))
$notify = New-Object System.Windows.Forms.NotifyIcon
try {
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notify.BalloonTipTitle = $title
    $notify.BalloonTipText = $body
    $notify.Visible = $true
    $notify.ShowBalloonTip(4000)
    Start-Sleep -Milliseconds 4500
}
finally {
    $notify.Visible = $false
    $notify.Dispose()
}
"""


def _show_python_qr(url: str) -> bool:
    try:
        module = importlib.import_module("qrcode")
        code = module.QRCode(border=4)
        code.add_data(url)
        code.print_ascii(invert=True)
    except Exception:
        return False
    return True


def show_qr(url: str) -> bool:
    """Print a QR code when a local encoder exists; otherwise keep the URL."""
    executable = shutil.which("qrencode")
    if executable:
        try:
            subprocess.run(
                [executable, "-t", "ANSIUTF8", "-o", "-"],
                input=url.encode("utf-8"),
                check=True,
                timeout=5,
                **subprocess_options(),
            )
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    return _show_python_qr(url)


async def _run_quiet(command: list[str], *, timeout: float,
                     env: dict[str, str] | None = None) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            **subprocess_options(),
        )
    except (OSError, ValueError):
        return False
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        return False
    except BaseException:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        raise
    return process.returncode == 0


def _powershell() -> str | None:
    for name in ("powershell.exe", "pwsh.exe"):
        executable = shutil.which(name)
        if executable:
            return executable
    return None


def _windows_notification_command(executable: str) -> list[str]:
    encoded = base64.b64encode(
        _WINDOWS_NOTIFY_SCRIPT.encode("utf-16le")
    ).decode("ascii")
    return [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded,
    ]


async def notify(title: str, body: str) -> bool:
    """Show a best-effort desktop notice without delaying the caller."""
    title = str(title)
    body = str(body)
    if IS_WINDOWS:
        executable = _powershell()
        if not executable:
            return False
        env = os.environ.copy()
        env[NOTIFY_TITLE_ENV] = base64.b64encode(
            title.encode("utf-8")
        ).decode("ascii")
        env[NOTIFY_BODY_ENV] = base64.b64encode(
            body.encode("utf-8")
        ).decode("ascii")
        return await _run_quiet(
            _windows_notification_command(executable),
            timeout=7,
            env=env,
        )

    executable = shutil.which("notify-send")
    if not executable:
        return False
    return await _run_quiet(
        [executable, "--app-name=Codex WeChat", "--", title, body],
        timeout=3,
    )
