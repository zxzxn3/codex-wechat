#!/usr/bin/env python3
"""Stable Windows supervisor entry for the package implementation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_wechat.bootstrap_supervisor import log_failure, main  # noqa: E402


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        log_failure(error)
        print(f"bootstrap_supervisor.py: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
