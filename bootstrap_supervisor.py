#!/usr/bin/env python3
"""Resolve the active release and run its Windows supervisor.

The supervisor returns 76 when the user asks for a restart, so this loop
re-resolves the version pointer and loads whatever release it now names.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import re
import sys
import time

from log_rotation import (DEFAULT_KEEP, KeepSource, append as append_log,
                          keep_from_config)
from platform_support import DATA_ROOT_FILE, data_root

# Let the previous child finish closing before the next supervisor starts.
RESTART_SETTLE_SECONDS = 0.2
# `installer.release_identifier()` writes the first 16 hex digits of the hash.
RELEASE_ID = re.compile(r"[a-f0-9]{16}")


def active_release(install_root: Path) -> Path:
    install_root = Path(install_root).expanduser()
    if install_root.is_symlink():
        raise ValueError(f"安装根目录不能是符号链接: {install_root}")
    install_root = install_root.resolve()
    current = install_root / "current"
    if current.is_symlink():
        raise ValueError(f"当前版本指针 current 不能是符号链接或重解析点: {current}")
    if not current.exists():
        raise ValueError(f"当前版本指针 current 缺失: {current}")
    if not current.is_file():
        raise ValueError(f"当前版本指针 current 必须是普通文件: {current}")
    identifier = current.read_text(encoding="utf-8").strip()
    # The installer writes the first 16 hex digits of the release hash; anything
    # else (notably "..", which would resolve to the install root) is corruption.
    if not RELEASE_ID.fullmatch(identifier):
        raise ValueError(f"当前版本指针 current 包含无效版本标识: {current}")
    releases = install_root / "releases"
    if releases.is_symlink():
        raise ValueError(f"releases 目录不能是符号链接: {releases}")
    release = releases / identifier
    if release.is_symlink():
        raise ValueError(f"release 目录不能是符号链接: {release}")
    if not (release / "windows_service.py").is_file():
        raise ValueError(f"当前版本不完整: {release}")
    return release


def load_supervisor(release: Path):
    entry = str(release)
    # Reloading a new release must shadow the previous one instead of stacking it.
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    path = release / "windows_service.py"
    spec = importlib.util.spec_from_file_location("codex_wechat_windows_service", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load supervisor module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    run = sub.add_parser("supervise", help="run the active release supervisor")
    run.add_argument("--install-root", type=Path, required=True)
    return result


def log_failure(error: BaseException) -> None:
    write_log(data_root() / "logs" / "service.log",
              f"{type(error).__name__}: {error}",
              keep=keep_from_config(data_root() / "config.json"))


def log_release_failure(error: BaseException, path: Path, keep: KeepSource) -> None:
    write_log(path, f"release switch: {type(error).__name__}: {error}", keep=keep)


def write_log(path: Path, message: str, *, keep: KeepSource = DEFAULT_KEEP) -> None:
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        append_log(path, f"{stamp} bootstrap: {message}\n", keep)
    except (OSError, ValueError):
        pass


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    install_root = args.install_root.expanduser().resolve()
    os.environ["CODEX_WECHAT_INSTALL_ROOT"] = str(install_root)
    marker = install_root / DATA_ROOT_FILE
    if marker.is_symlink() or not marker.is_file():
        raise ValueError(f"安装根缺少数据根标记: {marker}")
    root = data_root(install_root)
    state_dir = root / ".state"
    config = root / "config.json"
    log_path = root / "logs" / "service.log"

    def log_keep():
        return keep_from_config(config)

    active = None
    last_failure = None
    while True:
        try:
            release = active_release(install_root)
            python = release / ".venv" / "Scripts" / "python.exe"
            if not python.is_file():
                raise FileNotFoundError(f"当前版本的 Python 环境缺失: {python}")
            module = load_supervisor(release)
        except Exception as error:
            if active is None:
                raise
            # A broken pointer must not leave the task without a bridge.
            failure = f"{type(error).__name__}: {error}"
            if failure != last_failure:
                log_release_failure(error, log_path, log_keep)
                last_failure = failure
            release, python, module = active
        else:
            last_failure = None
            if active is not None and release != active[0]:
                write_log(log_path, f"版本指针已变化，正在加载 {release.name}",
                          keep=log_keep)
            active = (release, python, module)
        try:
            code = asyncio.run(module.supervise(
                release,
                state_dir,
                config,
                log_path,
                python=python,
            ))
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0
        if code != getattr(module, "RELEASE_SWITCH_EXIT_CODE", 76):
            return code
        time.sleep(RESTART_SETTLE_SECONDS)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        log_failure(error)
        print(f"bootstrap_supervisor.py: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
