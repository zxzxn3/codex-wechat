"""Release-directory lifecycle; no installer prompts or platform policy."""
import os
from pathlib import Path
import shutil
import time

ACTIVE_FILE = "active-release"
RELEASES_DIR = "releases"
PRUNE_RETRY_SECONDS = 0.2


def active_release(target):
    try:
        value = (Path(target) / ACTIVE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def prune_releases(target, *, keep, report=None, retry_seconds=PRUNE_RETRY_SECONDS):
    """Delete stale releases, but never the one a running bridge marked active."""
    releases = Path(target) / RELEASES_DIR
    if releases.is_symlink() or not releases.is_dir():
        return []
    kept = {name for name in keep if name}
    active = active_release(target)
    if active:
        kept.add(active)
    for stale in releases.glob(".trash-*"):
        shutil.rmtree(stale, ignore_errors=True)
    candidates = [entry for entry in releases.iterdir()
                  if not entry.name.startswith(".") and entry.is_dir() and not entry.is_symlink()]
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
                time.sleep(retry_seconds)
            else:
                shutil.rmtree(trash, ignore_errors=True)
                removed.append(entry.name)
                break
    return removed
