#!/usr/bin/env python3
"""Install immutable codex-wechat releases and switch them atomically."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unicodedata
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
LOGO_PATH = Path("assets/logo/codex-wechat.txt")
DECLARATION_PATHS = {
    "start": Path("assets/install/after-art.txt"),
    "complete": Path("assets/install/after-install.txt"),
}
NON_INTERACTIVE = False
# Generous: pip on a slow mirror may legitimately take minutes, but a hung
# command must not block the installer forever.
INSTALL_COMMAND_TIMEOUT_SECONDS = 900
GIT_ARCHIVE_TIMEOUT_SECONDS = 120
SOURCE_ARCHIVE_TIMEOUT_SECONDS = 180
SOURCE_ARCHIVE_MAX_BYTES = 256 * 1024 * 1024
SOURCE_TREE_MAX_BYTES = 512 * 1024 * 1024
SOURCE_TREE_MAX_FILES = 20_000
UV_VERSION = "0.13.7"
UV_PYTHON = "3.11"
UV_BOOTSTRAP_ENV = "CODEX_WECHAT_BOOTSTRAP_UV"
UV_DIR = Path(".bootstrap/uv")
UV_PYTHON_DIR = Path(".bootstrap/python")
UV_CACHE_DIR = Path(".bootstrap/cache")
BOOTSTRAP_VENV = Path(".bootstrap-venv")
DEPENDENCIES = (
    ("Codex runtime", "codex.exe" if IS_WINDOWS else "codex", None, "微信模型任务", "warn"),
    ("curl", "curl.exe" if IS_WINDOWS else "curl", (8, 4), "网络测速", "warn"),
    ("qrencode", "qrencode", None, "原生终端二维码（缺少时使用轻量回退）", "warn"),
    ("systemd-run", "systemd-run", (254,), "Linux 共享后台", "warn"),
)
COMPONENTS = {
    "wechat-attachments": ("微信附件说明 skill", Path("skills/wechat-attachments/SKILL.md")),
    "codex-wechat-guide": ("微信使用说明 skill", Path("skills/codex-wechat-guide/SKILL.md")),
}
RELEASE_FORMAT = 2
PREVIOUS_FILE = "previous"
RELEASES_DIR = "releases"
ACTIVE_FILE = "active-release"
SERVICE_UNIT = "codex-wechat.service"
PACKAGE_ROOT = Path("codex_wechat")
LAYOUT_CONTRACT_FILE = PACKAGE_ROOT / "install_layout.py"
# A directory handle held for a moment - by a scanner or a just-exited process -
# blocks the rename that makes a release deletable; one retry covers it.
PRUNE_RETRY_SECONDS = 0.2
# The same transient handle can block publishing a freshly prepared staging
# tree. Windows reports the rename as access denied, so retry with backoff.
STAGING_REPLACE_ATTEMPTS = 6
STAGING_REPLACE_RETRY_SECONDS = 0.25
LOCK_FILE = ".install-lock"
MANIFEST_FILE = ".release.json"
UPDATE_SOURCE_FILE = ".update-source.json"
PRIVATE_NAMES = {
    ".git",
    ".state",
    ".update-backups",
    ".venv",
    ".bootstrap",
    ".bootstrap-venv",
    "__pycache__",
    "dist",
    "experiments",
    "htmlcov",
    "coverage.xml",
    RELEASES_DIR,
    "workspace",
    "shared",
    "logs",
    PREVIOUS_FILE,
    LOCK_FILE,
    UPDATE_SOURCE_FILE,
    ACTIVE_FILE,
}

REQUIRED_FILES = {
    Path(".gitignore"),
    Path("LICENSE"),
    Path("bootstrap_supervisor.py"),
    Path("codex-wechat"),
    Path("codex-wechat.ps1"),
    PACKAGE_ROOT / "__init__.py",
    PACKAGE_ROOT / "app.py",
    PACKAGE_ROOT / "bootstrap_supervisor.py",
    PACKAGE_ROOT / "install_layout.py",
    PACKAGE_ROOT / "launcher.py",
    PACKAGE_ROOT / "platform_support.py",
    PACKAGE_ROOT / "state.py",
    PACKAGE_ROOT / "windows_service.py",
    Path("config.default.json"),
    Path("install.sh"),
    Path("install.ps1"),
    Path("installer.py"),
    Path("pyproject.toml"),
    Path("windows/wmi_launch.py"),
    Path("windows/task-service.ps1"),
}
STABLE_LINUX_FILES = (
    Path("codex-desktop"),
    Path("codex-wechat"),
    Path("codex-wechat.service"),
    Path("config.default.json"),
    Path("installer.py"),
)
STABLE_WINDOWS_FILES = (
    Path("codex-desktop.ps1"),
    Path("install.ps1"),
    Path("installer.py"),
    Path("bootstrap_supervisor.py"),
    Path("codex-wechat.ps1"),
    Path("uninstall.ps1"),
    Path("config.default.json"),
    Path("windows/Start-SharedBackend.ps1"),
    Path("windows/wmi_launch.py"),
    Path("windows/task-service.ps1"),
)
STABLE_DIRECTORIES = (Path("skills"),)


def styled(text: str, code: str) -> str:
    if (sys.stdout.isatty()
            and "NO_COLOR" not in os.environ
            and os.environ.get("TERM") != "dumb"):
        return f"\033[{code}m{text}\033[0m"
    return text


def phase(text: str) -> None:
    print(styled("::", "1;34") + " " + styled(text, "1"), flush=True)


def detail(text: str) -> None:
    print("  " + styled("->", "2") + " " + text)


def display_width(text: str) -> int:
    """Count terminal cells; Chinese characters occupy two columns."""
    return sum(
        0 if unicodedata.combining(char) else
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def confirm(prompt: str) -> bool:
    if NON_INTERACTIVE:
        return True
    while True:
        try:
            answer = input(
                styled("::", "1;34") + " " + styled(prompt, "1")
                + " " + styled("[Y/n]", "1") + " "
            ).strip().casefold()
        except EOFError:
            print("未收到输入，已取消。")
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            print("已取消，没有继续修改。")
            return False
        print("请输入 y 或 n。")


def introduction() -> bool:
    phase("开始之前")
    width = min(76, shutil.get_terminal_size(fallback=(80, 24)).columns)
    print(styled(textwrap.fill(
        "codex-wechat 是独立项目，用于把微信私聊连接到本机 Codex。"
        "安装会创建一份不可变程序版本，并保留已有的微信登录、配置、"
        "待办、会话和工作目录；不会复制 Codex 凭据或会话历史。",
        width=width,
    ), "33"))
    print()
    print(styled(textwrap.fill(
        "本项目为第三方独立开发，与 OpenAI、腾讯公司（微信）及其关联方"
        "没有隶属、合作或背书关系，也未获其授权；"
        "相关名称与商标归各自权利人所有。",
        width=width,
    ), "33"))
    print()
    print(styled(textwrap.fill(
        "本项目以 Apache License 2.0 开源发布。"
        "安装和使用前请阅读并遵守 LICENSE；第三方名称和商标的权利边界见"
        " TRADEMARKS.md。",
        width=width,
    ), "1;31"))
    print()
    print(styled(textwrap.fill(
        "接下来展示操作计划，确认后执行；不启用新自启、默认不自动启动服务、不安装附加组件。",
        width=width,
    ), "2"))
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


def install_layout(source: Path):
    """Load the same installed-layout contract that the release will execute."""
    path = Path(source) / LAYOUT_CONTRACT_FILE
    name = "_codex_wechat_install_layout_contract"
    existing = sys.modules.get(name)
    if existing is not None and Path(existing.__file__ or "").resolve() == path.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"安装布局契约缺失: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def installed_layout(source: Path, target: Path, *, required=False):
    return install_layout(source).InstalledLayout.load(
        target,
        platform="windows" if IS_WINDOWS else "linux",
        required=required,
    )


def installed_release_id(source: Path, target: Path) -> str | None:
    layout = installed_layout(source, target)
    return layout.release_id if layout is not None else None


def available_installed_release_id(source: Path, target: Path) -> str | None:
    if not (Path(source) / LAYOUT_CONTRACT_FILE).is_file():
        return None
    return installed_release_id(source, target)


def _private(relative: Path) -> bool:
    if relative in (Path("config.json"), Path("config.json.tmp")):
        return True
    for part in relative.parts:
        if part in PRIVATE_NAMES:
            return True
        if part.startswith(".") and part != ".gitignore":
            return True
    return relative.suffix.casefold() in (".pyc", ".pyo")


def _worktree_files(source: Path) -> dict[Path, tuple[bytes, int]]:
    """Read a source snapshot that has no Git checkout metadata."""
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
    return files


def _archive_files(data: bytes, *, strip_root: bool) -> dict[Path, tuple[bytes, int]]:
    """Validate a tar archive and return its regular files without extracting it."""
    files: dict[Path, tuple[bytes, int]] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as bundle:
        members = bundle.getmembers()
        root_name = None
        if strip_root:
            roots = {
                PurePosixPath(member.name.rstrip("/")).parts[0]
                for member in members
                if member.name.rstrip("/")
            }
            if len(roots) != 1:
                raise ValueError("源码归档必须只有一个顶层目录")
            root_name = next(iter(roots))
        for member in members:
            raw_name = member.name.rstrip("/")
            posix = PurePosixPath(raw_name)
            if (not raw_name or posix.is_absolute() or ".." in posix.parts
                    or "\\" in raw_name or ":" in raw_name):
                raise ValueError(f"源码归档包含不安全路径: {member.name}")
            parts = posix.parts
            if strip_root:
                if parts[0] != root_name:
                    raise ValueError("源码归档必须只有一个顶层目录")
                parts = parts[1:]
                if not parts:
                    continue
            relative = Path(*parts)
            if member.isdir() or _private(relative):
                continue
            if not member.isfile():
                raise ValueError(f"源码归档中不是普通文件: {relative}")
            if relative in files:
                raise ValueError(f"源码归档包含重复路径: {relative}")
            if member.size < 0 or len(files) >= SOURCE_TREE_MAX_FILES:
                raise ValueError("源码归档超过文件数量上限")
            total += member.size
            if total > SOURCE_TREE_MAX_BYTES:
                raise ValueError("源码归档展开后超过大小上限")
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError(f"无法读取源码归档文件: {relative}")
            files[relative] = (stream.read(), member.mode & 0o777)
    return files


def _git_archive_files(source: Path) -> dict[Path, tuple[bytes, int]]:
    """Read exactly HEAD, so untracked worktree files cannot enter a release."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=source,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"无法运行 Git 检查源码: {error}") from error
    if status.returncode:
        raise ValueError(f"无法读取源码 Git 状态: {status.stderr.strip() or status.returncode}")
    if status.stdout.strip():
        raise ValueError("源码有未提交的已跟踪修改；请先提交，再构建 release")
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=source,
            capture_output=True,
            timeout=GIT_ARCHIVE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"无法运行 Git 构建源码快照: {error}") from error
    if archive.returncode:
        detail = archive.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"无法读取源码 Git 提交: {detail or archive.returncode}")
    return _archive_files(archive.stdout, strip_root=False)


