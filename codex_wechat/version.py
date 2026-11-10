"""Build identity shared by the client, the /version reply and log records.

There is no product version to bump: an installed copy is identified by the
release id its manifest records, and a source checkout reports ``dev``.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Union


def release_id(root: Union[str, Path, None] = None) -> str:
    """Installed release identifier; empty when running from a source checkout."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        manifest = json.loads((base / ".release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    identifier = manifest.get("id") if isinstance(manifest, dict) else ""
    return identifier if isinstance(identifier, str) else ""


@lru_cache(maxsize=1)
def build_id() -> str:
    """Identify the running build: the installed release, else ``dev``.

    Cached: every log record carries it, and an uncached read would hit the
    manifest once per event.
    """
    return release_id() or "dev"
