"""Bounded fallback for local Codex token-count metadata, never conversation text."""
import json
from pathlib import Path
import re
from threading import Lock

TOKEN_COUNT = re.compile(rb'"type"\s*:\s*"token_count"')
MAX_TAIL = 8 * 1024 * 1024


def stored_usage(path, thread_id):
    if not path:
        return None
    path = Path(path)
    if path.suffix != ".jsonl" or not thread_id or thread_id not in path.name:
        return None
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - MAX_TAIL)
            stream.seek(start)
            lines = stream.read(MAX_TAIL).splitlines()
            if start:
                lines = lines[1:]  # Skip a partial record at the tail boundary.
        for line in reversed(lines):
            if not TOKEN_COUNT.search(line):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # A writer may not have finished its final JSONL record.
            payload = record.get("payload", {})
            if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            result = {}
            for source, target in (("total_token_usage", "total"), ("last_token_usage", "last")):
                value = info.get(source)
                if isinstance(value, dict):
                    result[target] = {"".join([key.split("_")[0],
                        *(part.capitalize() for part in key.split("_")[1:])]): count
                        for key, count in value.items() if type(count) is int and count >= 0}
            window = info.get("model_context_window")
            if type(window) is int and window > 0:
                result["modelContextWindow"] = window
            if result.get("total") or result.get("last"):
                return result
    except (OSError, ValueError, TypeError):
        return None
    return None


class UsageCache:
    """Cache unchanged native files; stat only, with bounded connection-local entries."""
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def clear(self):
        with self.lock:
            self.entries.clear()

    @staticmethod
    def signature(path):
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size

    def read(self, path, thread_id):
        if not path:
            return None
        key = str(path), thread_id
        with self.lock:
            try:
                signature = self.signature(path)
                previous = self.entries.get(key)
                if previous and previous[0] == signature:
                    return previous[1]
                result = stored_usage(path, thread_id)
                if self.signature(path) == signature:
                    self.entries.pop(key, None)
                    self.entries[key] = signature, result
                    while len(self.entries) > 20:
                        self.entries.pop(next(iter(self.entries)))
                return result
            except OSError:
                self.entries.pop(key, None)
                return None