def project_files(source: Path, *, require_complete=True) -> dict[Path, tuple[bytes, int]]:
    """Read distributable files without following private or public symlinks."""
    source = source.expanduser()
    if source.is_symlink():
        raise ValueError(f"源码目录不能是符号链接: {source}")
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"源码目录不存在: {source}")
    files = _git_archive_files(source) if (source / ".git").exists() else _worktree_files(source)
    missing = sorted(str(path) for path in REQUIRED_FILES if path not in files)
    if missing and require_complete:
        raise ValueError("源码缺少必要文件: " + ", ".join(missing))
    return files


def check_runtime_compatibility(files, config):
    """Validate capability declarations, never import downloaded project code."""
    backends = config.get("backends", {})
    if not isinstance(backends, dict):
        raise ValueError("backends 必须是对象")
    definitions = list(backends.values())
    required = any(isinstance(item, dict) and item.get("websocketUrl")
                   for item in definitions)
    if not required:
        return
    declaration = files.get(PACKAGE_ROOT / "codex_connection.py") if files else None
    try:
        module = ast.parse(declaration[0]) if declaration else None
        values = [ast.literal_eval(node.value) for node in module.body
                  if isinstance(node, ast.Assign) and any(
                      isinstance(target, ast.Name) and target.id == "SUPPORTED_TRANSPORTS"
                      for target in node.targets)] if module else []
        capabilities = values[0] if len(values) == 1 else ()
    except (SyntaxError, ValueError, TypeError):
        raise ValueError("源码的连接能力声明无效") from None
    if not isinstance(capabilities, tuple) or "loopback-websocket" not in capabilities:
        raise ValueError("当前配置使用共享 WebSocket，但待安装版本不支持；未修改安装。")


def check_config_compatibility(files, layout):
    if layout is None:
        return
    path = layout.runtime_paths().config_file
    if not path.exists():
        return
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("运行配置必须是 JSON 对象")
    check_runtime_compatibility(files, config)


def release_identifier(files: dict[Path, tuple[bytes, int]]) -> str:
    digest = hashlib.sha256()
    platform_tag = "windows" if IS_WINDOWS else sys.platform
    digest.update(
        f"codex-wechat-release-{RELEASE_FORMAT}-{platform_tag}\0".encode())
    for relative in sorted(files, key=lambda value: value.as_posix()):
        content, mode = files[relative]
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        if not IS_WINDOWS:
            digest.update(str(mode & 0o777).encode("ascii"))
            digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def wheel_identifier(wheel: Path) -> str:
    digest = hashlib.sha256(Path(wheel).read_bytes()).hexdigest()
    return digest[:16]


