"""Remove the installed launcher and service, preserving user data by default."""
import asyncio
import os
from pathlib import Path
import shutil
import sys

import service_adapter
from platform_support import INSTALL_ROOT, IS_WINDOWS, data_root, subprocess_options
from service_control import UNIT, systemctl
from storage import Store

LINUX_DATA_PATHS = ("config.json", ".state", "workspace", "logs", "shared")
UNINSTALL_TIMEOUT_SECONDS = 60


def _command_link() -> Path:
    return Path.home() / ".local/bin/codex-wechat"


def _unit_link() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config.expanduser() / "systemd/user" / UNIT


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


async def _windows_uninstall(purge: bool) -> None:
    try:
        current = await service_adapter.status()
    except FileNotFoundError:
        # No native service manager is available; the stable uninstaller can
        # still remove a task/shim after the foreground-lock check below.
        current = None
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"无法确认后台服务状态，拒绝卸载: {error}") from error
    if current is not None and current.installed and current.running:
        await service_adapter.stop_and_wait()
    _require_no_bridge()
    script = INSTALL_ROOT / "uninstall.ps1"
    if not script.is_file():
        raise RuntimeError("找不到 Windows 卸载组件，请重新运行安装器。")
    command = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-DataRoot", str(data_root()),
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
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=UNINSTALL_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Windows 卸载组件超时。") from None
    if stdout:
        print(stdout.decode("utf-8", "replace").rstrip())
    if stderr:
        print(stderr.decode("utf-8", "replace").rstrip(), file=sys.stderr)
    if process.returncode:
        raise RuntimeError("Windows 卸载未完成。")


async def _linux_service_removal() -> None:
    try:
        current = await service_adapter.status()
    except FileNotFoundError:
        # No systemd client is installed; there cannot be a registered unit.
        current = None
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"无法确认后台服务状态，拒绝卸载: {error}") from error
    except ValueError:
        print("检测到不属于本安装的后台服务，未改动该服务。")
        return
    if current is None or not current.installed:
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
    if install.is_relative_to(root):
        raise ValueError(f"数据目录不能包含安装目录: {root}")
    protected = {Path(root.anchor), Path.home().resolve()}
    if root in protected or Path.home().resolve().is_relative_to(root):
        raise ValueError(f"拒绝删除范围过大的数据目录: {root}")
    if not root.exists():
        print(f"数据目录不存在: {root}")
        return
    if not root.is_dir():
        raise ValueError(f"数据目录不是目录: {root}")
    for name in LINUX_DATA_PATHS:
        _remove_path(root / name)
    print(f"已删除数据目录中的用户数据: {root}")


def _require_no_bridge() -> None:
    state_dir = data_root() / ".state"
    if not state_dir.exists():
        return
    try:
        store = Store(state_dir)
    except RuntimeError:
        raise RuntimeError("前台桥接仍在运行，请先按 Ctrl+C 退出后再卸载。") from None
    except (OSError, ValueError) as error:
        raise RuntimeError(f"无法确认前台桥接是否已停止: {state_dir}") from error
    else:
        store.close()


async def uninstall(*, purge: bool = False) -> None:
    if IS_WINDOWS:
        await _windows_uninstall(purge)
        return
    await _linux_service_removal()
    _require_no_bridge()
    _linux_command_removal()
    if purge:
        _purge_linux_data()
    else:
        print(f"用户数据已保留: {data_root()}")
        print("如需同时删除配置、登录和待办，请使用 --purge-data。")


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--purge-data",
        action="store_true",
        help="同时删除配置、登录、待办、工作目录、日志和共享后台数据",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(uninstall(purge=args.purge_data))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"uninstall.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
