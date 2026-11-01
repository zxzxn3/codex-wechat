"""Atomic state and a process-held single-instance lock on Linux and Windows."""
import json
import os
from pathlib import Path
from platform_support import IS_WINDOWS

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl


class Store:
    def __init__(self, root, *, lock=True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not IS_WINDOWS:
            self.root.chmod(0o700)
        if not lock:
            return
        self.lock = (self.root / "lock").open("a+")
        if not IS_WINDOWS:
            os.chmod(self.root / "lock", 0o600)
        try:
            if IS_WINDOWS:
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("另一个 codex-wechat 进程正在使用此状态目录") from None

    def load(self, name, default=None):
        path = self.root / (name + ".json")
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, name, value):
        path = self.root / (name + ".json")
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if not IS_WINDOWS:
            temp.chmod(0o600)
        temp.replace(path)
        if IS_WINDOWS:
            return  # Windows does not expose POSIX directory fsync via os.open.
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
