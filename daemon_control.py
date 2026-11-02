"""Read native systemd metadata for this gateway's daemon launcher only."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

from errors import BackendStartupError
from subprocess_io import communicate


def unit_name(command, cwd, env):
    # Stable across gateway restarts; never hash or serialize credential values.
    identity = [command, cwd, env.get("CODEX_HOME"), env.get("HOME")]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return "codex-wechat-codex-" + digest + ".service"


async def systemctl(*args):
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    output, _ = await communicate(proc, timeout=4)
    return proc.returncode, output.decode(errors="replace")


async def status(unit):
    fields = ("LoadState", "ActiveState", "Result", "ExecMainCode", "ExecMainStatus")
    code, output = await systemctl("show", unit, *("--property=" + key for key in fields))
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return values if not code and values.get("LoadState") == "loaded" else {}


def failure(state, command):
    if state.get("ActiveState") != "failed":
        return None
    if (state.get("ExecMainStatus") == str(os.EX_CONFIG) and state.get("Result") == "exit-code"
            and Path(command[0]).name == "codex-deepseek"):
        return BackendStartupError("DeepSeek 密钥环未就绪，请在桌面解锁密钥环后重试。", retry_after=30)
    return BackendStartupError("Codex 后台启动失败，请检查启动器和用户服务日志。")
