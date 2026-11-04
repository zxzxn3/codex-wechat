"""Launch a Windows process outside the caller's Job Object without argv secrets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        return 2

    env_path = Path(args.env_file)
    try:
        values = json.loads(env_path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            return 2
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in values.items()})
    except (OSError, ValueError, TypeError):
        return 2
    finally:
        # The environment file carries proxy credentials; remove it before the
        # target starts, even if parsing fails.
        try:
            env_path.unlink()
        except OSError:
            pass
        try:
            env_path.parent.rmdir()
        except OSError:
            pass

    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(
        command,
        cwd=args.cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
