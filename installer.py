#!/usr/bin/env python3
"""Install immutable codex-wechat releases and switch them atomically."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
LOGO_PATH = Path("assets/logo/codex-wechat.txt")
DECLARATION_PATHS = {
    "start": Path("assets/install/after-art.txt"),
    "complete": Path("assets/install/after-install.txt"),
}
NON_INTERACTIVE = False
# Requirements for Python packages remain solely in requirements.txt.
DEPENDENCIES = (
    ("Codex CLI", "codex.exe" if os.name == "nt" else "codex", None, "微信模型任务", "warn"),
    ("curl", "curl.exe" if os.name == "nt" else "curl", (8, 4), "微信登录与收发", "warn"),
    ("qrencode", "qrencode", None, "终端二维码", "warn"),
    ("systemd-run", "systemd-run", (254,), "Linux 共享后台", "warn"),
)
COMPONENTS = {
    "wechat-attachments": ("微信附件说明 skill", Path("skills/wechat-attachments/SKILL.md")),
}
RELEASE_FORMAT = 1
CURRENT_FILE = "current"
PREVIOUS_FILE = "previous"
RELEASES_DIR = "releases"
LOCK_FILE = ".install-lock"
MANIFEST_FILE = ".release.json"
LEGACY_ID = "legacy"
UPDATE_SOURCE_FILE = ".update-source.json"
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
    UPDATE_SOURCE_FILE,
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
    Path("installer.py"),
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
    if NON_INTERACTIVE:
        return True
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
        "接下来展示操作计划，确认后执行；不启用新自启、不启动服务、不安装附加组件。",
        width=width,
    ))
    print()
    return True


def asset_path(source: Path, relative: Path) -> Path | None:
    path = source / relative
    for part in (path, *path.parents):
        if part.is_symlink():
            return None
        if part == source:
            break
    return path if path.is_file() else None


def logo_banner(source: Path) -> None:
    """Display optional user-supplied art without requiring it for installation."""
    path = asset_path(source, LOGO_PATH)
    if (path is None or not sys.stdout.isatty()
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
    source: Path | None,
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
        if not release_matches(target, state):
            raise ValueError(
                f"目标不是完整的现有 codex-wechat 安装: {target}\n"
                "未修改现有文件。"
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
        "--no-cache-dir",
        "-r",
        str(root / "requirements.txt"),
    ], cwd=root)
    _run([
        str(python),
        "-c",
        "import importlib.metadata; "
        "print(\"Python dependencies installed\")",
    ], cwd=root)
    python_files = [
        str(path) for path in project_files(root)
        if path.suffix == ".py"
    ]
    _run([str(python), "-c", "from pathlib import Path; import sys; "
          "[compile(Path(p).read_bytes(), p, 'exec') for p in sys.argv[1:]]", *python_files], cwd=root)


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
    created = not root.exists()
    try:
        _run([sys.executable, "-m", "venv", str(root)], cwd=target)
        if not python.is_file():
            raise ValueError(f"Windows supervisor 环境创建失败: {python}")
    except BaseException:
        if created and root.exists():
            shutil.rmtree(root)
        raise



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
    # Allocate/write every entry before replacing the first installed file.
    with tempfile.TemporaryDirectory(prefix=".entry-", dir=target) as directory:
        staging = Path(directory)
        for relative in _stable_files():
            source_path = source / relative
            destination = target / relative
            if not source_path.is_file() or source_path.is_symlink() or destination.is_symlink():
                raise ValueError(f"安装入口不是普通文件: {relative}")
            _atomic_write(staging / relative, source_path.read_bytes(),
                          stat.S_IMODE(source_path.stat().st_mode), root=staging)
        for relative in _stable_files():
            os.replace(staging / relative, target / relative)


def activate_release(target: Path, identifier: str) -> None:
    _write_pointer(target, CURRENT_FILE, identifier)


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
    command.extend(["-PreparedRuntime", "-PreserveAutostart", "-NoStart"])
    _run(command, cwd=release)


def operation_plan(operations: list[str]) -> None:
    width = max(map(len, operations), default=1) + 4
    for number, operation in enumerate(operations, 1):
        print(
            styled(f"({number}/{len(operations)})", "2")
            + f" {operation:<{width}}"
            + styled("待执行", "2")
        )


def _common_install(source: Path, target: Path, *, dry_run: bool, update: bool) -> int:
    source = _resolved_root(source, "源码目录")
    target = target.expanduser().absolute() if dry_run else _resolved_root(target, "安装目录")
    if not dry_run and source == target and read_pointer(target, CURRENT_FILE):
        raise ValueError(
            "版本化安装不能把安装根目录本身当作新源码；"
            "请从另一份源码 checkout 运行 update。"
        )
    if source != SOURCE_ROOT:
        logo_banner(source)
        declaration(source, "start")
    phase("演练模式，不会修改文件" if dry_run else "安装 / 更新")
    try:
        files = project_files(source)
    except (OSError, ValueError) as error:
        if not dry_run:
            raise
        detail(f"源码检查: {error}；因 --dry-run 放行。")
        files = None
    identifier = release_identifier(files) if files is not None else "演示版本"
    if dry_run:
        detail("因 --dry-run，真实安装目录与命令入口检查按通过处理；不代表实际安装条件已满足。")
        current = None
    else:
        check_install_target(source, target, update=update)
        preflight_command_link(target)
        current = read_pointer(target, CURRENT_FILE)
        if current == identifier and release_matches(target, identifier, files) and stable_files_match(source, target):
            print(f"版本 {identifier} 已经安装，没有修改。")
            return 0
    if not introduction():
        return 0
    report_dependencies(source, dry_run=dry_run)
    operation_plan(["准备程序与 Python 依赖", "集中更新入口及当前版本", "保留配置、登录与工作内容", "清理本次无用产物"])
    print(f"安装目录: {target}")
    print(f"版本: {identifier}")
    if not confirm("执行更新？" if update else "执行安装？"):
        return 0
    if dry_run:
        print("演练完成，没有修改文件。")
        declaration(source, "complete")
        return 0

    created_target = not target.exists()
    release = None
    created = False
    try:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        with InstallLock(target):
            check_install_target(source, target, update=update)
            fresh_files = project_files(source)
            if fresh_files != files:
                raise ValueError("确认期间源码发生变化，请重新运行")
            if read_pointer(target, CURRENT_FILE) != current:
                raise ValueError("确认期间当前版本发生变化，请重新运行")
            phase("准备程序与依赖...")
            release, created = install_release(target, identifier, files)
            prepare_bootstrap_runtime(target)
            # Everything requiring downloads/builds/user input finishes above.
            phase("更新入口与当前版本...")
            install_bootstrap(source, target)
            install_command_link(target)
            activate_release(target, identifier)
            configure_windows(release, target, update=update)
            (target / PREVIOUS_FILE).unlink(missing_ok=True)
    except BaseException:
        # Only delete a release we created and never activated; no restoration.
        if created and release is not None and read_pointer(target, CURRENT_FILE) != identifier:
            shutil.rmtree(release)
        if created_target:
            # Remove an empty failed installation, but preserve partial commits.
            for path in (target / RELEASES_DIR, target):
                try:
                    if path == target:
                        (target / LOCK_FILE).unlink(missing_ok=True)
                    path.rmdir()
                except OSError:
                    pass
        raise
    print(styled("==> 更新完成。" if update else "==> 安装完成。", "1;32"))
    declaration(source, "complete")
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
            print("安装时不扫码，登录自启默认关闭。")
            print(f"首次运行: {command} start")
            print("然后在微信发送 /help 或普通消息。")
    elif update:
        print("重启已有服务: systemctl --user restart codex-wechat")
        print("若未使用服务，先退出原前台进程，再运行 codex-wechat start。")
    else:
        print("1. 确认 Codex 已安装并登录。")
        print(f"2. 在终端运行 {target / 'codex-wechat'} start，保持此终端打开。")
        print("3. 在微信发送 /help、/status，然后发送普通消息开始交流。")
        print("自启默认关闭；需要时在微信发送 /settings autostart on。")
        print("若提示找不到 codex-wechat，请使用上面的完整路径，或将 ~/.local/bin 加入 PATH。")


def install(source: Path, target: Path, *, dry_run: bool = False) -> int:
    return _common_install(source, target, dry_run=dry_run, update=False)


def update(source: Path, target: Path, *, dry_run: bool = False) -> int:
    return _common_install(source, target, dry_run=dry_run, update=True)


def update_source(repo: str, ref: str = "main") -> dict:
    """An explicit public GitHub source, never inferred from the cfg remote."""
    parsed = urlsplit(repo)
    path = parsed.path.strip("/").removesuffix(".git")
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
            or parsed.query or parsed.fragment or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", path)):
        raise ValueError("更新来源须为公开 GitHub 仓库的 HTTPS 地址，不接受凭据、参数或本地路径。")
    if (not isinstance(ref, str) or not ref or ref.startswith("-")
            or any(char.isspace() or ord(char) < 32 for char in ref)):
        raise ValueError("更新 ref 必须是有效的分支或标签名称。")
    return {"repo": f"https://github.com/{path}.git", "ref": ref}


def saved_update_source(target: Path) -> dict | None:
    path = target / UPDATE_SOURCE_FILE
    if path.is_symlink():
        raise ValueError(f"更新来源不能是符号链接: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取更新来源: {path}") from error
    if not isinstance(value, dict) or not isinstance(value.get("repo"), str):
        raise ValueError("保存的更新来源格式无效，请显式指定 --repo。")
    return update_source(value["repo"], value.get("ref", "main"))


def remember_update_source(
    target: Path, source: Path, choice: dict, *, previous: str | None = None
) -> None:
    identifier = release_identifier(project_files(source))
    # Cancellation returns 0 too; never save a source for a cancelled install.
    if read_pointer(target, CURRENT_FILE) != identifier:
        return
    # A same-version rerun is a no-op (or only repairs stable entry files).
    # Do not silently change the saved source when one is already recorded.
    if (previous == identifier
            and (target / UPDATE_SOURCE_FILE).exists()):
        return
    with InstallLock(target):
        if read_pointer(target, CURRENT_FILE) == identifier:
            if (previous == identifier
                    and (target / UPDATE_SOURCE_FILE).exists()):
                return
            _atomic_write(target / UPDATE_SOURCE_FILE,
                          (json.dumps(choice, ensure_ascii=False) + "\n").encode(),
                          0o600, root=target)


def fetch_update_source(choice: dict, destination: Path) -> None:
    git = shutil.which("git")
    if not git:
        raise ValueError("在线更新需要 Git；请安装 Git，或下载新源码后用 update --source {目录}。")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Public updates do not need stored credentials or checkout hooks.
    subprocess.run([
        git, "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
        "clone", "--depth", "1", "--single-branch", "--branch", choice["ref"],
        "--", choice["repo"], str(destination),
    ], env=env, check=True, timeout=180)


def remote_update(target: Path, choice: dict, *, dry_run: bool = False) -> int:
    phase("在线更新")
    print(f"来源: {choice['repo']}")
    print(f"分支或标签: {choice['ref']}")
    if dry_run:
        detail("因 --dry-run，真实安装目录检查按通过处理；不访问网络或修改文件。")
        operation_plan(["获取指定来源源码", "校验源码并构建新版本", "切换版本并保存更新来源"])
        print("演练未获取远程版本，不代表该来源或新版本已通过校验。")
        return 0
    _check_target_path(target)
    if _installation_state(target) is None:
        raise ValueError("目标尚未安装 codex-wechat；请先使用 install。")
    previous = read_pointer(target, CURRENT_FILE)
    with tempfile.TemporaryDirectory(prefix="codex-wechat-update-") as directory:
        source = Path(directory) / "source"
        fetch_update_source(choice, source)
        # Use the installed updater, not an unconfirmed remote installer script.
        result = update(source, target)
        remember_update_source(target, source, choice, previous=previous)
        return result


def bootstrap(target: Path, choice: dict, *, dry_run: bool = False) -> int:
    """One entry for first installation and repeated installation/update."""
    phase("安装或更新")
    print(f"来源: {choice['repo']} ({choice['ref']})")
    if dry_run:
        return _common_install(SOURCE_ROOT, target, dry_run=True, update=False)
    _check_target_path(target)
    previous = read_pointer(target, CURRENT_FILE)
    updating = _installation_state(target) is not None
    # Reject unrelated files before any network or install side effect.
    check_install_target(None, target, update=updating)
    print("检测到已有安装，将执行更新。" if updating else "未检测到已有安装，将执行首次安装。")
    with tempfile.TemporaryDirectory(prefix="codex-wechat-bootstrap-") as directory:
        source = Path(directory) / "source"
        fetch_update_source(choice, source)
        result = _common_install(source, target, dry_run=False, update=updating)
        remember_update_source(target, source, choice, previous=previous)
        return result


def declaration(source: Path, stage: str) -> None:
    path = asset_path(source, DECLARATION_PATHS[stage])
    if path is not None:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            print(text)


def report_dependencies(source: Path, *, dry_run: bool = False) -> None:
    if sys.version_info < (3, 11):
        if not dry_run:
            raise ValueError("安装需要 Python 3.11+，请补齐后重跑。")
        detail("Python 版本不足 3.11；因 --dry-run 放行。")
    for name, command, minimum, purpose, policy in DEPENDENCIES:
        if (IS_WINDOWS and command in ("qrencode", "systemd-run")):
            continue
        executable = shutil.which(command)
        if not executable and name == "Codex CLI" and IS_WINDOWS:
            local = os.environ.get("LOCALAPPDATA")
            candidate = Path(local) / "Programs/OpenAI/Codex/bin/codex.exe" if local else None
            if candidate and candidate.is_file():
                executable = str(candidate)
        ready = bool(executable)
        if ready and minimum and not dry_run:
            try:
                result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5)
                version = re.search(r"\b(\d+)(?:\.(\d+))?", result.stdout)
                actual = tuple(int(value or 0) for value in version.groups()) if version else ()
                ready = result.returncode == 0 and actual[:len(minimum)] >= minimum
            except (OSError, subprocess.SubprocessError):
                ready = False
        if not ready:
            if policy == "block" and not dry_run:
                raise ValueError(f"{name} 缺失或版本不足，请补齐后重跑。")
            detail(f"{name} 缺失或版本不足，{purpose}暂不可用；{'演练继续' if dry_run else '可稍后补齐'}。")
        elif dry_run and minimum:
            detail(f"{name}: 已找到；版本检查因 --dry-run 跳过。")
    requirements = source / "requirements.txt"
    if requirements.is_file():
        detail("Python 依赖将从 requirements.txt 安装到程序独立环境。")
    elif dry_run:
        detail("requirements.txt 缺失；因 --dry-run 放行。")


def component_install(source: Path, name: str, skills_dir: Path, *, dry_run: bool = False) -> int:
    if source != SOURCE_ROOT and not dry_run:
        logo_banner(source)
        declaration(source, "start")
    label, relative = COMPONENTS[name]
    content = source / relative
    destination = skills_dir.expanduser().absolute() / name / "SKILL.md"
    print(f"{label}: {destination}")
    if dry_run:
        detail("因 --dry-run，来源及目标检查按通过处理；不安装组件。")
        return 0
    if not content.is_file() or content.is_symlink():
        raise ValueError(f"组件源码缺失: {content}")
    data = content.read_bytes()
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != data:
            raise ValueError(f"目标已有不同内容，保留原文件: {destination}")
        print("组件已安装，没有修改。")
        return 0
    if not confirm("安装此组件？"):
        return 0
    # Atomic file replacement never leaves half a skill, even on a full disk.
    created_directories = []
    parent = destination.parent
    while not parent.exists():
        created_directories.append(parent)
        parent = parent.parent
    try:
        _atomic_write(destination, data, 0o644, root=skills_dir.expanduser().absolute())
    finally:
        if not destination.exists():
            for parent in created_directories:
                try:
                    parent.rmdir()
                except OSError:
                    break
    print("组件已安装；Codex 将按请求需要读取 skill。")
    return 0


def menu_choice() -> str:
    print("1. 安装 / 更新\n2. 附加组件\n0. 退出")
    while True:
        try:
            answer = input("选择 [1]: ").strip()
        except EOFError:
            return "exit"
        if answer in ("", "1"):
            return "install"
        if answer == "2":
            return "components"
        if answer == "0":
            return "exit"
        print("请输入 0、1 或 2。")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="安装 / 更新 codex-wechat 或安装附加组件。")
    result.add_argument("command", nargs="?", default="menu", choices=("menu", "install", "update", "bootstrap", "components"))
    result.add_argument("--dir", type=Path)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--non-interactive", action="store_true", help="执行指定操作，保留保守默认，不询问、不启动服务或安装额外组件")
    result.add_argument("--repo", help="公开 GitHub HTTPS 仓库")
    result.add_argument("--ref", help="分支或标签")
    result.add_argument("--source", type=Path, help="本地源码，不联网")
    result.add_argument("--component", choices=tuple(COMPONENTS))
    result.add_argument("--skills-dir", type=Path, default=Path.home() / ".agents/skills")
    return result


def main(argv: list[str] | None = None) -> int:
    global NON_INTERACTIVE
    options = parser().parse_args(argv)
    if options.command == "bootstrap" and not options.repo:
        raise ValueError("一行安装入口需要 --repo；不会使用旧仓库。")
    if options.source is not None and (options.repo or options.ref):
        raise ValueError("--source 不能与 --repo / --ref 同时使用。")
    if options.ref and not options.repo:
        raise ValueError("--ref 需要同时指定 --repo。")
    previous_mode = NON_INTERACTIVE
    NON_INTERACTIVE = options.non_interactive
    try:
        command = options.command
        if options.source is None and not options.repo:
            logo_banner(SOURCE_ROOT)
            declaration(SOURCE_ROOT, "start")
        if command == "bootstrap":
            # The network entry offers exactly the same menu as the local entry.
            command = "menu"
        if command == "menu":
            if options.non_interactive:
                command = "install"
            elif not (sys.stdin.isatty() and sys.stdout.isatty()) and not options.dry_run:
                raise ValueError("交互安装需要终端；自动化请显式使用 --non-interactive。")
            else:
                command = menu_choice()
        if command == "exit":
            return 0
        if not options.non_interactive and not options.dry_run and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise ValueError("交互安装需要终端；自动化请显式使用 --non-interactive。")
        target = (options.dir or default_target()).expanduser()
        if command == "components":
            name = options.component
            if name is None:
                if options.non_interactive:
                    raise ValueError("非交互组件安装需要 --component。")
                for number, (key, (label, _)) in enumerate(COMPONENTS.items(), 1):
                    print(f"{number}. {label} ({key})")
                print("0. 退出")
                answer = input("选择 [0]: ").strip()
                if answer in ("", "0"):
                    return 0
                keys = list(COMPONENTS)
                if not answer.isdigit() or not 1 <= int(answer) <= len(keys):
                    raise ValueError("组件编号无效")
                name = keys[int(answer) - 1]
            source = options.source or SOURCE_ROOT
            if options.source is None and not options.repo and not options.dry_run:
                if SOURCE_ROOT == target or target / RELEASES_DIR in SOURCE_ROOT.parents:
                    identifier = read_pointer(target, CURRENT_FILE)
                    if identifier:
                        source = release_path(target, identifier)
            if options.repo:
                choice = update_source(options.repo, options.ref or "main")
                if options.dry_run:
                    return component_install(source, name, options.skills_dir, dry_run=True)
                with tempfile.TemporaryDirectory(prefix="codex-wechat-component-") as directory:
                    source = Path(directory) / "source"
                    fetch_update_source(choice, source)
                    return component_install(source, name, options.skills_dir)
            return component_install(source, name, options.skills_dir, dry_run=options.dry_run)
        if options.component or options.skills_dir != Path.home() / ".agents/skills":
            raise ValueError("组件参数只适用于 components。")
        if not options.dry_run and target.is_symlink():
            raise ValueError(f"安装目录不能是符号链接: {target}")
        target = target.absolute() if options.dry_run else target.resolve()
        if not options.dry_run and not IS_WINDOWS and target != default_target().resolve():
            raise ValueError(f"Linux 安装目录必须是 {default_target()}")
        if options.repo:
            return bootstrap(target, update_source(options.repo, options.ref or "main"), dry_run=options.dry_run)
        if options.ref:
            raise ValueError("--ref 需要同时指定 --repo。")
        installed_entry = SOURCE_ROOT == target or target / RELEASES_DIR in SOURCE_ROOT.parents
        if options.source is None and installed_entry and not options.dry_run and (command == "update" or read_pointer(target, CURRENT_FILE)):
            choice = saved_update_source(target)
            if choice:
                return bootstrap(target, choice, dry_run=options.dry_run)
            if not options.dry_run:
                raise ValueError("尚未配置更新来源，请指定 --repo 或 --source。")
        source = options.source or SOURCE_ROOT
        updating = False if options.dry_run else _installation_state(target) is not None
        return (update if updating else install)(source, target, dry_run=options.dry_run)
    finally:
        NON_INTERACTIVE = previous_mode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninstaller.py: 已取消", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"installer.py: {error}", file=sys.stderr)
        raise SystemExit(1)