def build_wheel(target: Path, source: Path, output: Path) -> Path:
    """Build the application distribution with uv; stable assets stay outside it."""
    output.mkdir(parents=True, exist_ok=True)
    _run_uv(
        target,
        ["build", "--no-cache", "--wheel", "--out-dir", str(output), str(source)],
        cwd=source,
    )
    wheels = sorted(output.glob("codex_wechat-*.whl"))
    if len(wheels) != 1:
        raise ValueError("uv 未生成唯一的 codex-wechat wheel")
    return wheels[0]


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


def _readable(path: Path) -> bool:
    """A real access check: Windows denies reads without failing stat()."""
    try:
        if path.is_dir():
            with os.scandir(path) as entries:
                next(entries, None)
        else:
            with path.open("rb"):
                pass
    except OSError:
        return False
    return True


def check_user_access(
    target: Path,
    identifier: str,
    source: Path,
    layout,
) -> None:
    """The service starts as the installing user, so entries must be readable.

    A restricted or elevated sandbox can create files with a private DACL: the
    install looks successful while `pythonw` fails to load the entry, and the
    scheduled task logs nothing. Detect that here instead.
    """
    entries = [target / relative for relative in _stable_files(source)]
    entries += [layout.manifest_path, release_path(target, identifier)]
    denied = [path for path in entries if not _readable(path)]
    if denied:
        remedy = (f'icacls "{target}" /reset /t /c /q' if IS_WINDOWS
                  else f'chmod -R u+rX "{target}"')
        raise ValueError(
            "当前用户无法读取安装入口，服务会以该用户身份启动并静默失败:\n"
            + "\n".join("  " + str(path) for path in denied)
            + "\n请在普通（非管理员、非沙箱）终端重新安装，或以管理员执行: "
            + remedy)


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


def check_install_target(
    source: Path | None,
    target: Path,
    *,
    update: bool,
    contract,
) -> None:
    _check_target_path(target)
    layout = contract.InstalledLayout.load(
        target,
        platform="windows" if IS_WINDOWS else "linux",
    )
    if source == target:
        return
    if update:
        if layout is None:
            raise ValueError(
                f"目标不是现有 codex-wechat 安装: {target}\n"
                "全新安装请使用 install。"
            )
        return
    if layout is not None:
        raise ValueError(
            f"目标已有 codex-wechat 安装: {target}\n"
            "现有安装在保留数据的前提下升级，请使用 update。"
        )
    if target.exists() and any(
            path.name != LOCK_FILE for path in target.iterdir()):
        raise ValueError(f"安装目录非空: {target}")


def release_path(target: Path, identifier: str) -> Path:
    if not identifier.isascii() or not all(
            char.isalnum() or char in "._-" for char in identifier):
        raise ValueError(f"版本标识无效: {identifier}")
    return target / RELEASES_DIR / identifier


def release_matches(target: Path, identifier: str) -> bool:
    if target.is_symlink():
        return False
    releases = target / RELEASES_DIR
    if releases.is_symlink():
        return False
    root = release_path(target, identifier)
    if root.is_symlink() or not root.is_dir():
        return False
    manifest_path = root / MANIFEST_FILE
    if manifest_path.is_symlink():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(manifest, dict) or manifest.get("schema") != RELEASE_FORMAT:
        return False
    if manifest.get("id") != identifier or manifest.get("platform") != sys.platform:
        return False
    wheel = manifest.get("wheel")
    return isinstance(wheel, str) and wheel.endswith(".whl")


def _write_release_manifest(staging: Path, identifier: str, wheel: Path) -> None:
    manifest = {
        "schema": RELEASE_FORMAT,
        "id": identifier,
        "platform": sys.platform,
        "wheel": Path(wheel).name,
    }
    _atomic_write(
        staging / MANIFEST_FILE,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        0o600,
        root=staging,
    )


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    detail("执行: " + " ".join(command))
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True,
                       timeout=INSTALL_COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"安装命令超过 {INSTALL_COMMAND_TIMEOUT_SECONDS // 60} 分钟未完成，"
            "请检查网络后重试。") from None


def _venv_python(root: Path) -> Path:
    if IS_WINDOWS:
        return root / ".venv/Scripts/python.exe"
    return root / ".venv/bin/python"


def _uv_executable(target: Path) -> Path:
    name = "uv.exe" if IS_WINDOWS else "uv"
    return target / UV_DIR / UV_VERSION / name


def _uv_environment(target: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "UV_CACHE_DIR": str((target / UV_CACHE_DIR).absolute()),
        "UV_MANAGED_PYTHON": "1",
        "UV_NO_CONFIG": "1",
        "UV_NO_MODIFY_PATH": "1",
        "UV_NO_PROGRESS": "1",
        "UV_PYTHON": UV_PYTHON,
        "UV_PYTHON_INSTALL_BIN": "0",
        "UV_PYTHON_INSTALL_DIR": str((target / UV_PYTHON_DIR).absolute()),
    })
    return environment


def _uv_version_matches(executable: Path) -> bool:
    if not executable.is_file():
        return False
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    fields = result.stdout.strip().split()
    return result.returncode == 0 and fields[:2] == ["uv", UV_VERSION]


