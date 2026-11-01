"""Codex launcher, byte transport and JSON-RPC lifecycle; no thread state."""
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import socket

import daemon_control

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"

class CodexRpcError(RuntimeError):
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

class CodexTransport:
    def __init__(self, config, cwd, *, on_request, on_event, on_connect, on_disconnect):
        self.config, self.cwd = config, cwd
        self.on_request, self.on_event = on_request, on_event
        self.on_connect, self.on_disconnect = on_connect, on_disconnect
        self.proc = self.reader = self.websocket = None
        self.requests = {}
        self.serial = 0
        self.command = config.get("command", ["codex", "app-server"])
        self.shared = config.get("sharedDaemon", self.command[-1:] == ["app-server"]
                                 or self.command[-2:] == ["app-server", "proxy"])

    async def start(self):
        env = dict(os.environ)
        for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME"):
            env.pop(key, None)
        env.update(self.config.get("env", {}))
        command = list(self.command)
        if self.shared:
            if command[-1:] == ["app-server"]:
                command.append("proxy")
            elif command[-2:] != ["app-server", "proxy"]:
                raise ValueError("sharedDaemon 模式的 command 须以 app-server 结尾")
            try:
                await asyncio.wait_for(self.connect(command, env), 5)
                return
            except CodexRpcError:
                raise  # A protocol rejection is not evidence that the daemon is absent.
            except (RuntimeError, OSError, asyncio.TimeoutError):
                await self.close()
            unit = await self.start_shared_daemon(command[:-1], env)
            deadline = asyncio.get_running_loop().time() + 30
            last_error = "连接超时"
            while asyncio.get_running_loop().time() < deadline:
                try:
                    remaining = deadline - asyncio.get_running_loop().time()
                    await asyncio.wait_for(self.connect(command, env), min(5, remaining))
                    return
                except CodexRpcError:
                    raise
                except (RuntimeError, OSError, asyncio.TimeoutError) as error:
                    state = await daemon_control.status(unit)
                    failure = daemon_control.failure(state, command)
                    if failure:
                        raise failure
                    last_error = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    await self.close()
                    await asyncio.sleep(0.5)
            raise RuntimeError(f"共享后台自动启动后仍未连接成功：{last_error}（{unit}）；请检查启动器/登录状态后重试 /backend")
        await self.connect(command, env)

    async def start_shared_daemon(self, command, env):
        # Let systemd own the daemon's cgroup, independently of the gateway.
        # Native `daemon start` reuses a running daemon; it never requests a restart.
        executable = shutil.which(command[0], path=env.get("PATH"))
        if not executable:
            raise RuntimeError("找不到 Codex 启动器，无法自动启动共享后台")
        command = [executable, *command[1:]]
        unit = daemon_control.unit_name(command, self.cwd, env)
        state = await daemon_control.status(unit)
        if state.get("ActiveState") in ("active", "activating", "reloading"):
            return unit  # Reuse the existing startup; never stack keyring prompts.
        if state.get("ActiveState") == "failed":
            code, _ = await daemon_control.systemctl("reset-failed", unit)
            if code:
                raise RuntimeError("无法清理消息桥后台启动失败状态")
        managed = {"INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "MAINPID",
                   "MANAGERPID", "SYSTEMD_EXEC_PID", "WATCHDOG_PID", "WATCHDOG_USEC"}
        args = ["systemd-run", "--user", "--quiet", "--no-ask-password",
                "--service-type=exec", "--property=ExitType=cgroup", "--property=UMask=0077",
                "--expand-environment=no", "--unit=" + unit,
                "--working-directory=" + self.cwd]
        # Copy values from the caller's environment, never put secret values in argv.
        args.extend("--setenv=" + key for key in env
                    if key not in managed and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))
        absent = [key for key in ("CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
                  if key not in env]
        if absent:
            args.append("--property=UnsetEnvironment=" + " ".join(absent))
        proc = await asyncio.create_subprocess_exec(
            *args, "--", *command, "daemon", "start", env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            code = await asyncio.wait_for(proc.wait(), 10)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if code:
            # Another client may have started the same native launcher concurrently.
            state = await daemon_control.status(unit)
            if state.get("ActiveState") not in ("active", "activating", "reloading"):
                failure = daemon_control.failure(state, command)
                if failure:
                    raise failure
                raise RuntimeError("无法自动启动共享后台，请检查用户 systemd 和 systemd-run；微信控制仍可用")
        return unit

    @property
    def connected(self):
        return bool(self.reader and not self.reader.done() and self.proc
                    and self.proc.returncode is None)

    async def connect(self, command, env):
        self.on_connect()
        if self.shared:
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
                limit=16 * 1024 * 1024)
        self.reader = asyncio.create_task(self.read())
        await self.rpc("initialize", {"clientInfo": {
            "name": "agent_wechat", "title": "Agent WeChat", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}})
        await self.write({"method": "initialized", "params": {}})
        # Keep project-owned skills local to this app-server connection instead
        # of installing links into each backend's CODEX_HOME.
        await self.rpc("skills/extraRoots/set", {"extraRoots": [str(SKILLS_ROOT)]})

    async def write(self, message):
        if not self.proc or self.proc.returncode is not None:
            raise RuntimeError("Codex 后端已退出，请重启消息桥")
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
        except (ValueError, OSError, RuntimeError):
            pass
        finally:
            error = RuntimeError("共享 Codex 连接断开，可重试 /backend 自动连接或启动后台；未重放任务"
                                 if self.shared else "Codex 连接断开，请重启消息桥；未自动重放任务")
            for future in list(self.requests.values()):
                if not future.done():
                    future.set_exception(error)
            self.on_disconnect(error)

    async def close(self):
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 8)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self.reader:
            await self.reader
