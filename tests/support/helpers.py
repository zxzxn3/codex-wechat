"""Capability guards and process-wide isolation for offline fixtures."""
from functools import wraps
import atexit
import os
from pathlib import Path
import shutil
import tempfile

from codex_wechat import diagnostics
from codex_wechat.install_layout import InstalledLayout, current_platform
from codex_wechat.platform_support import RuntimePaths


def runtime_paths_at(root, *, config_file=None, state_dir=None):
    root = Path(root)
    return RuntimePaths(
        data_root=root,
        config_file=Path(config_file) if config_file is not None else root / "config.json",
        state_dir=Path(state_dir) if state_dir is not None else root / ".state",
        workspace_dir=root / "workspace",
        logs_dir=root / "logs",
        shared_dir=root / "shared",
    )


def write_installation_layout(
    install_root,
    *,
    release_id="a" * 16,
    data_root,
    config_root,
    state_root,
    platform=None,
    release_files=False,
    create_release=True,
):
    install_root = Path(install_root)
    release = install_root / "releases" / release_id
    if create_release:
        package = release / "codex_wechat"
        package.mkdir(parents=True, exist_ok=True)
        (package / "app.py").write_text("", encoding="utf-8")
        if release_files:
            (package / "windows_service.py").write_text("", encoding="utf-8")
            python = (release / ".venv/Scripts/python.exe"
                      if (platform or current_platform()) == "windows"
                      else release / ".venv/bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
    InstalledLayout.create(
        install_root,
        release_id,
        platform=platform or current_platform(),
        data_root=data_root,
        config_root=config_root,
        state_root=state_root,
    ).publish()
    return InstalledLayout.load(
        install_root,
        platform=platform or current_platform(),
        required=True,
    )


def isolate_diagnostics():
    """Keep fixture events out of the developer's real data-root log.

    `diagnostics` is a process global, and most fixtures drive real code paths
    that log. Without this the suite appends synthetic `wechat.auth_required`
    and turn events to the log an operator later reads as evidence - and a
    module-level `reset()` would send the next module back to that file.
    """
    existing = getattr(diagnostics, "_test_log_sink", None)
    if existing is not None:
        return existing
    directory = tempfile.mkdtemp(prefix="codex-wechat-tests-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    sink = Path(directory) / "bridge.jsonl"
    reset = diagnostics.reset

    def isolated_reset(*args, **kwargs):
        reset(*args, **kwargs)
        diagnostics.configure(path=sink)

    diagnostics.configure(path=sink)
    diagnostics.reset = isolated_reset
    diagnostics._test_log_sink = sink
    return sink


def requires_symlinks(method):
    @wraps(method)
    def checked(self, *args, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target").write_text("fixture")
            try:
                (root / "link").symlink_to(root / "target")
            except OSError as error:
                if os.name == "nt" and error.winerror == 1314:
                    self.skipTest("Windows user lacks symbolic-link creation privilege")
                raise
        return method(self, *args, **kwargs)
    return checked


# Imported by the earliest offline fixtures during discovery, so a full run is
# already isolated before the first test executes.
isolate_diagnostics()
