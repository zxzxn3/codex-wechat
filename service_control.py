"""Control only the systemd user service owning this gateway process."""
import asyncio
import os

UNIT = "agent-wechat.service"


async def systemctl(*args):
    process = await asyncio.create_subprocess_exec(
        "systemctl", "--user", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 5)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("无法控制 systemd 用户服务，请在电脑端查看服务状态。")
    return output.decode().strip()


async def require_service_owner():
    pid = await systemctl("show", UNIT, "--property=MainPID", "--value")
    if pid != str(os.getpid()):
        raise ValueError("当前桥接不是该用户服务的主进程，不能远程重启。"
                         "请退出前台桥接后在电脑执行 systemctl --user start " + UNIT)


async def restart():
    # Waiting for our own restart job would deadlock with systemd waiting for us to exit.
    await systemctl("--no-block", "restart", UNIT)
