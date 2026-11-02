"""Privacy-preserving structured diagnostics.

Callers pass metadata only. Message text, prompts, responses, credentials,
context tokens, file names and absolute user paths are not part of the event
schema and are dropped even if a caller accidentally supplies them.
"""
from __future__ import annotations

import contextvars
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import threading

from platform_support import data_root
from storage import locked_file

APP_VERSION = "0.1.0"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_EVENT = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_REQUEST_ID = contextvars.ContextVar("codex_wechat_request_id", default=None)
_lock = threading.Lock()
_path = None
_level = None
_max_bytes = MAX_BYTES
_ENDPOINTS = frozenset("ilink/bot/" + name for name in (
    "get_bot_qrcode", "get_qrcode_status", "getupdates", "getuploadurl",
    "sendmessage", "getconfig", "sendtyping", "msg/notifystart", "msg/notifystop",
))

_TEXT_FIELDS = {
    "component": r"[a-z][a-z0-9_.-]{0,31}",
    "phase": r"[a-z][a-z0-9_.-]{0,31}",
    "status": r"[a-z][a-z0-9_.-]{0,31}",
    "mode": r"[a-z][a-z0-9_.-]{0,15}",
    "role": r"[a-z][a-z0-9_.-]{0,15}",
    "kind": r"[a-z][a-z0-9_.-]{0,31}",
    "backend": r"[a-z0-9][a-z0-9_.-]{0,31}",
    "provider": r"[a-z0-9][a-z0-9_.-]{0,31}",
    "operation": r"[a-z][a-z0-9_.-]{0,31}",
    "source": r"[a-z][a-z0-9_.-]{0,31}",
    "service": r"[a-z][a-z0-9_.-]{0,31}",
    "request_id": r"[A-Za-z0-9._-]{1,64}",
    "job_id": r"[A-Za-z0-9._-]{1,64}",
    "error_type": r"[A-Za-z_][A-Za-z0-9_]{0,60}",
    "error_code": r"[A-Za-z0-9._-]{1,16}",
    "platform": r"[a-z0-9_.-]{1,16}",
    "version": r"[A-Za-z0-9._-]{1,32}",
}
_NUMBER_FIELDS = {
    "duration_ms", "ingress_ms", "queued", "pending", "attempt", "count",
    "bytes", "chars", "parts", "part", "http_status", "curl_code",
    "exit_code", "retry_in_ms", "threshold", "sample_ms", "pid",
}
_BOOL_FIELDS = {"ok", "connected"}


def _configured_level():
    if _level is not None:
        return _level
    value = os.environ.get("CODEX_WECHAT_LOG_LEVEL", "info").casefold()
    return value if value in _LEVELS else "info"


def configure(*, path=None, level=None, max_bytes=None):
    """Override the log destination for tests or embedders."""
    global _path, _level, _max_bytes
    if path is not None:
        _path = Path(path)
    if level is not None:
        _level = level if level in _LEVELS else "info"
    if max_bytes is not None:
        _max_bytes = max(64, int(max_bytes))


def reset():
    """Restore production defaults; intended for test isolation."""
    global _path, _level, _max_bytes
    _path = None
    _level = None
    _max_bytes = MAX_BYTES
    _REQUEST_ID.set(None)


def log_path():
    return Path(_path) if _path is not None else data_root() / "logs" / "bridge.jsonl"


def new_request_id():
    return "req-" + os.urandom(8).hex()


def current_request_id():
    return _REQUEST_ID.get()


@contextmanager
def request_context(value):
    token = _REQUEST_ID.set(value)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def _clean_text(value):
    try:
        value = " ".join(str(value).split())
        value = re.sub(r"https?://\S+", "[URL]", value)
        value = re.sub(
            r"(?i)(bearer|token|secret|authorization|api[_-]?key)\s*[:=]\s*\S+",
            r"\1=[REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        home = str(Path.home())
        if home and home != "/":
            value = value.replace(home, "[HOME]")
        return value[:80]
    except (OSError, RuntimeError, ValueError):
        return ""


def _clean_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if abs(value) > 10 ** 15:
        return None
    return round(value, 3) if isinstance(value, float) else value


def _clean_field(name, value):
    if name == "endpoint":
        return value if isinstance(value, str) and value in _ENDPOINTS else None
    if name in _BOOL_FIELDS:
        return bool(value) if isinstance(value, bool) else None
    if name in _NUMBER_FIELDS:
        return _clean_number(value)
    if name == "error_code":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        value = str(value)
    elif not isinstance(value, str):
        return None
    if name not in _TEXT_FIELDS:
        return None
    value = _clean_text(value)
    return value if re.fullmatch(_TEXT_FIELDS[name], value) else None


def _rotate(path):
    parent = path.parent
    for number in range(BACKUP_COUNT, 1, -1):
        source = parent / f"{path.name}.{number - 1}"
        target = parent / f"{path.name}.{number}"
        try:
            os.replace(source, target)
        except OSError:
            pass
    try:
        os.replace(path, parent / f"{path.name}.1")
    except OSError:
        pass


def _write(record):
    try:
        path = log_path()
        data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
            with locked_file(path.with_name(path.name + ".lock")):
                try:
                    if path.stat().st_size + len(data) > _max_bytes:
                        _rotate(path)
                except OSError:
                    pass
                descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    remaining = memoryview(data)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0:
                            raise OSError("log write made no progress")
                        remaining = remaining[written:]
                finally:
                    os.close(descriptor)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except Exception:
        # Diagnostics must never break message delivery.
        return


def error_fields(error):
    """Return safe error metadata without an exception message or traceback."""
    fields = {"error_type": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, (int, str)) and not isinstance(code, bool):
        fields["error_code"] = str(code)[:16]
    return fields


def log_event(event_name, *, level="info", **fields):
    try:
        if not isinstance(event_name, str) or not _EVENT.fullmatch(event_name):
            return
        level = level if level in _LEVELS else "info"
        if _LEVELS[level] < _LEVELS[_configured_level()]:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "event": event_name,
            "platform": sys.platform,
            "version": APP_VERSION,
            "pid": os.getpid(),
        }
        request_id = _REQUEST_ID.get()
        if request_id:
            cleaned = _clean_field("request_id", request_id)
            if cleaned is not None:
                record["request_id"] = cleaned
        for name, value in fields.items():
            if (name in record or name not in _TEXT_FIELDS
                    and name not in _NUMBER_FIELDS and name not in _BOOL_FIELDS):
                continue
            cleaned = _clean_field(name, value)
            if cleaned is not None:
                record[name] = cleaned
        _write(record)
    except Exception:
        # Logging is best-effort and must not affect delivery.
        return
