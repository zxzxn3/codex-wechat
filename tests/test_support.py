"""Capability guards for offline platform fixtures."""
from functools import wraps
import os
from pathlib import Path
import tempfile


def xdg_user_data_dir(name, appauthor=False):
    """Stand in for platformdirs' Linux branch so Linux cases run on any host."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / name)


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
