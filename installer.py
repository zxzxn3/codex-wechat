#!/usr/bin/env python3
"""Install immutable codex-wechat releases and switch them atomically."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
LOGO_PATH = Path("assets/logo/codex-wechat.txt")
RELEASE_FORMAT = 1
CURRENT_FILE = "current"
PREVIOUS_FILE = "previous"
RELEASES_DIR = "releases"
LOCK_FILE = ".install-lock"
MANIFEST_FILE = ".release.json"
LEGACY_ID = "legacy"
PRIVATE_NAMES = {
    ".git",
    ".state",
    ".update-backups",
    ".venv",
    ".bootstrap-venv",
    "__pycache__",
    "dist",
    RELEASES_DIR,
    "workspace",
    CURRENT_FILE,
    PREVIOUS_FILE,
    LOCK_FILE,
}
REQUIRED_FILES = {
    Path(".gitignore"),
    Path("bootstrap_supervisor.py"),
    Path("codex-wechat"),
    Path("codex-wechat.ps1"),
    Path("codex_wechat.py"),
    Path("install.sh"),
    Path("install.ps1"),
    Path("installer.py"),
    Path("requirements.txt"),
}
STABLE_LINUX_FILES = (
    Path("codex-wechat"),
    Path("codex-wechat.service"),
    Path("installer.py"),
)
STABLE_WINDOWS_FILES = (
    Path("bootstrap_supervisor.py"),
    Path("codex-wechat.ps1"),
    Path("uninstall.ps1"),
)


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
        "安装会创建一份不可变程序版本，并保留已有的微信登录、配置、"
        "待办、会话和工作目录；不会复制 Codex 凭据或会话历史。",
        width=width,
    ))
    print()
    print(textwrap.fill(
        "接下来会先展示版本和操作计划，再单独确认一次。继续这里不会直接修改文件。",
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
        if part in PRIVATE_NAMES:
            return True
        if part.startswith(".") and part != ".gitignore":
            return True
    return relative.suffix.casefold() in (".pyc", ".pyo")


def project_files(source: Path) -> dict[Path, tuple[bytes, int]]:
    """Read distributable files without following private or public symlinks."""
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


def release_identifier(files: dict[Path, tuple[bytes, int]]) -> str:
    digest = hashlib.sha256()
    digest.update(
        f"codex-wechat-release-{RELEASE_FORMAT}-{sys.platform}\0".encode())
    for relative in sorted(files, key=lambda value: value.as_posix()):
        content, mode = files[relative]
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(mode & 0o777).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _resolved_root(path: Path, label: str) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError(f"{label}不能是符号链接: {path}")
    return path.resolve()


def _check_target_path(target: Path) -> None:
    if target.is_symlink():
        raise ValueError(f"安装目录不能是符号链接: {target}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"安装目录不是文件夹: {target}")


def read_pointer(target: Path, name: str) -> str | None:
    path = target / name
    if path.is_symlink():
        raise ValueError(f"版本指针不能是符号链接: {path}")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"无法读取版本指针 {path}: {error}") from error
    if not value:
        return None
    if value != LEGACY_ID and (
            not value.isascii() or not all(char.isalnum() or char in "._-" for char in value)):
        raise ValueError(f"版本指针内容无效: {path}")
    return value


def _atomic_write(
    path: Path,
    content: bytes,
    mode: int,
    *,
    root: Path,
) -> None:
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


def _write_pointer(target: Path, name: str, value: str) -> None:
    _atomic_write(
        target / name,
        (value + "\n").encode("utf-8"),
        0o600,
        root=target,
    )


class InstallLock:
    def __init__(self, target: Path):
        self.path = target / LOCK_FILE
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError(f"安装锁不能是符号链接: {self.path}")
        self.stream = self.path.open("a+b")
        try:
            if IS_WINDOWS:
                import msvcrt
                self.stream.seek(0, os.SEEK_END)
                if self.stream.tell() == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise ValueError("另一个安装器正在修改该目录") from None
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.stream is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def _legacy_installation(target: Path) -> bool:
    return (target / "codex_wechat.py").is_file() and (
        target / "installer.py").is_file()


def _installation_state(target: Path) -> str | None:
    current = read_pointer(target, CURRENT_FILE)
    if current:
        return current
    return LEGACY_ID if _legacy_installation(target) else None


def check_install_target(
    source: Path,
    target: Path,
    *,
    update: bool,
) -> None:
    _check_target_path(target)
    state = _installation_state(target)
    if source == target:
        if read_pointer(target, CURRENT_FILE):
            raise ValueError(
                "版本化安装不能把安装根目录本身当作新源码；"
                "请从另一份源码 checkout 运行 update。"
            )
        return
    if update:
        if state is None:
            raise ValueError(
                f"目标不是现有 codex-wechat 安装: {target}\n"
                "全新安装请使用 install。"
            )
        return
    if state is not None:
        raise ValueError(
            f"目标已有 codex-wechat 安装: {target}\n"
            "现有安装在保留数据的前提下升级，请使用 update。"
        )
    if target.exists() and any(
            path.name != LOCK_FILE for path in target.iterdir()):
        raise ValueError(f"安装目录非空: {target}")


def release_path(target: Path, identifier: str) -> Path:
    if identifier == LEGACY_ID:
        return target
    if not identifier.isascii() or not all(
            char.isalnum() or char in "._-" for char in identifier):
        raise ValueError(f"版本标识无效: {identifier}")
    return target / RELEASES_DIR / identifier


def release_matches(target: Path, identifier: str, files=None) -> bool:
    if identifier == LEGACY_ID:
        return _legacy_installation(target)
    if target.is_symlink():
        return False
    releases = target / RELEASES_DIR
    if releases.is_symlink():
        return False
    root = release_path(target, identifier)
    if root.is_symlink() or not root.is_dir():
        return False
    try:
        manifest = json.loads((root / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(manifest, dict) or manifest.get("schema") != RELEASE_FORMAT:
        return False
    if manifest.get("id") != identifier or manifest.get("platform") != sys.platform:
        return False
    try:
        actual = release_identifier(project_files(root))
    except (OSError, ValueError):
        return False
    return actual == identifier and (files is None or actual == release_identifier(files))


def _copy_release(staging: Path, files: dict[Path, tuple[bytes, int]]) -> None:
    for relative, (content, mode) in files.items():
        _atomic_write(staging / relative, content, mode, root=staging)
    manifest = {
        "schema": RELEASE_FORMAT,
        "id": release_identifier(files),
        "platform": sys.platform,
        "files": sorted(path.as_posix() for path in files),
    }
    _atomic_write(
        staging / MANIFEST_FILE,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        0o600,
        root=staging,
    )


def _run(command: list[str], *, cwd: Path) -> None:
    detail("执行: " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _venv_python(root: Path) -> Path:
    if IS_WINDOWS:
        return root / ".venv/Scripts/python.exe"
    return root / ".venv/bin/python"


def prepare_runtime(root: Path) -> None:
    """Create a release-local environment and verify its Python imports."""
    python = _venv_python(root)
    if not python.is_file():
        command = [sys.executable, "-m", "venv"]
        if not IS_WINDOWS:
            command.append("--system-site-packages")
        command.append(str(root / ".venv"))
        _run(command, cwd=root)
    if not python.is_file():
        raise ValueError(f"虚拟环境创建失败: {python}")
    _run([
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(root / "requirements.txt"),
    ], cwd=root)
    _run([
        str(python),
        "-c",
        "import cryptography, websockets; "
        "from websockets.asyncio.client import unix_connect",
    ], cwd=root)
    python_files = [
        str(path) for path in project_files(root)
        if path.suffix == ".py"
    ]
    _run([str(python), "-m", "py_compile", *python_files], cwd=root)


def prepare_bootstrap_runtime(target: Path) -> None:
    if not IS_WINDOWS:
        return
    root = target / ".bootstrap-venv"
    if root.is_symlink():
        raise ValueError(f"Windows supervisor 环境不能是符号链接: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"Windows supervisor 环境不是目录: {root}")
    python = target / ".bootstrap-venv/Scripts/python.exe"
    if python.is_file():
        _run([
            str(python),
            "-c",
            "import sys; raise SystemExit(sys.version_info < (3, 11))",
        ], cwd=target)
        return
    _run([
        sys.executable,
        "-m",
        "venv",
        str(root),
    ], cwd=target)
    if not python.is_file():
        raise ValueError(f"Windows supervisor 环境创建失败: {python}")


def install_release(
    target: Path,
    identifier: str,
    files: dict[Path, tuple[bytes, int]],
) -> tuple[Path, bool]:
    releases = target / RELEASES_DIR
    if releases.is_symlink():
        raise ValueError(f"版本目录不能是符号链接: {releases}")
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = release_path(target, identifier)
    if final.exists():
        if not release_matches(target, identifier, files):
            raise ValueError(f"现有版本内容与标识不一致: {final}")
        return final, False
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases))
    try:
        _copy_release(staging, files)
        prepare_runtime(staging)
        if release_identifier(project_files(staging)) != identifier:
            raise ValueError("暂存版本校验失败")
        os.replace(staging, final)
        return final, True
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _stable_files() -> tuple[Path, ...]:
    return STABLE_WINDOWS_FILES if IS_WINDOWS else STABLE_LINUX_FILES


def stable_files_match(source: Path, target: Path) -> bool:
    for relative in _stable_files():
        source_path = source / relative
        target_path = target / relative
        if (source_path.is_symlink() or target_path.is_symlink()
                or not source_path.is_file() or not target_path.is_file()):
            return False
        try:
            if (source_path.read_bytes() != target_path.read_bytes()
                    or stat.S_IMODE(source_path.stat().st_mode)
                    != stat.S_IMODE(target_path.stat().st_mode)):
                return False
        except OSError:
            return False
    if IS_WINDOWS:
        return (target / ".bootstrap-venv/Scripts/python.exe").is_file()
    return True


def install_bootstrap(source: Path, target: Path) -> None:
    for relative in _stable_files():
        source_path = source / relative
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError(f"缺少安装入口文件: {relative}")
        content = source_path.read_bytes()
        mode = stat.S_IMODE(source_path.stat().st_mode)
        _atomic_write(target / relative, content, mode, root=target)
    prepare_bootstrap_runtime(target)


def activate_release(target: Path, identifier: str) -> str | None:
    old = read_pointer(target, CURRENT_FILE)
    if old == identifier:
        return old
    previous = old or (LEGACY_ID if _legacy_installation(target) else "")
    if previous:
        _write_pointer(target, PREVIOUS_FILE, previous)
    _write_pointer(target, CURRENT_FILE, identifier)
    return old


def rollback_activation(target: Path, old: str | None) -> None:
    current = target / CURRENT_FILE
    if old:
        _write_pointer(target, CURRENT_FILE, old)
    else:
        current.unlink(missing_ok=True)


def _linux_command_link() -> Path:
    return Path.home() / ".local/bin/codex-wechat"


def preflight_command_link(target: Path) -> None:
    if IS_WINDOWS:
        return
    link = _linux_command_link()
    expected = (target / "codex-wechat").resolve()
    if link.is_symlink():
        if link.resolve() != expected:
            raise ValueError(f"{link} 已指向其它程序，请先自行处理")
    elif link.exists():
        raise ValueError(f"{link} 已存在且不是项目命令链接")


def install_command_link(target: Path) -> None:
    if IS_WINDOWS:
        return
    link = _linux_command_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    expected = (target / "codex-wechat").resolve()
    if link.is_symlink() and link.resolve() == expected:
        return
    link.symlink_to(os.path.relpath(expected, link.parent))


def configure_windows(release: Path, target: Path, *, update: bool) -> None:
    if not IS_WINDOWS:
        return
    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not powershell:
        raise ValueError("找不到 PowerShell，无法完成 Windows 安装")
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(release / "install.ps1"),
        "-InstallRoot",
        str(target),
    ]
    if update:
        command.extend(["-PreserveAutostart", "-NoStart"])
    _run(command, cwd=release)


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
    target = target.expanduser().absolute() if dry_run else _resolved_root(target, "安装目录")
    if not dry_run and source == target and read_pointer(target, CURRENT_FILE):
        raise ValueError(
            "版本化安装不能把安装根目录本身当作新源码；"
            "请从另一份源码 checkout 运行 update。"
        )
    files = project_files(source)
    identifier = release_identifier(files)
    if not dry_run:
        check_install_target(source, target, update=update)
    current = None if dry_run else read_pointer(target, CURRENT_FILE)
    if current == identifier and release_matches(target, identifier, files):
        if stable_files_match(source, target):
            print(f"版本 {identifier} 已经安装，没有修改。")
            return 0
        if dry_run:
            print(f"版本 {identifier} 未变化，但稳定入口需要修复。")
            return 0
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        with InstallLock(target):
            if (read_pointer(target, CURRENT_FILE) != identifier
                    or not release_matches(target, identifier, files)):
                raise ValueError("确认期间当前版本发生变化，请重新运行")
            install_bootstrap(source, target)
        print(f"版本 {identifier} 未变化，已修复稳定入口。")
        return 0
    if not dry_run:
        preflight_command_link(target)
    logo_banner(source)
    if dry_run:
        phase("演练模式，不会修改文件")
        detail("因 --dry-run，真实安装目录与命令入口检查按通过处理；不代表实际安装条件已满足。")
    if not introduction():
        return 0

    operations = ["构建并校验新版本", "安装稳定启动入口", "原子切换当前版本"]
    if IS_WINDOWS:
        operations.extend(["更新计划任务", "保留现有登录与数据"])
    else:
        operations.extend(["校验命令入口", "保留现有登录与数据"])
    phase("准备更新..." if update else "准备安装...")
    print()
    operation_plan(operations)
    print()
    print(styled("安装目录:", "2") + "  " + styled(str(target), "1"))
    print(styled("版本:", "2") + "      " + styled(identifier, "1"))
    print(styled("版本目录:", "2") + "  " + styled(str(target / RELEASES_DIR / identifier), "1"))
    previous = None if dry_run else read_pointer(target, PREVIOUS_FILE)
    if current:
        detail(f"当前版本: {current}")
    elif not dry_run and _legacy_installation(target):
        detail("当前是旧式直接安装，本次会迁移为版本化安装。")
    if previous:
        detail(f"上一版本: {previous}")
    detail("保留 .state、config.json、workspace、日志和未知本地文件。")
    print()
    if not confirm("执行更新？" if update else "执行安装？"):
        return 0
    if dry_run:
        print("演练完成，没有修改文件。")
        phase("正式安装或更新成功后的使用步骤；本次未执行")
        next_steps(target, update=update, identifier=identifier)
        return 0

    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with InstallLock(target):
        check_install_target(source, target, update=update)
        fresh_files = project_files(source)
        if release_identifier(fresh_files) != identifier or fresh_files != files:
            raise ValueError("确认期间源码发生变化，未激活新版本；请重新运行")
        phase("正在构建并校验新版本...")
        release, created = install_release(target, identifier, files)
        if created:
            detail(f"已创建不可变版本: {release}")
        else:
            detail(f"复用已校验版本: {release}")
        phase("正在安装稳定入口...")
        install_bootstrap(source, target)
        old = read_pointer(target, CURRENT_FILE)
        phase("正在原子切换版本...")
        activate_release(target, identifier)
        try:
            install_command_link(target)
            configure_windows(release, target, update=update)
        except (OSError, ValueError, subprocess.SubprocessError):
            rollback_activation(target, old)
            raise
    phase("安装校验...")
    if not release_matches(target, identifier):
        rollback_activation(target, old)
        raise ValueError("激活后的版本校验失败，已恢复原 current 指针")
    detail("版本内容、运行环境和原子指针校验通过。")
    print(styled("==> 安装完成。" if not update else "==> 更新完成。", "1;32"))
    next_steps(target, update=update, identifier=identifier)
    return 0


def next_steps(target: Path, *, update: bool, identifier: str) -> None:
    print()
    phase("下一步")
    print(f"版本: {identifier}")
    if IS_WINDOWS:
        entry = str(target / "codex-wechat.ps1").replace("'", "''")
        command = f"& '{entry}'"
        if update:
            print(f"重启服务: {command} service-restart")
        else:
            print("安装时会在终端请求微信扫码，并询问登录自启（默认关闭）。")
            print("若已扫码并启动，可直接在微信发送 /help 或普通消息。")
            print(f"补做扫码: {command} login")
            print(f"手动启动: {command} service-start")
    elif update:
        print("重启已有服务: systemctl --user restart codex-wechat")
        print("若未使用服务，先退出原前台进程，再运行 codex-wechat start。")
        print(f"回滚版本: {target / 'codex-wechat'} rollback")
    else:
        print("1. 确认 Codex 已安装并登录。")
        print(f"2. 在终端运行 {target / 'codex-wechat'} login，手机微信扫码确认。")
        print(f"3. 运行 {target / 'codex-wechat'} start，保持此终端打开。")
        print("4. 在微信发送 /help、/status，然后发送普通消息开始交流。")
        print("自启默认关闭；需要时在微信发送 /settings autostart on。")
        print("若提示找不到 codex-wechat，请使用上面的完整路径，或将 ~/.local/bin 加入 PATH。")


def install(source: Path, target: Path, *, dry_run: bool = False) -> int:
    return _common_install(source, target, dry_run=dry_run, update=False)


def update(source: Path, target: Path, *, dry_run: bool = False) -> int:
    return _common_install(source, target, dry_run=dry_run, update=True)


def rollback(target: Path, *, dry_run: bool = False) -> int:
    if dry_run:
        phase("演练模式，不会修改文件")
        detail("因 --dry-run，真实安装目录与回滚版本检查按通过处理；不代表已有可回滚版本。")
        print(f"安装目录: {target.expanduser().absolute()}")
        print("计划: 将 current 切换到 previous，保留原当前版本供再次回滚。")
        print("演练完成，没有修改版本指针。")
        return 0
    target = _resolved_root(target, "安装目录")
    _check_target_path(target)
    current = read_pointer(target, CURRENT_FILE)
    previous = read_pointer(target, PREVIOUS_FILE)
    if not current:
        raise ValueError("当前没有版本化安装，无法回滚")
    if not previous:
        raise ValueError("没有可回滚的上一版本")
    if not release_matches(target, previous):
        raise ValueError(f"上一版本不完整，拒绝切换: {previous}")
    print(f"当前版本: {current}")
    print(f"回滚版本: {previous}")
    with InstallLock(target):
        if read_pointer(target, CURRENT_FILE) != current:
            raise ValueError("确认期间 current 指针发生变化，请重新运行")
        if previous == LEGACY_ID:
            (target / CURRENT_FILE).unlink()
        else:
            _write_pointer(target, CURRENT_FILE, previous)
        _write_pointer(target, PREVIOUS_FILE, current)
    print("==> 回滚指针已切换；重启服务后生效。")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="installer.py",
        description="安装、更新或回滚 codex-wechat 的不可变版本。",
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("install", "安装 codex-wechat"),
        ("update", "从新源码构建并切换版本"),
        ("rollback", "切换到上一版本"),
    ):
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
    if not options.dry_run and target.is_symlink():
        raise ValueError(f"安装目录不能是符号链接: {target}")
    target = target.absolute() if options.dry_run else target.resolve()
    if not options.dry_run and not IS_WINDOWS and target != default_target().resolve():
        raise ValueError(f"Linux 安装目录必须是 {default_target()}")
    if options.command == "rollback":
        return rollback(target, dry_run=options.dry_run)
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
