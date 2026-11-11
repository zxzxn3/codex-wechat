"""Stable entry launcher for an installed release."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .install_layout import InstalledLayout


def _install_root(value: str | None) -> Path:
    raw = value or os.environ.get("CODEX_WECHAT_INSTALL_ROOT")
    if not raw:
        raw = str(Path(__file__).resolve().parent.parent)
    return Path(raw).expanduser().absolute()


def command(
    install_root: Path,
    module: str,
    arguments: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Resolve one release command without exposing installation details."""
    root = Path(install_root).expanduser().absolute()
    layout = InstalledLayout.load(root)
    if layout is None:
        project = root
        python = Path(sys.executable)
    else:
        project = layout.release_dir()
        python = layout.python_executable
        if not python.is_file():
            raise ValueError(f"当前版本的 Python 环境缺失: {python}")
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment["PYTHONPATH"] = str(project)
    environment["CODEX_WECHAT_INSTALL_ROOT"] = str(root)
    return [str(python), "-s", "-P", "-u", "-m", module, *arguments], environment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-root")
    parser.add_argument("--module", default="codex_wechat")
    options, arguments = parser.parse_known_args(argv)
    root = _install_root(options.install_root)
    executable, environment = command(root, options.module, arguments)
    os.execvpe(executable[0], executable, environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
