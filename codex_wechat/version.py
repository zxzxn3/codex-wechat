"""Product version shared by diagnostics and the Codex client identity."""
import json
from pathlib import Path
from typing import Union

APP_VERSION = "0.1.0"


def release_id(root: Union[str, Path, None] = None) -> str:
    """Installed release identifier; empty when running from a source checkout."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        manifest = json.loads((base / ".release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    identifier = manifest.get("id") if isinstance(manifest, dict) else ""
    return identifier if isinstance(identifier, str) else ""
