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
import sys
import time

# Let the previous child finish closing before the next supervisor starts.
RESTART_SETTLE_SECONDS = 0.2


def active_release(install_root: Path) -> Path:
    install_root = Path(install_root).expanduser()
    if install_root.is_symlink():
        raise ValueError(f"install root must not be a symlink: {install_root}")
    install_root = install_root.resolve()
    current = install_root / "current"
    if current.is_symlink():
        raise ValueError(f"version pointer must not be a symlink: {current}")
    if not current.exists():
        raise ValueError(f"version pointer is missing: {current}")
    identifier = current.read_text(encoding="utf-8").strip()
    if (not identifier or not identifier.isascii()
            or not all(char.isalnum() or char in "._-" for char in identifier)):
        raise ValueError(f"invalid release id in {current}")
    releases = install_root / "releases"
    if releases.is_symlink():
        raise ValueError(f"releases directory must not be a symlink: {releases}")
    release = releases / identifier
    if release.is_symlink():
        raise ValueError(f"release directory must not be a symlink: {release}")
    if not (release / "windows_service.py").is_file():
        raise ValueError(f"active release is incomplete: {release}")
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
    run.add_argument("--state-dir", type=Path)
    run.add_argument("--config", type=Path)
    run.add_argument("--log", type=Path)
    return result


def log_failure(error: BaseException) -> None:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    write_log(root / "codex-wechat" / "logs" / "service.log",
              f"{type(error).__name__}: {error}")


def log_release_failure(error: BaseException, path: Path) -> None:
    write_log(path, f"release switch: {type(error).__name__}: {error}")


def write_log(path: Path, message: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{stamp} bootstrap: {message}\n")
    except OSError:
        pass


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    install_root = args.install_root.expanduser().resolve()
    os.environ["CODEX_WECHAT_INSTALL_ROOT"] = str(install_root)
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "codex-wechat"
    state_dir = args.state_dir or root / ".state"
    config = args.config or root / "config.json"
    log_path = args.log or root / "logs" / "service.log"
    active = None
    last_failure = None
    while True:
        try:
            release = active_release(install_root)
            python = release / ".venv" / "Scripts" / "python.exe"
            if not python.is_file():
                raise FileNotFoundError(f"release Python environment is missing: {python}")
            module = load_supervisor(release)
        except Exception as error:
            if active is None:
                raise
            # A broken pointer must not leave the task without a bridge.
            failure = f"{type(error).__name__}: {error}"
            if failure != last_failure:
                log_release_failure(error, log_path)
                last_failure = failure
            release, python, module = active
        else:
            last_failure = None
            if active is not None and release != active[0]:
                write_log(log_path, f"version pointer changed; loading {release.name}")
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
