"""Codex launcher, byte transport and JSON-RPC lifecycle; no thread state."""
import asyncio
import json
import re
import socket

from . import daemon_control
from .backend_runtime import BackendRuntime
from .codex_connection import ConnectionSpec
from .diagnostics import error_fields, log_event
from .errors import ErrorAction
from .subprocess_io import communicate
from .platform_support import IS_WINDOWS, PROJECT_ROOT, subprocess_options
from .version import build_id

SKILLS_ROOT = PROJECT_ROOT / "skills"

class CodexRpcError(RuntimeError):
    error_actions = ErrorAction.TERMINAL

    def __init__(self, code, reason="", method=None, *, definite=None):
        self.code = code
        # Only validation/precondition rejections establish non-delivery. Internal
        # errors may arrive after side effects and must remain uncertain.
        self.definite_rejection = (definite if definite is not None else
            code in (-32601, -32602) or (code == -32600 and bool(re.search(
                r"not steerable|no active turn|expected.?turn|turn.*mismatch|turn.*already.*(?:active|progress)",
                str(reason), re.I))))
        # Keep useful daemon errors without echoing URLs, credentials or request bodies.
        reason = str(reason).replace("\n", " ")
        if re.search(r"\b(prompt|instructions?|input)\b", reason, re.I):
            reason = "服务端拒绝了请求内容，原文已隐藏。"
        else:
            reason = re.sub(r"https?://\S+", "[URL]", reason)
            reason = re.sub(r"(?i)bearer\s+\S+", "Bearer [已隐藏]", reason)
            reason = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", reason)
            reason = re.sub(r'''(?i)((?:api[_-]?key|token|secret|authorization)["']?\s*[:=]\s*)[^\s,;}]+''',
                            r"\1[已隐藏]", reason)
        self.reason = reason[:300]
        stage = f"（{method}）" if method else ""
        detail = f"：{self.reason}" if self.reason else ""
        super().__init__(f"Codex RPC 错误 {code}{stage}{detail}")

class SharedDaemonUnavailable(RuntimeError):
    """The shared daemon cannot be reached or started; callers may use a private server."""
    error_actions = ErrorAction.FALLBACK | ErrorAction.RETRYABLE

    def __init__(self, reason, *, cause=None):
        super().__init__(str(reason))
        self.cause = cause
        self.cause_type = type(cause).__name__ if cause is not None else None
        self.cause_code = getattr(cause, "code", None)
        self.retry_after = getattr(cause, "retry_after", None)

