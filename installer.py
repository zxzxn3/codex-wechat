#!/usr/bin/env python3
"""Install or update a codex-wechat source checkout."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
LOGO_PATH = Path("assets/logo/codex-wechat.txt")
PRIVATE_NAMES = {
    ".git",
    ".state",
    ".update-backups",
    ".venv",
    "__pycache__",
    "dist",
    "workspace",
}
REQUIRED_FILES = {
    Path(".gitignore"),
    Path("codex-wechat"),
    Path("codex-wechat.ps1"),
    Path("codex_wechat.py"),
    Path("install.sh"),
    Path("install.ps1"),
    Path("installer.py"),
    Path("requirements.txt"),
}


def styled(text: str, code: str) -> str:
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
        return f"\033[{code}m{text}\033[0m"
    return text


def phase(text: str) -> None:
    print(styled("::", "1;34") + " " + styled(text, "1"), flush=True)


def detail(text: str) -> None:
    print("  " + styled("->", "2") + " " + text)


def confirm(prompt: str) -> bool:
    while True:
        try:
            answer = input(
                styled("::", "1;34") + " " + styled(prompt, "1")
                + " " + styled("[y/N]", "1") + " "
            ).strip().casefold()
        except EOFError:
            answer = ""
        if answer in ("", "n", "no"):
            print("已取消，没有继续修改。")
            return False
        if answer in ("y", "yes"):
            return True
        print("请输入 y 或 n。")


def introduction() -> bool:
    phase("开始之前")
    width = min(76, shutil.get_terminal_size(fallback=(80, 24)).columns)
    print(textwrap.fill(
        "codex-wechat 是独立项目，用于把微信私聊连接到本机 Codex。"
        "它会复制程序文件、准备 Python 环境，并保留已有的微信登录、"
        "配置、待办、会话和工作目录。不会复制 Codex 凭据或会话历史。",
        width=width,
    ))
    print()
    print(textwrap.fill(
        "接下来会先展示操作计划，再单独确认一次。继续这里不会直接修改文件。",
        width=width,
    ))
    print()
    return confirm("继续？")


def logo_banner(source: Path) -> None:
    """Display optional user-supplied art without requiring it for installation."""
    path = source / LOGO_PATH
    if (not path.is_file() or not sys.stdout.isatty()
            or os.environ.get("TERM") == "dumb"):
        return
    try:
        artwork = path.read_text(encoding="utf-8").rstrip("\n")
    except (OSError, UnicodeError):
        return
    columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    if artwork and max(map(len, artwork.splitlines()), default=0) <= columns:
        print(artwork)
        print()


def default_target() -> Path:
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise ValueError("Windows 缺少 LOCALAPPDATA，无法确定安装目录")
        return Path(local) / "codex-wechat" / "app"
    return Path.home() / ".local/share/codex-wechat"


def _private(relative: Path) -> bool:
    if relative == Path("config.json"):
        return True
    for part in relative.parts:
        if part in PRIVATE_NAMES or part == "__pycache__":
            return True
        if part.startswith(".") and part != ".gitignore":
            return True
    return relative.suffix.casefold() in (".pyc", ".pyo")


def project_files(source: Path) -> dict[Path, tuple[bytes, int]]:
    """Read the distributable source payload without following private links."""
    source = source.expanduser()
    if source.is_symlink():
        raise ValueError(f"源码目录不能是符号链接: {source}")
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"源码目录不存在: {source}")
    files: dict[Path, tuple[bytes, int]] = {}
    for current, directories, names in os.walk(source, followlinks=False):
        current_path = Path(current)
        public_directories = []
        for name in sorted(directories):
            path = current_path / name
            relative = path.relative_to(source)
            if _private(relative):
                continue
            if path.is_symlink():
                raise ValueError(f"源码中不允许符号链接: {relative}")
            public_directories.append(name)
        directories[:] = public_directories
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(source)
            if _private(relative):
                continue
            if path.is_symlink():
                raise ValueError(f"源码中不允许符号链接: {relative}")
            if not path.is_file():
                raise ValueError(f"源码中不是普通文件: {relative}")
            files[relative] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    missing = sorted(str(path) for path in REQUIRED_FILES if path not in files)
    if missing:
        raise ValueError("源码缺少必要文件: " + ", ".join(missing))
    return files


def _check_target_path(target: Path) -> None:
    if target.is_symlink():
        raise ValueError(f"安装目录不能是符号链接: {target}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"安装目录不是文件夹: {target}")


def _resolved_root(path: Path, label: str) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError(f"{label}不能是符号链接: {path}")
    return path.resolve()


def check_fresh_target(source: Path, target: Path) -> None:
    _check_target_path(target)
    if target == source:
        return
    if target.exists() and next(target.iterdir(), None) is not None:
        raise ValueError(
            f"安装目录非空: {target}\n"
            "现有安装请从另一份新源码运行 update。"
        )


def check_installed_target(source: Path, target: Path) -> None:
    _check_target_path(target)
    if target == source:
        raise ValueError("更新必须从另一份新源码运行，不能用安装目录中的同一副本")
    missing = [
        str(target / relative)
        for relative in (Path("codex_wechat.py"), Path("requirements.txt"), Path("codex-wechat"))
        if not (target / relative).is_file()
    ]
    if missing:
        raise ValueError(
            f"目标不是完整的 codex-wechat 安装: {target}\n"
            + "\n".join(missing)
        )


def _atomic_write(path: Path, content: bytes, mode: int, *, root: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"拒绝写入符号链接文件: {path}")
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError(f"拒绝写入符号链接目录: {parent}")
        if parent == root:
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_files(source: Path, target: Path, files: dict[Path, tuple[bytes, int]]) -> None:
    if source == target:
        return
    target.mkdir(parents=True, exist_ok=True)
    for relative, (content, mode) in files.items():
        _atomic_write(target / relative, content, mode, root=target)


def _snapshot_files(
    target: Path,
    files: dict[Path, tuple[bytes, int]],
) -> dict[Path, tuple[bytes, int] | None]:
    before: dict[Path, tuple[bytes, int] | None] = {}
    for relative in files:
        path = target / relative
        if path.is_symlink():
            raise ValueError(f"拒绝更新符号链接文件: {path}")
        for parent in (path.parent, *path.parent.parents):
            if parent.is_symlink():
                raise ValueError(f"拒绝更新符号链接路径: {parent}")
            if parent == target:
                break
        if path.exists() and not path.is_file():
            raise ValueError(f"目标路径不是普通文件: {relative}")
        before[relative] = (
            (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            if path.exists() else None
        )
    return before


def _changed_files(
    files: dict[Path, tuple[bytes, int]],
    before: dict[Path, tuple[bytes, int] | None],
) -> list[Path]:
    changes = [
        relative for relative, (content, mode) in files.items()
        if before[relative] is None
        or before[relative][0] != content
        or (before[relative][1] & 0o111) != (mode & 0o111)
    ]
    return changes


def update_files(
    target: Path,
    files: dict[Path, tuple[bytes, int]],
    *,
    expected_before: dict[Path, tuple[bytes, int] | None],
) -> tuple[Path | None, list[Path]]:
    before = _snapshot_files(target, files)
    if before != expected_before:
        raise ValueError("确认后安装目录发生变化，未应用更新；请重新运行")
    changes = _changed_files(files, before)
    if not changes:
        return None, []
    backup_root = target / ".update-backups"
    if backup_root.is_symlink():
        raise ValueError(f"拒绝使用符号链接备份目录: {backup_root}")
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(
        prefix="update-" + time.strftime("%Y%m%d-%H%M%S") + "-",
        dir=backup_root,
    ))
    for relative in changes:
        previous = before[relative]
        if previous is None:
            continue
        backup_path = backup / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(previous[0])
        backup_path.chmod(previous[1])
    for relative in changes:
        content, mode = files[relative]
        _atomic_write(target / relative, content, mode, root=target)
    for relative, (content, mode) in files.items():
        path = target / relative
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"文件校验失败: {relative}")
        if (path.stat().st_mode & 0o111) != (mode & 0o111):
            raise ValueError(f"文件权限校验失败: {relative}")
    return backup, changes


def _run(command: list[str], *, cwd: Path) -> None:
    detail("执行: " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _venv_python(root: Path) -> Path:
    if IS_WINDOWS:
        return root / ".venv/Scripts/python.exe"
    return root / ".venv/bin/python"


def prepare_posix(root: Path) -> None:
    python = _venv_python(root)
    if not python.is_file():
        _run([
            sys.executable,
            "-m",
            "venv",
            "--system-site-packages",
            str(root / ".venv"),
        ], cwd=root)
    if not python.is_file():
        raise ValueError(f"虚拟环境创建失败: {python}")
    _run([str(python), "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"], cwd=root)
    _run([
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(root / "requirements.txt"),
    ], cwd=root)
    _run(["bash", str(root / "install.sh")], cwd=root)
    link = Path.home() / ".local/bin/codex-wechat"
    if not link.is_symlink() or link.resolve() != (root / "codex-wechat").resolve():
        raise ValueError(f"命令链接校验失败: {link}")


def prepare_windows(root: Path, *, update: bool) -> None:
    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not powershell:
        raise ValueError("找不到 PowerShell，无法完成 Windows 安装")
    if not update:
        _run([
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "install.ps1"),
        ], cwd=root)
        return
    python = _venv_python(root)
    if not python.is_file():
        raise ValueError(
            f"现有 Windows 安装缺少虚拟环境: {python}\n"
            "请运行 install.ps1 完成环境修复后再更新。"
        )
    _run([
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(root / "requirements.txt"),
    ], cwd=root)


def prepare_runtime(root: Path, *, update: bool) -> None:
    if IS_WINDOWS:
        prepare_windows(root, update=update)
    else:
        prepare_posix(root)
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            raise ValueError(f"安装后缺少文件: {relative}")


def operation_plan(operations: list[str]) -> None:
    width = max(map(len, operations), default=1) + 4
    for number, operation in enumerate(operations, 1):
        print(
            styled(f"({number}/{len(operations)})", "2")
            + f" {operation:<{width}}"
            + styled("待执行", "2")
        )


def _common_install(
    source: Path,
    target: Path,
    *,
    dry_run: bool,
    update: bool,
) -> int:
    source = _resolved_root(source, "源码目录")
    target = _resolved_root(target, "安装目录")
    files = project_files(source)
    if update:
        check_installed_target(source, target)
    else:
        check_fresh_target(source, target)
    if update:
        before = _snapshot_files(target, files)
        changes = _changed_files(files, before)
        if not changes:
            print("项目文件已经是最新版本，没有修改。")
            return 0
    logo_banner(source)
    if dry_run:
        phase("演练模式，不会修改文件")
    if not introduction():
        return 0

    if update:
        operations = ["备份被替换文件", "更新项目文件", "更新运行环境", "校验安装"]
        phase("准备更新...")
    else:
        operations = (
            ["安装项目文件", "更新运行环境", "校验安装"]
            if source != target
            else ["校验当前源码", "更新运行环境", "校验安装"]
        )
        phase("准备安装...")

    print()
    operation_plan(operations)
    print()
    print(styled("安装目录:", "2") + "  " + styled(str(target), "1"))
    if update:
        print(styled("备份目录:", "2") + "  " + styled(str(target / ".update-backups"), "1"))
        for relative in changes:
            action = "新增" if before[relative] is None else "替换"
            detail(f"{action}: {relative}")
        detail("保留 .state、.venv、config.json、workspace 和未知文件。")
    elif source == target:
        detail("当前已在安装目录中运行，只准备运行环境，不复制源码。")
    else:
        detail(f"将安装 {len(files)} 个项目文件。")
    print()
    if not confirm("执行更新？" if update else "执行安装？"):
        return 0
    if dry_run:
        print("演练完成，没有修改文件。")
        return 0

    # Recheck after both prompts before making changes.
    if update:
        check_installed_target(source, target)
    else:
        check_fresh_target(source, target)
    try:
        if not update and source != target:
            phase("正在安装文件...")
            install_files(source, target, files)
        if update:
            phase("正在更新文件...")
            backup, changes = update_files(target, files, expected_before=before)
            if backup is None:
                print("项目文件已经是最新版本，没有修改。")
                return 0
            detail(f"备份: {backup}")
        phase("正在准备运行环境...")
        prepare_runtime(target, update=update)
    except KeyboardInterrupt:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        hint = (
            f"\n备份: {target / '.update-backups'}"
            if update else "\n先前完成的步骤可能已保留。"
        )
        raise ValueError(f"安装未完成: {error}{hint}") from error

    phase("校验安装...")
    for relative, (content, mode) in files.items():
        path = target / relative
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"安装文件校验失败: {relative}")
        if (path.stat().st_mode & 0o111) != (mode & 0o111):
            raise ValueError(f"安装文件权限校验失败: {relative}")
    detail("项目文件、权限和运行入口校验通过。")
    print(styled("==> 安装完成。" if not update else "==> 更新完成。", "1;32"))
    next_steps(target, update=update)
    return 0


def next_steps(target: Path, *, update: bool) -> None:
    print()
    phase("下一步")
    if update:
        if IS_WINDOWS:
            print("重启服务: .\\codex-wechat.ps1 service-restart")
        else:
            print("重启服务: systemctl --user restart codex-wechat")
        return
    if IS_WINDOWS:
        print("查看状态: .\\codex-wechat.ps1 status")
        print("如需登录或调整自启: .\\codex-wechat.ps1 login / .\\codex-wechat.ps1 autostart on")
    else:
        print("扫码登录: codex-wechat login")
        print("前台启动: codex-wechat start")
        print("微信内开启自启: /settings autostart on")


def install(source: Path, target: Path, *, dry_run: bool = False) -> int:
    return _common_install(source, target, dry_run=dry_run, update=False)


def update(source: Path, target: Path, *, dry_run: bool = False) -> int:
    return _common_install(source, target, dry_run=dry_run, update=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="installer.py",
        description="安装或更新 codex-wechat，并保留微信登录与运行状态。",
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    for name, help_text in (("install", "安装 codex-wechat"), ("update", "从新源码更新现有安装")):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument(
            "--dir",
            type=Path,
            default=None,
            help="项目安装目录；默认使用当前平台的标准目录",
        )
        command.add_argument("--dry-run", action="store_true", help="显示计划但不修改文件")
    return result


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    target = (options.dir or default_target()).expanduser()
    if target.is_symlink():
        raise ValueError(f"安装目录不能是符号链接: {target}")
    target = target.resolve()
    if not IS_WINDOWS and target != default_target().resolve():
        raise ValueError(f"Linux 安装目录必须是 {default_target()}")
    if options.command == "update":
        return update(SOURCE_ROOT, target, dry_run=options.dry_run)
    return install(SOURCE_ROOT, target, dry_run=options.dry_run)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninstaller.py: 已取消", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"installer.py: {error}", file=sys.stderr)
        raise SystemExit(1)
