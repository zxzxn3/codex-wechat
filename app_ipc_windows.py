"""Windows App IPC transport; the follower protocol remains in app_ipc."""
import asyncio
import os
from pathlib import Path

from native_codex import discover_app_codex

PIPE_PATH = r"\\.\pipe\codex-ipc"
# The App package ships its Electron main executable next to the codex CLI
# resources; the identity check below compares against this exact image.
APP_EXECUTABLE = "ChatGPT.exe"


def verify_server(handle, executable, *, trust_router=False):
    """Authenticate the actual connected pipe, not an independently looked-up PID.

    With trust_router the caller has opted into a relay hosted by another local
    Codex client (the VS Code extension also hosts `codex-ipc`): the same-user
    check still applies, the executable check does not.
    """
    import win32api
    import win32con
    import win32pipe
    import win32process
    import win32security

    pid = win32pipe.GetNamedPipeServerProcessId(handle)
    process = win32api.OpenProcess(
        win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
    try:
        remote = local = None
        try:
            remote = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
            local = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            remote_sid = win32security.GetTokenInformation(remote, win32security.TokenUser)[0]
            local_sid = win32security.GetTokenInformation(local, win32security.TokenUser)[0]
            if remote_sid != local_sid:
                raise ValueError("App IPC 服务不属于当前用户")
        finally:
            for token in (remote, local):
                if token is not None:
                    token.Close()
        if trust_router:
            return
        image = win32process.GetModuleFileNameEx(process, 0)
        try:
            same = os.path.samefile(image, executable)
        except OSError:
            same = False
        if not same:
            raise ValueError("App IPC 服务不是当前已注册的 Codex App；更新 App 后请退出重开")
    finally:
        process.Close()


async def open_pipe(path, *, limit, trust_router=False):
    if str(path) != PIPE_PATH:
        raise ValueError("只允许本机 Codex App 的命名管道")
    executable = None
    if not trust_router:
        # Package discovery is bounded and must not block the bridge event loop.
        core = await asyncio.to_thread(discover_app_codex, windows=True)
        if not core:
            raise ValueError("找不到已注册的 Codex App，无法确认 IPC 服务身份")
        executable = Path(core).parent.parent / APP_EXECUTABLE
        if not executable.is_file():
            raise ValueError("找不到已注册的 Codex App 主程序；请完全退出并重新打开 App 后再试")
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.create_pipe_connection(lambda: protocol, PIPE_PATH)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    try:
        pipe = transport.get_extra_info("pipe")
        verify_server(pipe.fileno(), executable, trust_router=trust_router)
        return reader, writer
    except BaseException:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (OSError, TimeoutError):
            pass
        raise
