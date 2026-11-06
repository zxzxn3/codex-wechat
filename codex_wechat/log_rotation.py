"""Bounded generation-based rotation shared by diagnostic and console logs."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from .storage import locked_file


MAX_BYTES = 5 * 1024 * 1024
KEEP_MIN = 1
KEEP_MAX = 100
DEFAULT_KEEP = 3
KeepSource = int | Callable[[], int]


def normalize_keep(value):
    if type(value) is not int or not KEEP_MIN <= value <= KEEP_MAX:
        raise ValueError(f"logs.keep 必须是 {KEEP_MIN}–{KEEP_MAX} 的整数")
    return value


def keep_from_config(path) -> int:
    """Read logs.keep without making service logging depend on config validity."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        logs = config.get("logs", {}) if isinstance(config, dict) else {}
        if not isinstance(logs, dict):
            return DEFAULT_KEEP
        return normalize_keep(logs.get("keep", DEFAULT_KEEP))
    except (OSError, TypeError, ValueError):
        return DEFAULT_KEEP


def rotated_path(path: Path, number: int) -> Path:
    return path.with_name(f"{path.name}.{number}")


def prune_rotations(path: Path, keep: int) -> None:
    """Delete generations above keep; bounded for a previously larger setting."""
    keep = normalize_keep(keep)
    path = Path(path)
    for number in range(keep + 1, keep + 257):
        try:
            rotated_path(path, number).unlink()
        except OSError:
            pass


def rotate(path: Path, keep: int) -> bool:
    """Shift path and its generations; report whether the active file moved."""
    keep = normalize_keep(keep)
    path = Path(path)
    for number in range(keep, 1, -1):
        try:
            os.replace(rotated_path(path, number - 1),
                       rotated_path(path, number))
        except OSError:
            pass
    moved = False
    try:
        os.replace(path, rotated_path(path, 1))
        moved = True
    except OSError:
        pass
    if moved:
        prune_rotations(path, keep)
    return moved


def _resolve_keep(keep) -> int:
    try:
        return normalize_keep(keep() if callable(keep) else keep)
    except (TypeError, ValueError):
        return DEFAULT_KEEP


def append(path, data, keep: KeepSource, *, max_bytes=MAX_BYTES) -> None:
    """Append bytes under a process lock, rotating at a fixed size boundary.

    Logging is best-effort: a busy lock or unwritable file drops this chunk
    rather than delaying the bridge or supervisor.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return
    path = Path(path)
    max_bytes = max(64, int(max_bytes))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(path.with_name(path.name + ".lock")):
            remaining = memoryview(data)
            while remaining:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                if size >= max_bytes and rotate(path, _resolve_keep(keep)):
                    continue
                chunk = remaining if size >= max_bytes else remaining[:max_bytes - size]
                with path.open("ab", buffering=0) as stream:
                    stream.write(chunk)
                remaining = remaining[len(chunk):]
    except OSError:
        return
