"""Build identity shared by the client, the /version reply and log records.

There is no product version to bump: an installed copy is identified by the
release id its manifest records, and a source checkout reports ``dev``.
"""
from functools import lru_cache
from pathlib import Path
from typing import Union

from .install_layout import InstalledLayout


def release_id(root: Union[str, Path, None] = None) -> str:
    """Installed release identifier; empty when running from a source checkout."""
    if root is None:
        package_parent = Path(__file__).resolve().parent.parent
        if (package_parent / ".git").is_dir():
            return ""
        from .platform_support import INSTALL_ROOT
        root = INSTALL_ROOT
    try:
        layout = InstalledLayout.load(Path(root))
    except (OSError, ValueError):
        return ""
    return layout.release_id if layout is not None else ""


@lru_cache(maxsize=1)
def build_id() -> str:
    """Identify the running build: the installed release, else ``dev``.

    Cached: every log record carries it, and an uncached read would hit the
    manifest once per event.
    """
    return release_id() or "dev"
