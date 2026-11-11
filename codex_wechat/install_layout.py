"""The installed-layout contract shared by installer and runtime."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Mapping


SCHEMA_VERSION = 1
INSTALLATION_FILE = ".installation.json"
LEGACY_FILES = (
    "current",
    ".runtime-roots",
    ".data-root",
    ".config-root",
    ".state-root",
)
RELEASE_ID = re.compile(r"[a-f0-9]{16}")
PLATFORMS = ("linux", "windows")


class InstallLayoutError(ValueError):
    """An installation manifest or planned layout is invalid."""


@dataclass(frozen=True)
class RuntimePaths:
    """Persistent storage locations using their configured, unresolved paths."""

    data_root: Path
    config_file: Path
    state_dir: Path
    workspace_dir: Path
    logs_dir: Path
    shared_dir: Path


def runtime_paths_for(
    platform: str,
    data_root: Path,
    config_root: Path,
    state_root: Path,
) -> RuntimePaths:
    """Map validated roots to the per-platform runtime locations."""
    if platform == "windows":
        return RuntimePaths(
            data_root=data_root,
            config_file=data_root / "config.json",
            state_dir=data_root / ".state",
            workspace_dir=data_root / "workspace",
            logs_dir=data_root / "logs",
            shared_dir=data_root / "shared",
        )
    return RuntimePaths(
        data_root=data_root,
        config_file=config_root / "config.json",
        state_dir=state_root,
        workspace_dir=data_root / "workspace",
        logs_dir=state_root / "logs",
        shared_dir=state_root / "shared",
    )


@dataclass(frozen=True)
class OwnedPath:
    """One application-owned filesystem leaf selected for deletion."""

    role: str
    path: Path


@dataclass(frozen=True)
class PurgePlan:
    """Explicit application-owned paths; the data roots are never purged."""

    items: tuple[OwnedPath, ...]

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.items)


def current_platform() -> str:
    return "windows" if os.name == "nt" else "linux"


def _absolute_path(value, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise InstallLayoutError(f"{label}必须是绝对路径")
    text = os.fspath(value)
    if not text or "\x00" in text or "\n" in text or "\r" in text:
        raise InstallLayoutError(f"{label}内容无效")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise InstallLayoutError(f"{label}必须是绝对路径: {path}")
    return path


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _is_link(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return bool(
        path.is_symlink()
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _symlink_component(path: Path) -> Path | None:
    path = Path(path).expanduser()
    for component in (path, *path.parents):
        if _is_link(component):
            return component
    return None


def default_roots(
    platform: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or Path.home()).expanduser()
    override = env.get("CODEX_WECHAT_DATA_ROOT")
    if platform == "windows":
        local = env.get("LOCALAPPDATA")
        if not local:
            raise InstallLayoutError("Windows 缺少 LOCALAPPDATA，无法确定数据目录")
        data = (Path(override).expanduser().absolute() if override
                else Path(local) / "codex-wechat")
        return data, data, data

    def xdg(name: str, fallback: Path) -> Path:
        value = env.get(name)
        if value and Path(value).is_absolute():
            return Path(value).expanduser()
        return fallback

    data = (Path(override).expanduser().absolute() if override
            else xdg("XDG_DATA_HOME", home / ".local/share") / "codex-wechat")
    config = xdg("XDG_CONFIG_HOME", home / ".config") / "codex-wechat"
    state = xdg("XDG_STATE_HOME", home / ".local/state") / "codex-wechat"
    return data, config, state


def validate_roots(
    platform: str,
    install_root: Path,
    data_root: Path,
    config_root: Path,
    state_root: Path,
) -> None:
    try:
        install = install_root.resolve()
        roots = {
            "数据根": data_root.resolve(),
            "配置根": config_root.resolve(),
            "状态根": state_root.resolve(),
        }
        home = Path.home().resolve()
    except (OSError, RuntimeError) as error:
        raise InstallLayoutError(f"无法解析运行时路径布局: {error}") from error

    for role, root in roots.items():
        if root in {Path(root.anchor), home} or home.is_relative_to(root):
            raise InstallLayoutError(f"{role}不能是用户目录或其祖先: {root}")

    if platform == "windows":
        if config_root != data_root or state_root != data_root:
            raise InstallLayoutError("Windows 配置根和状态根必须等于数据根")
        return

    for role in ("配置根", "状态根"):
        if _paths_overlap(roots[role], install):
            raise InstallLayoutError(f"{role}与程序安装目录重叠: {roots[role]}")
    for first, second in (("数据根", "配置根"), ("数据根", "状态根"),
                          ("配置根", "状态根")):
        if _paths_overlap(roots[first], roots[second]):
            raise InstallLayoutError(
                f"{first}与{second}不能重叠: {roots[first]} / {roots[second]}"
            )
    workspace = (roots["数据根"] / "workspace").resolve()
    if workspace == install or install.is_relative_to(workspace):
        raise InstallLayoutError(f"工作区不能包含程序安装目录: {workspace}")


@dataclass(frozen=True)
class InstalledLayout:
    """One validated installation record; every installed path comes from here."""

    install_root: Path
    release_id: str
    platform: str
    data_root: Path
    config_root: Path
    state_root: Path

    @classmethod
    def create(
        cls,
        install_root,
        release_id: str,
        *,
        platform: str,
        data_root,
        config_root,
        state_root,
    ) -> InstalledLayout:
        layout = cls(
            install_root=_absolute_path(install_root, "安装根"),
            release_id=release_id,
            platform=platform,
            data_root=_absolute_path(data_root, "数据根"),
            config_root=_absolute_path(config_root, "配置根"),
            state_root=_absolute_path(state_root, "状态根"),
        )
        layout._validate()
        return layout

    @classmethod
    def planned(
        cls,
        install_root,
        release_id: str,
        *,
        platform: str | None = None,
        existing: InstalledLayout | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> InstalledLayout:
        root = _absolute_path(install_root, "安装根")
        target_platform = current_platform() if platform is None else platform
        if target_platform not in PLATFORMS:
            raise InstallLayoutError(f"不支持的平台: {target_platform}")
        if not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
            raise InstallLayoutError(f"版本标识无效: {release_id}")
        if existing is None:
            data, config, state = default_roots(target_platform, environ=environ)
        else:
            data, config, state = (
                existing.data_root,
                existing.config_root,
                existing.state_root,
            )
            override = (os.environ if environ is None else environ).get(
                "CODEX_WECHAT_DATA_ROOT"
            )
            if override and Path(override).expanduser().absolute().resolve() != data.resolve():
                raise InstallLayoutError(
                    "CODEX_WECHAT_DATA_ROOT 与已安装布局不一致；"
                    "已安装实例的数据根固定，请清除环境变量"
                )
        return cls.create(
            install_root=root,
            release_id=release_id,
            platform=target_platform,
            data_root=data,
            config_root=config,
            state_root=state,
        )

    def _validate(self) -> None:
        if self.platform not in PLATFORMS:
            raise InstallLayoutError(f"不支持的平台: {self.platform}")
        if not isinstance(self.release_id, str) or not RELEASE_ID.fullmatch(self.release_id):
            raise InstallLayoutError(f"版本标识无效: {self.release_id}")
        if _is_link(self.install_root):
            raise InstallLayoutError(f"安装根目录不能是符号链接: {self.install_root}")
        validate_roots(
            self.platform,
            self.install_root,
            self.data_root,
            self.config_root,
            self.state_root,
        )

    @property
    def manifest_path(self) -> Path:
        return self.install_root / INSTALLATION_FILE

    @property
    def release_path(self) -> Path:
        return self.install_root / "releases" / self.release_id

    @property
    def python_executable(self) -> Path:
        if self.platform == "windows":
            return self.release_path / ".venv/Scripts/python.exe"
        return self.release_path / ".venv/bin/python"

    def release_dir(self) -> Path:
        """Return the selected immutable release, rejecting links."""
        releases = self.install_root / "releases"
        if _is_link(releases):
            raise InstallLayoutError(f"版本目录不能是符号链接: {releases}")
        release = self.release_path
        if _is_link(release):
            raise InstallLayoutError(f"当前版本目录不能是符号链接: {release}")
        if not release.is_dir():
            raise InstallLayoutError(f"当前版本目录不完整: {release}")
        return release

    def runtime_paths(self) -> RuntimePaths:
        return runtime_paths_for(
            self.platform,
            self.data_root,
            self.config_root,
            self.state_root,
        )

    def purge_plan(self) -> PurgePlan:
        runtime = self.runtime_paths()
        if self.platform == "windows":
            candidates = (
                OwnedPath("配置", self.data_root / "config.json"),
                OwnedPath("配置临时文件", self.data_root / "config.json.tmp"),
                OwnedPath("状态", self.data_root / ".state"),
                OwnedPath("工作区", self.data_root / "workspace"),
                OwnedPath("日志", self.data_root / "logs"),
                OwnedPath("共享后台", self.data_root / "shared"),
            )
        else:
            candidates = (
                OwnedPath("配置", runtime.config_file),
                OwnedPath("配置临时文件",
                          runtime.config_file.with_name(runtime.config_file.name + ".tmp")),
                OwnedPath("状态", runtime.state_dir),
                OwnedPath("工作区", runtime.workspace_dir),
            )
        install = self.install_root.resolve()
        home = Path.home().resolve()
        for item in candidates:
            component = _symlink_component(item.path)
            if component is not None:
                raise InstallLayoutError(
                    f"拒绝删除经过符号链接的{item.role}路径: {item.path}（{component}）"
                )
            try:
                target = item.path.resolve()
            except (OSError, RuntimeError) as error:
                raise InstallLayoutError(f"无法解析{item.role}路径: {item.path}") from error
            if target in {Path(target.anchor), home} or home.is_relative_to(target):
                raise InstallLayoutError(f"拒绝删除范围过大的{item.role}目录: {target}")
            if target == install or install.is_relative_to(target):
                raise InstallLayoutError(f"拒绝删除包含程序安装目录的{item.role}路径: {target}")
        return PurgePlan(candidates)

    def as_json(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "platform": self.platform,
            "installRoot": str(self.install_root),
            "releaseId": self.release_id,
            "dataRoot": str(self.data_root),
            "configRoot": str(self.config_root),
            "stateRoot": str(self.state_root),
        }

    def publish(self) -> None:
        self._validate()
        release = self.release_dir()
        if not (release / "codex_wechat/app.py").is_file():
            raise InstallLayoutError(f"当前版本不完整: {release}")
        path = self.manifest_path
        if _is_link(path):
            raise InstallLayoutError(f"安装清单不能是符号链接: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.as_json(), stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def remove(self) -> None:
        path = self.manifest_path
        if _is_link(path):
            raise InstallLayoutError(f"安装清单不能是符号链接: {path}")
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @classmethod
    def load(
        cls,
        install_root=None,
        *,
        platform: str | None = None,
        required: bool = False,
    ) -> InstalledLayout | None:
        root = _absolute_path(install_root or Path.cwd(), "安装根")
        if _is_link(root):
            raise InstallLayoutError(f"安装根目录不能是符号链接: {root}")
        legacy = [name for name in LEGACY_FILES
                  if _is_link(root / name) or (root / name).exists()]
        if legacy:
            raise InstallLayoutError(
                "检测到旧安装布局，当前版本不提供原地迁移；"
                f"请备份后全新安装（残留: {', '.join(legacy)}）"
            )
        path = root / INSTALLATION_FILE
        if not path.exists() and not path.is_symlink():
            if required:
                raise InstallLayoutError(f"安装清单缺失: {path}")
            return None
        if _is_link(path) or not path.is_file():
            raise InstallLayoutError(f"安装清单不是普通文件: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as error:
            raise InstallLayoutError(f"无法读取安装清单: {path}") from error
        required_keys = {
            "version", "platform", "installRoot", "releaseId",
            "dataRoot", "configRoot", "stateRoot",
        }
        if not isinstance(value, dict) or set(value) != required_keys:
            raise InstallLayoutError(f"安装清单格式无效: {path}")
        if type(value["version"]) is not int or value["version"] != SCHEMA_VERSION:
            raise InstallLayoutError(
                f"不支持的安装清单版本: {value['version']!r}"
            )
        if value["platform"] not in PLATFORMS:
            raise InstallLayoutError(f"安装清单平台无效: {value['platform']!r}")
        expected_platform = current_platform() if platform is None else platform
        if value["platform"] != expected_platform:
            raise InstallLayoutError(
                f"安装清单平台不匹配: {value['platform']} != {expected_platform}"
            )
        recorded_root = _absolute_path(value["installRoot"], "安装清单 installRoot")
        if recorded_root.resolve() != root.resolve():
            raise InstallLayoutError(
                f"安装清单属于其它安装目录: {recorded_root} != {root}"
            )
        return cls.create(
            install_root=root,
            release_id=value["releaseId"],
            platform=value["platform"],
            data_root=_absolute_path(value["dataRoot"], "安装清单 dataRoot"),
            config_root=_absolute_path(value["configRoot"], "安装清单 configRoot"),
            state_root=_absolute_path(value["stateRoot"], "安装清单 stateRoot"),
        )

    @classmethod
    def load_required(
        cls,
        install_root=None,
        *,
        platform: str | None = None,
    ) -> InstalledLayout:
        """Load an installed layout or raise a typed contract error."""
        layout = cls.load(install_root, platform=platform, required=True)
        if layout is None:
            raise AssertionError("required installed layout was not loaded")
        return layout


def main(argv=None) -> int:
    """Small stable CLI used by native entry adapters."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("show", "purge-plan", "remove"))
    parser.add_argument("--install-root", required=True)
    options = parser.parse_args(argv)
    layout = InstalledLayout.load_required(options.install_root)
    if options.action == "remove":
        layout.remove()
        return 0
    if options.action == "purge-plan":
        value = {
            "items": [
                {"role": item.role, "path": str(item.path)}
                for item in layout.purge_plan().items
            ],
        }
    else:
        value = {
            **layout.as_json(),
            "releasePath": str(layout.release_path),
        }
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