class CodexTransport:
    def __init__(self, config, cwd, *, on_request, on_event, on_connect, on_disconnect):
        self.config, self.cwd = config, cwd
        self.runtime = BackendRuntime(config, cwd)
        self.on_request, self.on_event = on_request, on_event
        self.on_connect, self.on_disconnect = on_connect, on_disconnect
        self.proc = self.reader = self.websocket = None
        self.server_version = ""
        self.requests = {}
        self.serial = 0
        self.launch_env = {}
        self.spec = ConnectionSpec.from_config(config)
        self.websocket_url = self.spec.endpoint
        self.command = list(self.spec.command)
        self.shared = self.spec.shared

    async def start(self):
        env = self.spec.environment(self.config)
        command = self.command if self.websocket_url else self.spec.resolve(env)
        # Only network settings are retained for probes, never launcher credentials.
        network = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "openai_base_url"}
        self.launch_env = {k: v for k, v in env.items() if k.lower() in network}
        if self.websocket_url:
            await self.runtime.ensure_external(env)
            try:
                await asyncio.wait_for(self.connect(command, env), 15)
            except BaseException:
                await self.close()
                raise
            return
        if self.shared:
            try:
                await asyncio.wait_for(self.connect(command, env), 5)
                return
            except CodexRpcError:
                raise  # A protocol rejection is not evidence that the daemon is absent.
            except (TimeoutError, RuntimeError, OSError):
                await self.close()
            try:
                unit = await self.start_shared_daemon(command[:-1], env)
            except (TimeoutError, RuntimeError, OSError) as error:
                await self.close()
                raise SharedDaemonUnavailable(str(error), cause=error) from error
            deadline = asyncio.get_running_loop().time() + 30
            last_error = "连接超时"
            while asyncio.get_running_loop().time() < deadline:
                try:
                    remaining = deadline - asyncio.get_running_loop().time()
                    await asyncio.wait_for(self.connect(command, env), min(5, remaining))
                    return
                except CodexRpcError:
                    raise
                except (TimeoutError, RuntimeError, OSError) as error:
                    state = await daemon_control.status(unit) if unit else {}
                    failure = daemon_control.failure(state, command) if unit else None
                    if failure:
                        await self.close()
                        raise SharedDaemonUnavailable(str(failure), cause=failure) from error
                    last_error = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    await self.close()
                    # A fresh daemon needs about 0.7s before its socket answers;
                    # poll faster than that so the wait is not rounded up to it.
                    await asyncio.sleep(0.25)
            raise SharedDaemonUnavailable(f"共享后台自动启动后仍未连接成功：{last_error}")
        await self.connect(command, env)

    async def start_shared_daemon(self, command, env):
        return await self.runtime.ensure_native(command, env, is_windows=IS_WINDOWS)

    @property
    def connected(self):
        return bool(self.reader and not self.reader.done() and (
            self.websocket is not None if self.websocket_url else
            self.proc and self.proc.returncode is None))

    async def connect(self, command, env):
        self.on_connect()
        if self.websocket_url:
            from websockets.asyncio.client import connect
            self.websocket = await connect(
                self.websocket_url, proxy=None, open_timeout=5, close_timeout=1,
                compression=None, max_size=16 * 1024 * 1024)
        elif self.shared and IS_WINDOWS:
            from .pipe_websocket import PipeWebSocket
            self.proc = await asyncio.create_subprocess_exec(
                *command, cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=16 * 1024 * 1024, **subprocess_options())
            self.websocket = PipeWebSocket(self.proc)
            await self.websocket.handshake()
        elif self.shared:
            try:
                from websockets.asyncio.client import unix_connect
                from websockets.exceptions import WebSocketException
            except ImportError:
                raise ValueError("共享后台需要 websockets 15–17，请按 README 安装项目依赖") from None
            # `app-server proxy` forwards raw bytes, including the HTTP Upgrade and
            # WebSocket frames. A local socketpair lets the library own this protocol
            # while the existing launcher selects the daemon/home and credentials.
            local, remote = socket.socketpair()
            try:
                try:
                    self.proc = await asyncio.create_subprocess_exec(
                        *command, cwd=self.cwd, env=env, stdin=remote, stdout=remote,
                        stderr=asyncio.subprocess.DEVNULL)
                finally:
                    remote.close()
                self.websocket = await unix_connect(
                    sock=local, uri="ws://localhost/", proxy=None,
                    open_timeout=4, close_timeout=1, compression=None,
                    max_size=16 * 1024 * 1024)
            except WebSocketException as error:
                local.close()
                raise RuntimeError(f"共享连接 WebSocket 握手失败（{type(error).__name__}）") from None
            except BaseException:
                local.close()
                raise
        else:
            self.proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=16 * 1024 * 1024, **subprocess_options())
        self.reader = asyncio.create_task(self.read())
        # The response carries the server's own identification; keep it for the
        # one startup log line instead of guessing versions from the install.
        greeting = await self.rpc("initialize", {"clientInfo": {
            "name": "codex_wechat", "title": "Codex WeChat", "version": build_id()},
            "capabilities": {"experimentalApi": True}})
        if isinstance(greeting, dict):
            for key in ("userAgent", "user_agent"):
                value = greeting.get(key)
                if isinstance(value, str) and value:
                    self.server_version = value[:96]
                    break
        await self.write({"method": "initialized", "params": {}})
        # Keep project-owned skills local to this app-server connection instead
        # of installing links into each backend's CODEX_HOME.
        await self.rpc("skills/extraRoots/set", {"extraRoots": [str(SKILLS_ROOT)]})

    async def write(self, message):
        if self.websocket is None and (not self.proc or self.proc.returncode is not None):
            raise RuntimeError("Codex 后端已退出，请重启微信桥")
        payload = json.dumps(message)
        if self.websocket is not None:
            from websockets.exceptions import WebSocketException
            try:
                await self.websocket.send(payload)
            except WebSocketException as error:
                raise RuntimeError(f"共享连接发送失败（{type(error).__name__}）") from None
        else:
            self.proc.stdin.write((payload + "\n").encode())
            await self.proc.stdin.drain()

    async def receive(self):
        if self.websocket is not None:
            from websockets.exceptions import WebSocketException
            try:
                return await self.websocket.recv()
            except WebSocketException as error:
                raise RuntimeError(f"共享连接接收失败（{type(error).__name__}）") from None
        return await self.proc.stdout.readline()

    async def rpc(self, method, params):
        self.serial += 1
        key = self.serial
        future = asyncio.get_running_loop().create_future()
        self.requests[key] = future
        try:
            await self.write({"id": key, "method": method, "params": params})
            return await asyncio.wait_for(future, 90)
        except CodexRpcError as error:
            raise CodexRpcError(error.code, error.reason, method,
                                definite=error.definite_rejection) from None
        finally:
            self.requests.pop(key, None)

    async def read(self):
        try:
            while line := await self.receive():
                msg = json.loads(line)
                if "id" in msg and "method" not in msg:
                    future = self.requests.get(msg["id"])
                    if future and not future.done():
                        if "error" in msg:
                            future.set_exception(CodexRpcError(msg['error'].get('code', 'unknown'),
                                                               msg['error'].get('message', '')))
                        else:
                            future.set_result(msg.get("result", {}))
                elif "id" in msg:
                    await self.on_request(msg)
                else:
                    self.on_event(msg)
        except (ValueError, OSError, RuntimeError) as error:
            # A failing handler ends this loop as well, so record what happened;
            # otherwise the disconnect below reads as a network problem.
            log_event("codex.read_failed", level="warning", component="transport",
                      **error_fields(error))
        finally:
            error = RuntimeError("共享 Codex 连接断开，可重试 /backend 自动连接或启动后台；未重放任务"
                                 if self.shared else "Codex 连接断开，请重启微信桥；未自动重放任务")
            for future in list(self.requests.values()):
                if not future.done():
                    future.set_exception(error)
            self.on_disconnect(error)

    async def close(self):
        reader, self.reader = self.reader, None
        if reader and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        websocket, self.websocket = self.websocket, None
        proc, self.proc = self.proc, None
        try:
            if websocket is not None:
                await websocket.close()
        finally:
            if proc is not None:
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                await communicate(proc, timeout=8)
