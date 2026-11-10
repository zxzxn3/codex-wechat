"""Capability guards and process-wide isolation for offline fixtures."""
from contextlib import ExitStack, contextmanager
from functools import wraps
import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

from codex_wechat import diagnostics
from codex_wechat.install_layout import InstalledLayout, current_platform
from codex_wechat.platform_support import RuntimePaths


@contextmanager
def linux_install_platform():
    """Model a Linux installation on any host.

    The fixtures below shape Linux roots (separate data/config/state) while the
    code under test resolves the platform from the host, so a Windows run would
    compare a Windows expectation against a Linux manifest. Pinning both sides
    here keeps one rule: a test states the platform it models.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("codex_wechat.install_layout.current_platform",
                                  return_value="linux"))
        stack.enter_context(patch("tests.support.helpers.current_platform",
                                  return_value="linux"))
        # The installer and uninstaller each keep their own host flag, and the
        # uninstaller imported current_platform by name; pin every binding that
        # exists rather than assuming one module owns the decision.
        for name in ("installer", "codex_wechat.uninstall",
                     "codex_wechat.platform_support"):
            module = sys.modules.get(name)
            if module is None:
                continue
            if hasattr(module, "IS_WINDOWS"):
                stack.enter_context(patch(name + ".IS_WINDOWS", False))
            if hasattr(module, "current_platform"):
                stack.enter_context(
                    patch(name + ".current_platform", return_value="linux"))
        yield


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


def outbox_entry(identity="message", *, user="owner", context="context",
                 text="message", reply_seq=0):
    """A minimally complete persisted outbound entry."""
    return {
        "id": identity,
        "group": identity,
        "role": "body",
        "user": user,
        "context": context,
        "text": text,
        "replySeq": reply_seq,
    }


def write_installation_layout(
    install_root,
    *,
    release_id="a" * 16,
    data_root,
    config_root=None,
    state_root=None,
    platform=None,
    release_files=False,
    create_release=True,
):
    # Omitted roots follow the platform contract: Windows keeps one root, Linux
    # splits config and state. Callers that model Linux pass all three.
    config_root = data_root if config_root is None else config_root
    state_root = data_root if state_root is None else state_root
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
    return InstalledLayout.load_required(
        install_root,
        platform=platform or current_platform(),
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
