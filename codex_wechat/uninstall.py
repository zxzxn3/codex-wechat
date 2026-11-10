"""Linux stable uninstall helper; Windows uses uninstall.ps1."""
import asyncio
from pathlib import Path
import re
import shutil
import sys

from . import service_adapter
from .install_layout import InstalledLayout, current_platform
from .platform_support import INSTALL_ROOT, IS_WINDOWS
from .service_control import UNIT, systemctl
from .storage import Store

SHARED_UNIT_PATTERN = re.compile(r"codex-wechat-codex-[0-9a-f]{20}\.service")


def _command_link() -> Path:
    return Path.home() / ".local/bin/codex-wechat"


def _default_unit_link() -> Path:
    return Path.home() / ".config/systemd/user" / UNIT


def _unit_candidates(layout: InstalledLayout) -> tuple[Path, ...]:
    """Possible systemd links for diagnostics when status is unavailable."""
    configured = layout.config_root.parent / "systemd/user" / UNIT
    default = _default_unit_link()
    return (configured,) if configured == default else (configured, default)


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


async def _linux_service_removal(layout: InstalledLayout) -> None:
    try:
        current = await service_adapter.status()
    except FileNotFoundError:
        # No systemd client is installed; there cannot be a registered unit.
        current = None
    except (OSError, RuntimeError) as error:
        # Preserve foreground-only installs, but never guess that an existing
        # unit link in any configured location is absent.
        if not any(
                candidate.exists() or candidate.is_symlink()
                for candidate in _unit_candidates(layout)):
            print("systemd 用户服务不可用；跳过服务移除。")
            return
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
    registered = current.fragment_path
    expected = (INSTALL_ROOT / UNIT).resolve()
    try:
        owns_link = (registered is not None and registered.is_symlink()
                     and registered.resolve() == expected)
    except OSError:
        owns_link = False
    if owns_link and registered is not None:
        registered.unlink()
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


async def _linux_shared_backend_removal(paths) -> None:
    """Stop only units whose working directory belongs to this data root."""
    if shutil.which("systemctl") is None:
        return
    output = await systemctl(
        "list-units", "--all", "--plain", "--no-legend",
        "codex-wechat-codex-*.service",
    )
    units = []
    for line in output.splitlines():
        if not line.strip():
            continue
        for field in line.split():
            if SHARED_UNIT_PATTERN.fullmatch(field) and field not in units:
                units.append(field)
                break
    data_root = paths.data_root.resolve()
    for unit in units:
        try:
            workdir = await systemctl(
                "show", unit, "--property=WorkingDirectory", "--value")
            if not workdir:
                continue
            cwd = Path(workdir).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if cwd != data_root and not cwd.is_relative_to(data_root):
            continue
        await systemctl("stop", unit, timeout=20)
        print(f"已停止共享后台: {unit}")


def _purge_linux_data(plan) -> None:
    for item in plan.items:
        _remove_path(item.path)
    try:
        next(item.path for item in plan.items if item.role == "配置").parent.rmdir()
    except OSError:
        pass
    print("已删除配置、登录、待办、工作目录、日志和共享后台数据。")


def _require_no_bridge(state_dir: Path | None = None) -> None:
    if state_dir is None:
        layout = InstalledLayout.load(INSTALL_ROOT, platform=current_platform())
        if layout is None:
            return
        state_dir = layout.runtime_paths().state_dir
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
        raise RuntimeError("Windows 卸载请使用 stable uninstall.ps1；uninstall.py 仅用于 Linux。")
    layout = InstalledLayout.load_required(
        INSTALL_ROOT, platform=current_platform())
    paths = layout.runtime_paths()
    plan = None
    if purge:
        plan = layout.purge_plan()
    await _linux_service_removal(layout)
    _require_no_bridge(paths.state_dir)
    if purge:
        await _linux_shared_backend_removal(paths)
    _linux_command_removal()
    if purge:
        _purge_linux_data(plan)
        layout.remove()
    # Actions report above; this summary names what is left, in one order shared
    # with the Windows helper: program files, data, how to keep using it, then the
    # decision about removing the rest. The uninstaller runs out of the install
    # tree, so it cannot delete it itself.
    print(f"程序文件仍在: {INSTALL_ROOT}（含 releases 与 Python 环境，不含用户数据）")
    if purge:
        print(f"数据文件已删除: {paths.data_root}")
        print("如需在同一路径重新安装，请先手动删除上述程序目录。")
    else:
        print(f"数据文件仍在: {paths.data_root}")
        print(f"配置仍在: {paths.config_file}")
        print(f"状态仍在: {paths.state_dir}")
        print("重新运行安装器会继续使用这个数据根；如需同时删除配置、登录和待办，"
              "请使用 --purge-data。")
    print("如不再需要，可在本命令结束后手动删除上述目录。")


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
