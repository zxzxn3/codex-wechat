"""One dependency declaration for installation and native capability selection."""
from pathlib import Path

PYTHON_REQUIREMENTS = Path("requirements.txt")
QR_FALLBACK_REQUIREMENTS = Path("requirements-qr.txt")


def tools(windows):
    return (
        ("Codex runtime", "codex.exe" if windows else "codex", None, "微信模型任务", "warn"),
        ("curl", "curl.exe" if windows else "curl", (8, 4), "微信登录与收发", "warn"),
        ("qrencode", "qrencode", None, "原生终端二维码（缺少时使用轻量回退）", "warn"),
        ("systemd-run", "systemd-run", (254,), "Linux 共享后台", "warn"),
    )
