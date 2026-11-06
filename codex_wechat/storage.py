"""Private JSON state and a process-held lock using native filesystem semantics."""
import json
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager

from .platform_support import IS_WINDOWS, make_private

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl


def _lock_descriptor(descriptor):
    if IS_WINDOWS:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def locked_file(path):
    """Try a native process lock; contention raises instead of blocking delivery."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock_descriptor(descriptor)
        yield
    finally:
        os.close(descriptor)


class Store:
    def __init__(self, root, *, lock=True):
        self.root = Path(root)
        make_private(self.root)
        self.lock = None
        if not lock:
            return
        self.lock = (self.root / "lock").open("a+")
        if not IS_WINDOWS:
            os.chmod(self.root / "lock", 0o600)
        try:
            _lock_descriptor(self.lock.fileno())
        except OSError:
            self.close()
            raise RuntimeError("另一个 codex-wechat 进程正在使用此状态目录") from None

    def close(self):
        if self.lock is not None:
            self.lock.close()

    def load(self, name, default=None):
        path = self.root / (name + ".json")
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return default

    def save(self, name, value):
        path = self.root / (name + ".json")
        fd, temporary = tempfile.mkstemp(prefix="." + name + "-", suffix=".tmp", dir=self.root)
        temp = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
            if not IS_WINDOWS:
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temp.unlink(missing_ok=True)
