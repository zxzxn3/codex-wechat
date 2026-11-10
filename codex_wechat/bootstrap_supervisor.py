#!/usr/bin/env python3
"""Resolve the active release and run its Windows supervisor.

The supervisor returns 76 when the user asks for a restart, so this loop
reloads the release selected by the installation contract.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import time

from .install_layout import InstallLayoutError, InstalledLayout, default_roots
from .log_rotation import (DEFAULT_KEEP, KeepSource, append as append_log,
                          keep_from_config)
from .platform_support import INSTALL_ROOT

# Let the previous child finish closing before the next supervisor starts.
RESTART_SETTLE_SECONDS = 0.2


def active_release(install_root: Path) -> Path:
    layout = InstalledLayout.load(
        install_root, platform="windows", required=True)
    release = layout.release_dir()
    if not (release / "codex_wechat" / "windows_service.py").is_file():
        raise ValueError(f"当前版本不完整: {release}")
    return release


def load_supervisor(release: Path):
    package_root = Path(release) / "codex_wechat"
    path = package_root / "__init__.py"
    package_name = f"_codex_wechat_release_{Path(release).name}"
    for loaded in tuple(sys.modules):
        if loaded.startswith("_codex_wechat_release_"):
            del sys.modules[loaded]
    spec = importlib.util.spec_from_file_location(
        package_name,
        path,
        submodule_search_locations=[str(package_root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load supervisor package: {package_root}")
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    try:
        spec.loader.exec_module(package)
        return importlib.import_module(package_name + ".windows_service")
    except Exception:
        for loaded in tuple(sys.modules):
            if loaded.startswith("_codex_wechat_release_"):
                del sys.modules[loaded]
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    run = sub.add_parser("supervise", help="run the active release supervisor")
    run.add_argument("--install-root", type=Path, required=True)
    return result


def log_failure(error: BaseException) -> None:
    try:
        install_root = Path(
            os.environ.get("CODEX_WECHAT_INSTALL_ROOT", str(INSTALL_ROOT))
        ).expanduser().resolve()
        layout = InstalledLayout.load(install_root, platform="windows")
        root = layout.data_root if layout is not None else default_roots("windows")[0]
    except (InstallLayoutError, OSError):
        return
    write_log(root / "logs" / "service.log",
              f"{type(error).__name__}: {error}",
              keep=keep_from_config(root / "config.json"))


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
    layout = InstalledLayout.load(
        install_root, platform="windows", required=True)
    paths = layout.runtime_paths()
    state_dir = paths.state_dir
    config = paths.config_file
    log_path = paths.logs_dir / "service.log"

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
                write_log(log_path, f"当前版本已变化，正在加载 {release.name}",
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