def ensure_uv(target: Path) -> Path:
    """Persist the bootstrap-provided uv binary inside the installation root."""
    destination = _uv_executable(target)
    if _uv_version_matches(destination):
        return destination
    candidate = os.environ.get(UV_BOOTSTRAP_ENV) or shutil.which("uv")
    if not candidate:
        raise ValueError("uv 不可用；请重新运行 bootstrap.sh / bootstrap.ps1。")
    source = Path(candidate).expanduser()
    if not source.is_file():
        raise ValueError(f"bootstrap uv 不存在: {source}")
    if not _uv_version_matches(source):
        raise ValueError(f"bootstrap uv 版本不匹配，需要 {UV_VERSION}: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, temporary)
    if not IS_WINDOWS:
        temporary.chmod(0o700)
    os.replace(temporary, destination)
    return destination


def _run_uv(target: Path, arguments: list[str], *, cwd: Path) -> None:
    _run(
        [str(ensure_uv(target)), *arguments],
        cwd=cwd,
        env=_uv_environment(target),
    )


def _managed_python(target: Path) -> Path:
    uv = ensure_uv(target)
    try:
        result = subprocess.run(
            [str(uv), "python", "find", "--managed-python", UV_PYTHON],
            cwd=target,
            env=_uv_environment(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"无法查找 uv 管理的 Python: {error}") from error
    value = result.stdout.strip()
    if result.returncode or not value:
        raise ValueError(f"uv 管理的 Python {UV_PYTHON} 不可用: {result.stderr.strip()}")
    python = Path(value)
    if not python.is_file():
        raise ValueError(f"uv 管理的 Python 不存在: {python}")
    return python


def ensure_managed_python(target: Path) -> Path:
    uv = ensure_uv(target)
    _run(
        [str(uv), "python", "install", UV_PYTHON],
        cwd=target,
        env=_uv_environment(target),
    )
    return _managed_python(target)


RUNTIME_CHECK_TIMEOUT_SECONDS = 30
RUNTIME_IMPORTS = (
    "codex_wechat",
    "codex_wechat.desktop",
    "curl_cffi",
    "curl_cffi.requests",
    "websockets",
    "websockets.asyncio.client",
    "websockets.exceptions",
    "cryptography",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.ciphers",
)


def _runtime_imports() -> tuple[str, ...]:
    if IS_WINDOWS:
        return (*RUNTIME_IMPORTS, "win32api", "win32event", "win32job")
    return RUNTIME_IMPORTS


def _runtime_python(root: Path) -> Path:
    return _venv_python(root)


def _runtime_check_code() -> str:
    return "import " + ", ".join(_runtime_imports()) + "\n"


def _runtime_python_works(root: Path) -> bool:
    python = _runtime_python(root)
    if not python.is_file():
        return False
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=RUNTIME_CHECK_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def release_runtime_ready(root: Path) -> bool:
    """Check the release-local environment, not only the stable bootstrap."""
    if not _runtime_python_works(root):
        return False
    python = _runtime_python(root)
    try:
        result = subprocess.run(
            [str(python), "-c", _runtime_check_code()],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=RUNTIME_CHECK_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def prepare_runtime(root: Path, wheel: Path) -> None:
    """Create a release-local environment and install the application wheel."""
    target = root.parent.parent if root.parent.name == RELEASES_DIR else root
    venv = root / ".venv"
    if venv.is_symlink():
        raise ValueError(f"版本 Python 环境不能是符号链接: {venv}")
    if venv.exists():
        raise ValueError(f"版本暂存环境已存在: {venv}")
    _run_uv(target, [
        "venv", "--no-project", "--managed-python", "--python", UV_PYTHON,
        str(venv),
    ], cwd=root)
    python = _runtime_python(root)
    if not python.is_file():
        raise ValueError(f"虚拟环境创建失败: {python}")
    _run_uv(
        target,
        ["pip", "install", "--python", str(python), str(wheel)],
        cwd=root,
    )
    _run([
        str(python),
        "-c",
        _runtime_check_code() + "print(\"Python dependencies installed\")\n",
    ], cwd=root)


def _bootstrap_python(target: Path) -> Path:
    if IS_WINDOWS:
        return target / BOOTSTRAP_VENV / "Scripts/python.exe"
    return target / BOOTSTRAP_VENV / "bin/python"


def bootstrap_runtime_ready(target: Path) -> bool:
    python = _bootstrap_python(target)
    if not python.is_file():
        return False
    command = [
        str(python),
        "-I",
        "-c",
        "import sys; assert sys.version_info >= (3, 11); "
        "import codex_wechat.launcher, codex_wechat.uninstall",
    ]
    if IS_WINDOWS:
        command[3] += "; import win32api, win32event, win32job; import codex_wechat.windows_service"
    try:
        result = subprocess.run(
            command, cwd=target, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=RUNTIME_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def prepare_bootstrap_runtime(target: Path, wheel: Path) -> None:
    root = target / BOOTSTRAP_VENV
    if root.is_symlink():
        raise ValueError(f"稳定入口环境不能是符号链接: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"稳定入口环境不是目录: {root}")
    created = not root.exists()
    _run_uv(target, [
        "venv", "--no-project", "--managed-python", "--python", UV_PYTHON,
        str(root),
    ], cwd=target)
    python = _bootstrap_python(target)
    try:
        if not python.is_file():
            raise ValueError(f"稳定入口环境创建失败: {python}")
        _run([str(python), "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"], cwd=target)
        _run_uv(target, [
            "pip", "install", "--python", str(python), "--reinstall", str(wheel),
        ], cwd=target)
        if not bootstrap_runtime_ready(target):
            raise ValueError("稳定入口环境安装后不可用")
    except BaseException:
        if created and root.exists():
            shutil.rmtree(root)
        raise


def _ensure_stable_runtime(
    target: Path,
    wheel: Path,
    *,
    create_runtime: bool,
) -> None:
    ensure_uv(target)
    ensure_managed_python(target)
    if bootstrap_runtime_ready(target):
        return
    if not create_runtime:
        raise ValueError("稳定入口 Python 环境不完整；请停止服务后重新运行 bootstrap。")
    prepare_bootstrap_runtime(target, wheel)



def _staging_directory(parent: Path, prefix: str) -> Path:
    """Create a staging directory that inherits the parent's access rules.

    tempfile.mkdtemp creates its directory with mode 0700, which Windows turns
    into an owner-only DACL; `os.replace` then moves that DACL onto the
    installed entries and locks the service account out.
    """
    for _ in range(10):
        candidate = parent / (prefix + os.urandom(8).hex())
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise ValueError(f"无法在 {parent} 创建暂存目录")


def _publish_staging(staging: Path, final: Path) -> bool:
    """Rename a prepared staging tree into place.

    Windows reports ERROR_ACCESS_DENIED both when a scanner or a just-exited
    dependency process still holds the staging tree and when another installer
    has already published the same content. Only the latter is a benign race.
    """
    for attempt in range(STAGING_REPLACE_ATTEMPTS):
        try:
            os.replace(staging, final)
            return True
        except PermissionError as error:
            if final.is_dir():
                return False
            if attempt + 1 == STAGING_REPLACE_ATTEMPTS:
                raise ValueError(
                    f"无法启用新版本 {final.name}：暂存目录仍被占用或没有访问权限；"
                    "请稍后重试（Windows 上可暂时关闭安全软件扫描）。") from error
            time.sleep(STAGING_REPLACE_RETRY_SECONDS * (2 ** attempt))
    raise AssertionError("unreachable")


def install_release(
    target: Path,
    identifier: str,
    wheel: Path,
) -> tuple[Path, bool]:
    releases = target / RELEASES_DIR
    if releases.is_symlink():
        raise ValueError(f"版本目录不能是符号链接: {releases}")
    releases.mkdir(parents=True, exist_ok=True)
    final = release_path(target, identifier)
    if final.exists():
        if not release_matches(target, identifier):
            raise ValueError(f"现有版本清单与标识不一致: {final}")
        if not release_runtime_ready(final):
            raise ValueError(f"现有版本 Python 环境不完整: {final}")
        return final, False
    staging = _staging_directory(releases, ".staging-")
    try:
        prepare_runtime(staging, wheel)
        _write_release_manifest(staging, identifier, wheel)
        if not _publish_staging(staging, final):
            if not release_matches(target, identifier):
                raise ValueError(f"现有版本清单与标识不一致: {final}")
            return final, False
        return final, True
    finally:
        if staging.exists():
            # A failed cleanup must not hide why publishing failed.
            shutil.rmtree(staging, ignore_errors=True)


def _stable_files(source: Path) -> tuple[Path, ...]:
    entries = STABLE_WINDOWS_FILES if IS_WINDOWS else STABLE_LINUX_FILES
    return tuple(entries)


def stable_files_match(source: Path, target: Path) -> bool:
    try:
        files = _stable_files(source)
    except (OSError, ValueError):
        return False
    for relative in files:
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
    staging = _staging_directory(target, ".entry-")
    try:
        files = _stable_files(source)
        for relative in files:
            source_path = source / relative
            destination = target / relative
            if not source_path.is_file() or source_path.is_symlink() or destination.is_symlink():
                raise ValueError(f"安装入口不是普通文件: {relative}")
            _atomic_write(staging / relative, source_path.read_bytes(),
                          stat.S_IMODE(source_path.stat().st_mode), root=staging)
        for relative in files:
            destination = target / relative
            if destination.parent.is_symlink():
                raise ValueError(f"安装入口目录不能是符号链接: {destination.parent}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / relative, destination)
        for relative in STABLE_DIRECTORIES:
            source_path = source / relative
            if source_path.is_symlink() or not source_path.is_dir():
                raise ValueError(f"安装资源目录无效: {relative}")
            for path in source_path.rglob("*"):
                if path.is_symlink():
                    raise ValueError(f"安装资源不是普通文件: {path.relative_to(source)}")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise ValueError(f"安装资源不是普通文件: {path.relative_to(source)}")
                rel = path.relative_to(source)
                destination = target / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(destination, path.read_bytes(),
                              stat.S_IMODE(path.stat().st_mode), root=target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def prune_releases(target: Path, *, keep, report=None) -> list[str]:
    """Delete stale releases, but never the one a running bridge marked active."""
    releases = Path(target) / RELEASES_DIR
    if releases.is_symlink() or not releases.is_dir():
        return []
    kept = {name for name in keep if name}
    active_file = Path(target) / ACTIVE_FILE
    try:
        active = active_file.read_text(encoding="utf-8").strip()
    except OSError:
        active = None
    if active:
        kept.add(active)
    for stale in releases.glob(".trash-*"):
        shutil.rmtree(stale, ignore_errors=True)
    candidates = [entry for entry in releases.iterdir()
                  if not entry.name.startswith(".") and entry.is_dir()
                  and not entry.is_symlink()]
    removed = []
    for entry in sorted(candidates, key=lambda item: item.name):
        if entry.name in kept:
            continue
        trash = releases / (".trash-" + entry.name)
        for attempt in range(2):
            try:
                os.replace(entry, trash)
            except OSError:
                if attempt:
                    if report:
                        report(f"跳过旧版本 {entry.name}（仍被占用，下次更新再删）")
                    continue
                time.sleep(PRUNE_RETRY_SECONDS)
            else:
                shutil.rmtree(trash, ignore_errors=True)
                removed.append(entry.name)
                break
    return removed


def _linux_command_link() -> Path:
    return Path.home() / ".local/bin/codex-wechat"


# A .ps1 on PATH is gated by the execution policy and is invisible to cmd.exe,
# so the Windows entry is a .cmd shim next to the Linux one.
WINDOWS_SHIM_NAME = "codex-wechat.cmd"
WINDOWS_SHIM_MARKER = "rem codex-wechat command shim"


def _windows_local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def windows_shim_text(entry: Path) -> str:
    """Call the stable PowerShell entry with the policy bypass it needs."""
    quoted = str(entry).replace('"', '""')
    return (
        "@echo off\r\n"
        f"{WINDOWS_SHIM_MARKER}; generated by the installer, safe to delete\r\n"
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{quoted}" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )


def path_entry_matches(directory, path_value) -> bool:
    """Compare one Windows PATH entry without depending on the host OS."""
    def normalize(value):
        text = str(value).strip().strip('"')
        text = re.sub(
            r"%([^%]+)%",
            lambda match: os.environ.get(match.group(1), match.group(0)),
            text,
        )
        return (text.replace("/", "\\")
                .rstrip("\\").casefold())

    wanted = normalize(directory)
    return any(normalize(item) == wanted for item in str(path_value or "").split(";") if item.strip())


def _windows_path_value() -> str:
    """The PATH a new shell will see, not the installer's own copy of it."""
    if IS_WINDOWS:
        try:
            import winreg
        except ImportError:
            return os.environ.get("PATH", "")
        parts = []
        hkey_current_user = getattr(winreg, "HKEY_CURRENT_USER")
        hkey_local_machine = getattr(winreg, "HKEY_LOCAL_MACHINE")
        open_key = getattr(winreg, "OpenKey")
        query_value = getattr(winreg, "QueryValueEx")
        expand = getattr(winreg, "ExpandEnvironmentStrings", None)
        for root, subkey in ((hkey_current_user, "Environment"),
                             (hkey_local_machine,
                              r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with open_key(root, subkey) as key:
                    value, _ = query_value(key, "Path")
            except OSError:
                continue
            if isinstance(value, str):
                if expand is not None:
                    value = expand(value)
                value = os.path.expandvars(value)
                parts.append(value)
        if parts:
            return ";".join(parts)
    return os.environ.get("PATH", "")


def windows_command_state(target: Path) -> tuple[Path, str, str]:
    """Report where the shim would go and whether it can be installed."""
    local_bin = _windows_local_bin()
    shim = local_bin / WINDOWS_SHIM_NAME
    entry = target / "codex-wechat.ps1"
    if not local_bin.is_dir():
        return shim, "missing-directory", (
            f"未找到 {local_bin}，跳过命令入口；"
            f"请用完整路径运行 {entry}")
    if not path_entry_matches(local_bin, _windows_path_value()):
        return shim, "not-on-path", (
            f"{local_bin} 不在用户 PATH 中，跳过命令入口（安装器不会修改 PATH）；"
            f"把该目录加入 PATH 后重新运行安装器即可获得短命令，当前请用完整路径运行 {entry}")
    if shim.exists():
        try:
            text = shim.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return shim, "unreadable", f"无法读取 {shim}，跳过命令入口"
        if WINDOWS_SHIM_MARKER not in text:
            return shim, "foreign", f"{shim} 已存在且不是本项目的命令入口，请先自行处理"
    return shim, "ready", f"命令入口: {shim}"


def preflight_command_link(target: Path) -> None:
    if IS_WINDOWS:
        _, status, message = windows_command_state(target)
        if status == "foreign":
            raise ValueError(message)
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
        shim, status, message = windows_command_state(target)
        detail(message)
        if status != "ready":
            return
        text = windows_shim_text(target / "codex-wechat.ps1")
        if shim.is_file():
            with shim.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
                if stream.read() == text:
                    return
        shim.write_text(text, encoding="utf-8", newline="")
        return
    link = _linux_command_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    expected = (target / "codex-wechat").resolve()
    if link.is_symlink() and link.resolve() == expected:
        return
    link.symlink_to(os.path.relpath(expected, link.parent))


def configure_windows(release: Path, target: Path) -> None:
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
        str(target / "install.ps1"),
        "-InstallRoot",
        str(target),
    ]
    command.append("-PreparedRuntime")
    _run(command, cwd=release)


def _linux_service_snapshot(systemctl: str, unit: Path) -> tuple[str, str]:
    try:
        result = subprocess.run(
            [systemctl, "--user", "show", unit.name,
             "--property=LoadState", "--property=FragmentPath",
             "--property=ActiveState"],
            cwd=unit.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable", ""
    if result.returncode:
        return "unavailable", ""
    fields = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    fragment = fields.get("FragmentPath", "")
    if not fragment:
        state = "missing" if fields.get("LoadState") == "not-found" else "unavailable"
        return state, fields.get("ActiveState", "")
    try:
        registered = Path(fragment).resolve()
    except OSError:
        return "unavailable", ""
    state = "owned" if registered == unit.resolve() else "foreign"
    return state, fields.get("ActiveState", "")


def _linux_service_state(systemctl: str, unit: Path) -> str:
    return _linux_service_snapshot(systemctl, unit)[0]


def configure_linux_service(target: Path) -> bool:
    """Register the user unit without enabling or starting it."""
    if IS_WINDOWS:
        return False
    unit = target / SERVICE_UNIT
    if not unit.is_file() or unit.is_symlink():
        raise ValueError(f"服务模板不存在: {unit}")
    systemctl = shutil.which("systemctl")
    if not systemctl:
        detail("未找到 systemctl，跳过后台服务注册；仍可使用前台 start。")
        return False
    state = _linux_service_state(systemctl, unit)
    if state == "unavailable":
        detail("systemd 用户服务不可用，跳过后台服务注册；仍可使用前台 start。")
        return False
    if state == "foreign":
        raise ValueError("当前用户已有不属于本安装目录的 codex-wechat.service，未修改。")
    if state == "missing":
        _run([systemctl, "--user", "link", str(unit.resolve())], cwd=target)
    _run([systemctl, "--user", "daemon-reload"], cwd=target)
    return True


def configure_platform_service(release: Path, target: Path) -> bool:
    if IS_WINDOWS:
        configure_windows(release, target)
        return True
    return configure_linux_service(target)


def operation_plan(operations: list[str]) -> None:
    width = max(map(display_width, operations), default=1) + 4
    for number, operation in enumerate(operations, 1):
        padding = " " * max(0, width - display_width(operation))
        print(
            styled(f"({number}/{len(operations)})", "2")
            + f" {operation}{padding}"
            + styled("待执行", "2")
        )


def _common_install(
    source: Path,
    target: Path,
    *,
    dry_run: bool,
    update: bool,
    announce: bool = True,
    repair_stable_runtime: bool = False,
) -> int:
    source = _resolved_root(source, "源码目录")
    target = target.expanduser().absolute() if dry_run else _resolved_root(target, "安装目录")
    contract = None
    if (source / LAYOUT_CONTRACT_FILE).is_file():
        contract = install_layout(source)
    elif not dry_run:
        raise ValueError(f"安装布局契约缺失: {source / LAYOUT_CONTRACT_FILE}")
    existing = None
    if not dry_run:
        if contract is None:
            raise ValueError(f"安装布局契约缺失: {source / LAYOUT_CONTRACT_FILE}")
        check_install_target(source, target, update=update, contract=contract)
        existing = contract.InstalledLayout.load(
            target,
            platform="windows" if IS_WINDOWS else "linux",
        )
    if source != SOURCE_ROOT:
        logo_banner(source)
        declaration(source, "start")
    if announce:
        phase("演练模式，不会修改文件" if dry_run else ("更新" if update else "安装"))
    try:
        files = project_files(source)
    except (OSError, ValueError) as error:
        if not dry_run:
            raise
        detail(f"源码检查: {error}；因 --dry-run 放行。")
        files = None
    try:
        check_config_compatibility(files, existing)
    except (OSError, ValueError) as error:
        if not dry_run:
            raise
        detail(f"运行能力检查: {error}；因 --dry-run 放行。")
    identifier = "待构建 wheel"
    planned = None
    wheel_directory = None
    wheel = None
    current = existing.release_id if existing is not None else None
    if dry_run:
        detail("因 --dry-run，真实安装目录与命令入口检查按通过处理；不代表实际安装条件已满足。")
    else:
        if contract is None:
            raise ValueError(f"安装布局契约缺失: {source / LAYOUT_CONTRACT_FILE}")
        preflight_command_link(target)
        if existing is not None:
            try:
                planned = contract.InstalledLayout.planned(
                    target,
                    existing.release_id,
                    platform="windows" if IS_WINDOWS else "linux",
                    existing=existing,
                )
                wheel_directory = tempfile.TemporaryDirectory(
                    prefix="codex-wechat-wheel-")
                wheel = build_wheel(target, source, Path(wheel_directory.name))
                identifier = wheel_identifier(wheel)
                planned = contract.InstalledLayout.planned(
                    target,
                    identifier,
                    platform="windows" if IS_WINDOWS else "linux",
                    existing=existing,
                )
                if current == identifier and stable_files_match(source, target):
                    _ensure_stable_runtime(
                        target,
                        wheel,
                        create_runtime=repair_stable_runtime,
                    )
                    if not release_runtime_ready(target / RELEASES_DIR / identifier):
                        raise ValueError(
                            f"版本 {identifier} 的 Python 环境不完整；请重新安装。")
                    install_command_link(target)
                    wheel_directory.cleanup()
                    print(f"版本 {identifier} 已经安装，没有修改。")
                    return 0
            except BaseException:
                if wheel_directory is not None:
                    wheel_directory.cleanup()
                raise
    if not introduction():
        if wheel_directory is not None:
            wheel_directory.cleanup()
        return 0
    report_dependencies(source, dry_run=dry_run)
    operation_plan(["准备程序与 Python 依赖", "集中更新入口及当前版本", "保留配置、登录与工作内容", "清理临时文件与旧版本"])
    print(f"安装目录: {target}")
    print(f"版本: {identifier}")
    if not confirm("执行更新？" if update else "执行安装？"):
        if wheel_directory is not None:
            wheel_directory.cleanup()
        return 0
    if dry_run:
        print("演练完成，没有修改文件。")
        declaration(source, "complete")
        return 0
    if files is None or contract is None:
        # Not an assert: -O would drop the check and let a None manifest through.
        raise ValueError("安装清单尚未准备完成，未修改安装。")

    created_target = not target.exists()
    release = None
    created = False
    service_ready = False
    layout_published = False
    try:
        sys.path.insert(0, str(source))
        try:
            from codex_wechat.platform_support import make_private
        finally:
            sys.path.pop(0)
        make_private(target)
        with InstallLock(target):
            check_install_target(source, target, update=update, contract=contract)
            fresh_files = project_files(source)
            if fresh_files != files:
                raise ValueError("确认期间源码发生变化，请重新运行")
            locked_layout = contract.InstalledLayout.load(
                target,
                platform="windows" if IS_WINDOWS else "linux",
            )
            locked_release = locked_layout.release_id if locked_layout is not None else None
            if locked_release != current:
                raise ValueError("确认期间当前版本发生变化，请重新运行")
            if wheel is None:
                wheel_directory = tempfile.TemporaryDirectory(
                    prefix="codex-wechat-wheel-")
                phase("构建 application wheel...")
                wheel = build_wheel(target, source, Path(wheel_directory.name))
                identifier = wheel_identifier(wheel)
                planned = contract.InstalledLayout.planned(
                    target,
                    identifier,
                    platform="windows" if IS_WINDOWS else "linux",
                    existing=existing,
                )
            if wheel is None or planned is None:
                raise ValueError("wheel 或安装布局尚未准备完成")
            _ensure_stable_runtime(
                target,
                wheel,
                create_runtime=existing is None or repair_stable_runtime,
            )
            if (current == identifier
                    and release_matches(target, identifier)
                    and stable_files_match(source, target)):
                install_command_link(target)
                print(f"版本 {identifier} 已经安装，没有修改。")
                return 0
            phase("准备 release 环境...")
            release, created = install_release(target, identifier, wheel)
            # Everything requiring downloads/builds/user input finishes above.
            phase("更新入口与当前版本...")
            install_bootstrap(source, target)
            install_command_link(target)
            planned.publish()
            layout_published = True
            if existing is None:
                if release is None:
                    raise ValueError("release 尚未安装")
                service_ready = configure_platform_service(release, target)
            else:
                service_ready = True
            (target / PREVIOUS_FILE).unlink(missing_ok=True)
            check_user_access(target, identifier, source, planned)
            try:
                pruned = prune_releases(target, keep=(identifier, current))
            except OSError as error:
                detail(f"旧版本清理跳过: {error}")
            else:
                for name in pruned:
                    detail(f"已移除旧版本: {name}")
    except BaseException:
        # Only delete a release this attempt created but did not select.
        try:
            active_identifier = installed_release_id(source, target)
        except (OSError, ValueError):
            active_identifier = None
        if created and release is not None and active_identifier != identifier:
            shutil.rmtree(release)
        # A target created by this attempt is disposable until the contract is
        # published. Remove it as a whole so first install cannot leave roots
        # or a release without a selected layout.
        if created_target and not layout_published:
            shutil.rmtree(target, ignore_errors=True)
        elif not created_target:
            # Never remove a directory the user already owned; only clear what a
            # failed attempt can leave behind, and only while it is still empty.
            try:
                (target / LOCK_FILE).unlink(missing_ok=True)
                (target / RELEASES_DIR).rmdir()
                target.rmdir()
            except OSError:
                pass
        raise
    finally:
        if wheel_directory is not None:
            wheel_directory.cleanup()
    print(styled("==> 更新完成。" if update else "==> 安装完成。", "1;32"))
    declaration(source, "complete")
    if (service_ready and not NON_INTERACTIVE
            and sys.stdin.isatty() and sys.stdout.isatty()
            and confirm_start()):
        if start_installed(target, update=update):
            return 0
    next_steps(
        target,
        update=update,
        identifier=identifier,
        service_ready=service_ready,
    )
    return 0


def confirm_start() -> bool:
    """Offer the common post-install action without making it mandatory."""
    if NON_INTERACTIVE:
        return False
    while True:
        try:
            answer = input("立即启动？[Y/n] ").strip().casefold()
        except EOFError:
            print("未收到输入，已跳过启动。")
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("请输入 y 或 n。")


def service_start_command(target: Path, *, update: bool) -> list[str] | None:
    action = "service-restart" if update else "service-start"
    if IS_WINDOWS:
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            return None
        return [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(target / "codex-wechat.ps1"),
            action,
        ]
    return [str(target / "codex-wechat"), action]


def start_installed(target: Path, *, update: bool) -> bool:
    command = service_start_command(target, update=update)
    if command is None:
        print("启动未完成：找不到 PowerShell；安装本身已完成。")
        return False
    try:
        result = subprocess.run(command, cwd=target, check=False)
    except OSError as error:
        print(f"启动未完成：{error}；安装本身已完成。")
        return False
    if result.returncode:
        print(f"启动未完成（退出码 {result.returncode}）；安装本身已完成。")
        return False
    return True


def next_steps(
    target: Path,
    *,
    update: bool,
    identifier: str,
    service_ready: bool = True,
) -> None:
    print()
    phase("下一步")
    print(f"版本: {identifier}")
    print()
    if IS_WINDOWS:
        if windows_command_state(target)[1] == "ready":
            command = "codex-wechat"
        else:
            entry = str(target / "codex-wechat.ps1").replace('"', '""')
            command = (
                f'powershell -NoProfile -ExecutionPolicy Bypass '
                f'-File "{entry}"'
            )
        if update:
            print(f"1. 重启后台服务: {command} service-restart")
            print("2. 在微信发送 /status 确认连接。")
        else:
            print("1. 确认 Codex 已安装并登录。")
            print(f"2. 首次运行并扫码: {command} service-start")
            print("3. 在微信发送 /help 或 /status，然后发送普通消息。")
    elif update:
        if service_ready:
            print("1. 若此前使用前台 start，请先退出。")
            print("2. 重启后台服务: codex-wechat service-restart")
            print("3. 在微信发送 /status 确认连接。")
            print()
            print("现有自启状态保持不变。")
        else:
            print("后台服务未注册；请先退出原前台进程，再运行 codex-wechat start。")
    else:
        print("1. 确认 Codex 已安装并登录。")
        if service_ready:
            print("2. 运行并扫码: codex-wechat service-start")
        else:
            print("2. 在当前终端运行并扫码: codex-wechat start")
            print("   后台服务未注册；请保持终端运行。")
        print("3. 在微信发送 /help 或 /status，然后发送普通消息。")
    if not update:
        print()
        print("可选功能:")
        print("- 微信中发送 /settings autostart true 打开开机自启")


def install(
    source: Path,
    target: Path,
    *,
    dry_run: bool = False,
) -> int:
    return _common_install(
        source,
        target,
        dry_run=dry_run,
        update=False,
    )


def update(
    source: Path,
    target: Path,
    *,
    dry_run: bool = False,
    repair_stable_runtime: bool = False,
) -> int:
    return _common_install(
        source,
        target,
        dry_run=dry_run,
        update=True,
        repair_stable_runtime=repair_stable_runtime,
    )


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
    identifier = installed_release_id(source, target)
    if identifier is None:
        return
    # Cancellation returns 0 too; never save a source for a cancelled install.
    if installed_release_id(source, target) != identifier:
        return
    # A same-version rerun is a no-op (or only repairs stable entry files).
    # Do not silently change the saved source when one is already recorded.
    if (previous == identifier
            and (target / UPDATE_SOURCE_FILE).exists()):
        return
    with InstallLock(target):
        if installed_release_id(source, target) == identifier:
            if (previous == identifier
                    and (target / UPDATE_SOURCE_FILE).exists()):
                return
            _atomic_write(target / UPDATE_SOURCE_FILE,
                          (json.dumps(choice, ensure_ascii=False) + "\n").encode(),
                          0o600, root=target)


def fetch_update_source(choice: dict, destination: Path) -> None:
    parsed = urlsplit(choice["repo"])
    repository = parsed.path.strip("/").removesuffix(".git")
    url = (
        f"https://codeload.github.com/{repository}/tar.gz/"
        f"{quote(choice['ref'], safe='')}"
    )
    request = Request(url, headers={"User-Agent": "codex-wechat-installer"})
    try:
        with urlopen(request, timeout=SOURCE_ARCHIVE_TIMEOUT_SECONDS) as response:
            archive = response.read(SOURCE_ARCHIVE_MAX_BYTES + 1)
    except (OSError, ValueError) as error:
        raise ValueError(f"无法下载源码归档: {error}") from error
    if len(archive) > SOURCE_ARCHIVE_MAX_BYTES:
        raise ValueError("源码归档超过下载大小上限")
    files = _archive_files(archive, strip_root=True)
    if destination.exists():
        raise ValueError(f"源码下载目录已存在: {destination}")
    destination.mkdir(parents=True)
    for relative, (data, mode) in sorted(files.items()):
        _atomic_write(destination / relative, data, mode, root=destination)


def bootstrap(
    target: Path,
    choice: dict,
    *,
    dry_run: bool = False,
) -> int:
    """One entry for first installation and repeated installation/update."""
    if dry_run:
        print(f"来源: {choice['repo']} ({choice['ref']})")
        phase("演练模式，不会修改文件")
        return _common_install(
            SOURCE_ROOT,
            target,
            dry_run=True,
            update=False,
            announce=False,
        )
    _check_target_path(target)
    with tempfile.TemporaryDirectory(prefix="codex-wechat-bootstrap-") as directory:
        source = Path(directory) / "source"
        fetch_update_source(choice, source)
        contract = install_layout(source)
        layout = contract.InstalledLayout.load(
            target,
            platform="windows" if IS_WINDOWS else "linux",
        )
        updating = layout is not None
        previous = layout.release_id if layout is not None else None
        phase("更新" if updating else "安装")
        print(f"来源: {choice['repo']} ({choice['ref']})")
        check_install_target(source, target, update=updating, contract=contract)
        print("检测到已有安装，将执行更新。" if updating else "未检测到已有安装，将执行首次安装。")
        result = _common_install(
            source,
            target,
            dry_run=False,
            update=updating,
            announce=False,
            repair_stable_runtime=updating,
        )
        remember_update_source(target, source, choice, previous=previous)
        return result


def declaration(source: Path, stage: str) -> None:
    path = asset_path(source, DECLARATION_PATHS[stage])
    if path is not None:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            print(text)


def _discover_runtime(source: Path):
    # The network entry downloads this single stdlib-only installer first.
    # Once the full source is fetched, reuse its native discovery implementation:
    # it only reads PATH, LOCALAPPDATA and the registered App, so a dry run can
    # report the same answer without writing files or using the network.
    fallback = shutil.which("codex.exe" if IS_WINDOWS else "codex")
    helper = source / PACKAGE_ROOT / "native_codex.py"
    if not helper.is_file() or helper.is_symlink():
        return fallback
    # A dry run must not leave files behind, including a bytecode cache.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("codex_wechat_native_discovery", helper)
        if spec is None or spec.loader is None:
            return fallback
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.discover_codex(windows=IS_WINDOWS)
    except Exception:
        # An unusable helper must not hide an interpreter that PATH already has.
        return fallback
    finally:
        sys.dont_write_bytecode = previous


def report_dependencies(source: Path, *, dry_run: bool = False) -> None:
    if sys.version_info < (3, 11):
        if not dry_run:
            raise ValueError("安装需要 Python 3.11+，请补齐后重跑。")
        detail("Python 版本不足 3.11；因 --dry-run 放行。")
    for name, command, minimum, purpose, policy in DEPENDENCIES:
        if (IS_WINDOWS and command in ("qrencode", "systemd-run")):
            continue
        executable = (_discover_runtime(source) if name == "Codex runtime"
                      else shutil.which(command))
        ready = bool(executable)
        if ready and executable is not None and minimum and not dry_run:
            try:
                result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=5)
                version = re.search(r"\b(\d+)(?:\.(\d+))?", result.stdout)
                actual = tuple(int(value or 0) for value in version.groups()) if version else ()
                ready = result.returncode == 0 and actual[:len(minimum)] >= minimum
            except (OSError, subprocess.SubprocessError):
                ready = False
        if not ready:
            if policy == "block" and not dry_run:
                raise ValueError(f"{name} 缺失或版本不足，请补齐后重跑。")
            detail(f"{name} 缺失或版本不足，{purpose}未就绪；{'演练继续' if dry_run else '可稍后补齐'}。")
        elif dry_run and minimum:
            detail(f"{name}: 已找到；版本检查因 --dry-run 跳过。")
    declarations = source / "pyproject.toml"
    if declarations.is_file():
        detail("uv 将从 pyproject.toml 构建 wheel 并准备程序环境。")
    elif dry_run:
        detail("pyproject.toml 缺失；因 --dry-run 放行。")


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


def _elevated() -> bool:
    """An elevated installer would create files owned by the wrong account."""
    if os.name != "nt":
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # a locked-down shell may refuse the call itself
        return False


def main(argv: list[str] | None = None) -> int:
    global NON_INTERACTIVE
    options = parser().parse_args(argv)
    if _elevated() and os.environ.get("CODEX_WECHAT_ALLOW_ROOT") != "1":
        raise ValueError("请以普通用户身份运行，不要使用 root/sudo 或管理员终端。")
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
                    identifier = available_installed_release_id(SOURCE_ROOT, target)
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
            return bootstrap(
                target,
                update_source(options.repo, options.ref or "main"),
                dry_run=options.dry_run,
            )
        if options.ref:
            raise ValueError("--ref 需要同时指定 --repo。")
        installed_entry = SOURCE_ROOT == target or target / RELEASES_DIR in SOURCE_ROOT.parents
        current_identifier = (
            available_installed_release_id(SOURCE_ROOT, target)
            if installed_entry and not options.dry_run
            else None
        )
        if options.source is None and installed_entry and not options.dry_run and (
                command == "update" or current_identifier):
            choice = saved_update_source(target)
            if choice:
                return bootstrap(
                    target,
                    choice,
                    dry_run=options.dry_run,
                )
            if not options.dry_run:
                raise ValueError(
                    "尚未配置更新来源；若通过私有网络入口安装，请重跑同一网络入口，"
                    "或用 --source 指定本地源码。")
        source = options.source or SOURCE_ROOT
        updating = (
            False if options.dry_run
            else installed_layout(source, target) is not None
        )
        if updating:
            return update(
                source,
                target,
                dry_run=options.dry_run,
                repair_stable_runtime=(
                    command == "install" and options.source is not None
                ),
            )
        return install(source, target, dry_run=options.dry_run)
    finally:
        NON_INTERACTIVE = previous_mode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninstaller.py: 已取消", file=sys.stderr)
        raise SystemExit(130) from None
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"installer.py: {error}", file=sys.stderr)
        raise SystemExit(1) from error
