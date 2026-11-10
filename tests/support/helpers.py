"""Capability guards and process-wide isolation for offline fixtures."""
from functools import wraps
import atexit
import json
import os
from pathlib import Path
import shutil
import tempfile

from codex_wechat import diagnostics
from codex_wechat.platform_support import RuntimePaths


def xdg_user_data_dir(name, appauthor=False):
    """Stand in for platformdirs' Linux branch so Linux cases run on any host."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / name)


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


def write_runtime_roots_manifest(install_root, *, data_root, config_root, state_root):
    install_root = Path(install_root)
    (install_root / ".runtime-roots").write_text(
        json.dumps({
            "version": 1,
            "data": str(data_root),
            "config": str(config_root),
            "state": str(state_root),
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
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
