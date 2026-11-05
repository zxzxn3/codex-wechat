"""Remove the installed launcher and service, preserving user data by default."""
import asyncio
from pathlib import Path
import shutil
import sys

import service_adapter
from platform_support import INSTALL_ROOT, IS_WINDOWS, data_root, subprocess_options
from service_control import UNIT, systemctl

LINUX_DATA_PATHS = ("config.json", ".state", "workspace", "logs", "shared")


def _command_link() -> Path:
    return Path.home() / ".local/bin/codex-wechat"


def _unit_link() -> Path:
    return Path.home() / ".config/systemd/user" / UNIT


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


async def _windows_uninstall(purge: bool) -> None:
    script = INSTALL_ROOT / "uninstall.ps1"
    if not script.is_file():
        raise RuntimeError("找不到 Windows 卸载组件，请重新运行安装器。")
    command = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script),
    ]
    if purge:
        command.append("-PurgeData")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_options(),
    )
    stdout, stderr = await process.communicate()
    if stdout:
        print(stdout.decode("utf-8", "replace").rstrip())
    if stderr:
        print(stderr.decode("utf-8", "replace").rstrip(), file=sys.stderr)
    if process.returncode:
        raise RuntimeError("Windows 卸载未完成。")


async def _linux_service_removal() -> None:
    try:
        current = await service_adapter.status()
    except (OSError, RuntimeError):
        return
    except ValueError:
        print("检测到不属于本安装的后台服务，未改动该服务。")
        return
    if not current.installed:
        return
    if current.running:
        await service_adapter.stop_and_wait()
    try:
        await systemctl("disable", UNIT)
    except (OSError, RuntimeError):
        # The unit may only have been linked for on-demand starts.
        pass
    link = _unit_link()
    expected = (INSTALL_ROOT / UNIT).resolve()
    try:
        owns_link = link.is_symlink() and link.resolve() == expected
    except OSError:
        owns_link = False
    if owns_link:
        link.unlink()
    try:
        await systemctl("daemon-reload")
    except (OSError, RuntimeError):
        pass
    print("后台服务已停止并移除注册。")


def _linux_command_removal() -> None:
    link = _command_link()
    expected = (INSTALL_ROOT / "codex-wechat").resolve()
    try:
        owns_link = link.is_symlink() and link.resolve() == expected
    except OSError:
        owns_link = False
    if owns_link:
        link.unlink()
        print(f"已移除命令入口: {link}")
    elif link.exists():
        print(f"保留不是本安装创建的命令入口: {link}")


def _purge_linux_data() -> None:
    root = data_root()
    install = INSTALL_ROOT.resolve()
    if root == install:
        for name in LINUX_DATA_PATHS:
            _remove_path(root / name)
        print("已删除配置、登录、待办、工作目录、日志和共享后台数据。")
        return
    protected = {Path(root.anchor), Path.home().resolve()}
    if root in protected or Path.home().resolve().is_relative_to(root):
        raise ValueError(f"拒绝删除范围过大的数据目录: {root}")
    if root.is_symlink():
        raise ValueError(f"拒绝删除符号链接数据目录: {root}")
    if root.exists():
        shutil.rmtree(root)
    print(f"已删除数据目录: {root}")


async def uninstall(*, purge: bool = False) -> None:
    if IS_WINDOWS:
        await _windows_uninstall(purge)
        return
    await _linux_service_removal()
    _linux_command_removal()
    if purge:
        _purge_linux_data()
    else:
        print(f"用户数据已保留: {data_root()}")
        print("如需同时删除配置、登录和待办，请使用 --purge-data。")
