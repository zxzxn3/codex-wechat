"""Version-scoped Linux App follower protocol; never owns an App process.

Framing and method versions come from App 26.1027.51404. This is not the
public app-server protocol. Only a discovered owner may receive a request.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import stat
import struct
import time
import uuid

from codex_transport import CodexRpcError
from diagnostics import log_event

MAX_FRAME = 16 * 1024 * 1024
VERSIONS = {
    "initialize": 0, "thread-owner-discovery": 1,
    "thread-stream-following-changed": 1,
    "thread-stream-state-changed": 11,
    "thread-follower-start-turn": 2, "thread-follower-steer-turn": 1,
    "thread-follower-interrupt-turn": 4, "thread-follower-compact-thread": 1,
    "thread-follower-update-thread-settings": 2,
    "thread-follower-command-approval-decision": 1,
    "thread-follower-file-approval-decision": 1,
    "thread-follower-submit-user-input": 1,
}
RESPONSES = {
    "item/commandExecution/requestApproval": ("thread-follower-command-approval-decision", "decision"),
    "item/fileChange/requestApproval": ("thread-follower-file-approval-decision", "decision"),
    "item/tool/requestUserInput": ("thread-follower-submit-user-input", "response"),
}


def ipc_path(config):
    """Do not guess the home selected inside an arbitrary launcher."""
    if os.name == "nt" or config.get("websocketUrl"):
        return None
    home = config.get("env", {}).get("CODEX_HOME")
    command = config.get("command", ["codex", "app-server"])
    if not home and Path(command[0]).name != "codex":
        return None
    return Path(home or Path.home() / ".codex").expanduser() / "ipc" / "ipc.sock"


def apply_patches(state, patches):
    """Apply the App's Immer add/remove/replace paths, atomically to a copy."""
    result = copy.deepcopy(state)
    for patch in patches:
        path, op = patch["path"], patch["op"]
        if op not in ("add", "remove", "replace") or not isinstance(path, list) or len(path) > 64:
            raise ValueError("Unsupported App state patch")
        if not path:
            if op == "remove":
                raise ValueError("Cannot remove App state")
            result = copy.deepcopy(patch["value"])
            continue
        target = result
        for key in path[:-1]:
            target = target[key]
        key = path[-1]
        if isinstance(target, list):
            if type(key) is not int or not 0 <= key <= len(target):
                raise ValueError("Invalid App array path")
            if op == "add":
                target.insert(key, copy.deepcopy(patch["value"]))
            elif op == "remove":
                target.pop(key)
            else:
                target[key] = copy.deepcopy(patch["value"])
        elif isinstance(target, dict) and isinstance(key, str):
            if op == "remove":
                del target[key]
            else:
                target[key] = copy.deepcopy(patch["value"])
        else:
            raise ValueError("Invalid App patch container")
    if not isinstance(result, dict):
        raise ValueError("Invalid App conversation state")
    return result


def turns(state):
    history = state.get("turnHistory", {})
    if history.get("kind") != "canonical":
        return state.get("turns", [])
    history = history["history"]
    entities = history.get("entitiesByKey", {})
    return [entities[entry["value"]] for island in history.get("islands", [])
            for entry in island.get("entries", []) if entry.get("value") in entities]


class AppSession:
    def __init__(self, path, thread_id, *, on_event, on_request, on_disconnect):
        self.path, self.thread_id = Path(path), thread_id
        self.on_event, self.on_request, self.on_disconnect = on_event, on_request, on_disconnect
        self.stream = self.writer = self.reader = None
        self.client = "initializing-client"
        self.owner = None
        self.requests = {}
        self.write_lock = asyncio.Lock()
        self.snapshot_ready = asyncio.Event()
        self.state = {}
        self.revision = None
        self.enabled = False
        self.closing = False
        self.seen_items = set()
        self.seen_turns = set()
        self.started_turns = set()
        self.pending = {}
        self.interactions = asyncio.Queue()
        self.interaction_worker = None
        self.baseline_first = self.baseline_last = None
        self.attached_at_ms = 0

    @property
    def connected(self):
        return bool(self.reader and not self.reader.done() and not self.closing)

    async def attach(self):
        # Same-user private socket only; no symlink endpoints or shared directories.
        parent, endpoint = self.path.parent.lstat(), self.path.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or parent.st_mode & 0o022 or not stat.S_ISSOCK(endpoint.st_mode)
                or endpoint.st_uid != os.getuid()):
            raise ValueError("App IPC 路径不属于当前用户或权限不安全")
        self.stream, self.writer = await asyncio.wait_for(asyncio.open_unix_connection(
            str(self.path), limit=MAX_FRAME + 4), 3)
        self.reader = asyncio.create_task(self.read())
        result = await self.request("initialize", {"clientType": "codex-wechat"})
        self.client = result["clientId"]
        result = await self.request("thread-owner-discovery", {
            "hostId": "local", "conversationId": self.thread_id}, discover=True)
        self.owner = result["owner"]
        await self.follow(True)
        await asyncio.wait_for(self.snapshot_ready.wait(), 5)
        if self.state.get("id") != self.thread_id or self.state.get("hostId", "local") != "local":
            raise ValueError("App 返回了其它会话")
        # An attachment is a baseline, never a request to replay old replies.
        visible = turns(self.state)
        self.baseline_first = visible[0].get("turnId") if visible else None
        self.baseline_last = visible[-1].get("turnId") if visible else None
        self.attached_at_ms = int(time.time() * 1000)
        for turn in visible:
            if turn.get("status") != "inProgress":
                self.seen_turns.add(turn.get("turnId"))
            for item in turn.get("items", []):
                if self.item_complete(turn, item):
                    self.seen_items.add((turn.get("turnId"), item.get("id")))
        log_event("app_ipc.connected", component="app_ipc", connected=True)

    async def send(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > MAX_FRAME:
            raise CodexRpcError(-32602, "App IPC 请求过大", definite=True)
        async with self.write_lock:
            if not self.writer or self.writer.is_closing():
                raise RuntimeError("App IPC 连接已断开；未自动重发")
            self.writer.write(struct.pack("<I", len(data)) + data)
            await self.writer.drain()

    async def request(self, method, params, *, discover=False):
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.requests[rid] = future
        message = dict(type="request", requestId=rid, sourceClientId=self.client,
                       version=VERSIONS[method], method=method, params=params,
                       timeoutMs=5000 if discover or method == "initialize" else 30000)
        if self.owner and not discover:
            message["targetClientId"] = self.owner
        try:
            await self.send(message)
            response = await asyncio.wait_for(future, message["timeoutMs"] / 1000 + 1)
            if response.get("resultType") != "success":
                reason = response.get("error", "unknown")
                # Router-level refusal is definite. An owner error may follow side effects.
                raise CodexRpcError(-32600, "App IPC: " + str(reason), method,
                    definite=reason in ("no-client-found", "client-cannot-handle-request"))
            if method != "initialize" and not discover and response.get("handledByClientId") != self.owner:
                raise RuntimeError("App IPC 响应来源改变；投递结果未确认")
            if discover:
                return {"owner": response["handledByClientId"]}
            result = response.get("result", {})
            if method == "initialize":
                return result
            if result.get("method") != method:
                raise RuntimeError("App IPC 响应协议不匹配；投递结果未确认")
            return result.get("result", {})
        finally:
            self.requests.pop(rid, None)

    async def follow(self, following):
        await self.send(dict(type="broadcast", method="thread-stream-following-changed",
            sourceClientId=self.client, version=1, targetClientIds=[self.owner], params={
                "hostId": "local", "conversationId": self.thread_id, "following": following}))

    async def read(self):
        try:
            while True:
                size = struct.unpack("<I", await self.stream.readexactly(4))[0]
                if not 0 < size <= MAX_FRAME:
                    raise ValueError("App IPC frame exceeds budget")
                message = json.loads(await self.stream.readexactly(size))
                if message.get("type") == "response":
                    future = self.requests.get(message.get("requestId"))
                    if future and not future.done():
                        future.set_result(message)
                elif message.get("type") == "client-discovery-request":
                    await self.send(dict(type="client-discovery-response",
                        requestId=message["requestId"], response={"canHandle": False}))
                elif message.get("type") == "broadcast":
                    await self.broadcast(message)
        except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError, asyncio.IncompleteReadError):
            pass
        finally:
            error = RuntimeError("App IPC 连接断开；未自动重发，可重试 /switch 或 /backend")
            for future in list(self.requests.values()):
                if not future.done():
                    future.set_exception(error)
            if self.enabled and not self.closing:
                log_event("app_ipc.disconnected", level="warning", component="app_ipc", connected=False)
                self.on_disconnect(error)

    async def broadcast(self, message):
        method, params = message.get("method"), message.get("params", {})
        if message.get("targetClientIds") is not None and self.client not in message["targetClientIds"]:
            return
        if method == "client-status-changed" and params.get("clientId") == self.owner and params.get("status") == "disconnected":
            raise RuntimeError("App owner disconnected")
        if method != "thread-stream-state-changed" or message.get("sourceClientId") != self.owner:
            return
        if params.get("hostId") != "local" or params.get("conversationId") != self.thread_id:
            return
        if message.get("version", 0) != VERSIONS[method]:
            raise ValueError("Unsupported App stream protocol version")
        change = params["change"]
        if change["type"] == "snapshot":
            self.state = change["conversationState"]
        elif change["type"] == "patches":
            if self.revision != change.get("baseRevision"):
                # Ask for a fresh snapshot, never apply a patch to the wrong base.
                await self.follow(True)
                return
            self.state = apply_patches(self.state, change["patches"])
        else:
            raise ValueError("Unknown App stream change")
        if self.state.get("id") != self.thread_id:
            raise ValueError("App stream identity changed")
        self.revision = change["revision"]
        self.snapshot_ready.set()
        if self.enabled:
            self.publish()

    def emit(self, method, **params):
        self.on_event({"method": method, "params": {"threadId": self.thread_id, **params}})

    @staticmethod
    def item_complete(turn, item):
        return (turn.get("status") != "inProgress" or item.get("completedAtMs") is not None
                or bool(turn.get("completedItemIds", {}).get(item.get("id"))))

    def activate(self):
        self.enabled = True
        self.interaction_worker = asyncio.create_task(self.deliver_interactions())
        self.publish()

    async def deliver_interactions(self):
        while True:
            request = await self.interactions.get()
            if request.get("id") in self.pending:
                try:
                    await self.on_request(request)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log_event("app_ipc.request_failed", level="warning",
                              component="app_ipc", error_type=type(error).__name__)

    def publish(self):
        s = self.state
        self.emit("thread/status/changed", status=s.get("threadRuntimeStatus", {"type": "idle"}))
        self.emit("thread/settings/updated", threadSettings={
            **(s.get("latestThreadSettings") or {}), "model": s.get("latestModel"),
            "effort": s.get("latestReasoningEffort"), "cwd": s.get("cwd"),
            "modelProvider": s.get("modelProvider"), "collaborationMode": s.get("latestCollaborationMode")})
        usage = s.get("latestTokenUsageInfo")
        if usage:
            self.emit("thread/tokenUsage/updated", tokenUsage=usage)
        ordered = turns(s)
        ids = [turn.get("turnId") for turn in ordered]
        if self.baseline_last is not None and self.baseline_last in ids:
            ordered = ordered[ids.index(self.baseline_last):]
        elif self.baseline_first is not None and self.baseline_first in ids:
            ordered = ordered[ids.index(self.baseline_first):]
        elif self.baseline_first is not None:
            # The original window was replaced. Do not replay history that
            # starts before the attachment; keep an in-progress turn visible.
            ordered = [turn for turn in ordered
                       if turn.get("status") == "inProgress"
                       or (isinstance(turn.get("turnStartedAtMs"), (int, float))
                           and turn["turnStartedAtMs"] >= self.attached_at_ms)]
        for turn in ordered:
            tid = turn.get("turnId")
            if not tid or tid in self.seen_turns:
                continue
            if tid not in self.started_turns:
                self.started_turns.add(tid)
                self.emit("turn/started", turn={"id": tid, "status": "inProgress"})
            for item in turn.get("items", []):
                key = (tid, item.get("id"))
                if (item.get("id") and key not in self.seen_items and self.item_complete(turn, item)
                        and item.get("type") in ("agentMessage", "userMessage", "contextCompaction")):
                    self.seen_items.add(key)
                    self.emit("item/completed", turnId=tid, item=item)
            if turn.get("status") in ("completed", "failed", "interrupted"):
                self.seen_turns.add(tid)
                self.emit("turn/completed", turn={"id": tid, "status": turn["status"]})
        requests = {r["id"]: r for r in s.get("requests", []) if "id" in r}
        for rid in self.pending.keys() - requests.keys():
            self.emit("serverRequest/resolved", requestId=rid)
        for rid, request in requests.items():
            if rid not in self.pending and request.get("method") in RESPONSES:
                self.interactions.put_nowait(request)
        self.pending = requests

    def thread_result(self):
        s = self.state
        return {"thread": {"id": self.thread_id, "name": s.get("title"),
            "path": s.get("rolloutPath"), "cwd": s.get("cwd"),
            "model": s.get("latestModel"), "reasoningEffort": s.get("latestReasoningEffort"),
            "modelProvider": s.get("modelProvider"),
            "status": s.get("threadRuntimeStatus", {"type": "idle"})},
            **(s.get("latestThreadSettings") or {}),
            "model": s.get("latestModel"), "modelProvider": s.get("modelProvider"),
            "reasoningEffort": s.get("latestReasoningEffort"), "cwd": s.get("cwd"),
            "collaborationMode": s.get("latestCollaborationMode")}

    async def rpc(self, method, params):
        if not self.connected:
            raise RuntimeError("App IPC 已断开；请重新连接会话")
        base = {"conversationId": self.thread_id}
        if method == "thread/read":
            return self.thread_result()
        if method == "thread/turns/list":
            return {"data": [{"id": t["turnId"], "status": t["status"]} for t in reversed(turns(self.state))
                             if t.get("turnId")], "nextCursor": None}
        if method == "turn/start":
            return (await self.request("thread-follower-start-turn", {**base,
                "turnStart": {"request": params, "context": {}}}))["result"]
        if method == "turn/steer":
            active = next((t for t in reversed(turns(self.state)) if t.get("status") == "inProgress"), None)
            if not active or active.get("turnId") != params.get("expectedTurnId"):
                raise CodexRpcError(-32600, "expected turn mismatch", method, definite=True)
            restore = {"id": params.get("clientUserMessageId") or uuid.uuid4().hex,
                "cwd": self.state.get("cwd"), "context": {
                    "workspaceRoots": [self.state.get("cwd") or "/"],
                    "collaborationMode": self.state.get("latestCollaborationMode")}}
            return (await self.request("thread-follower-steer-turn", {**base,
                "input": params["input"], "restoreMessage": restore,
                "clientUserMessageId": params.get("clientUserMessageId")}))["result"]
        if method == "turn/interrupt":
            return await self.request("thread-follower-interrupt-turn", {**base,
                "mode": "user-stop", "expectedTurnId": params["turnId"]})
        if method == "thread/compact/start":
            return await self.request("thread-follower-compact-thread", base)
        raise CodexRpcError(-32601, "App IPC 不支持此操作", method, definite=True)

    async def settings(self, options):
        settings = dict(options)
        config = settings.pop("config", {})
        if config:
            if set(config) != {"model_reasoning_effort"}:
                raise ValueError("App IPC 不支持这些会话配置")
            settings["effort"] = config["model_reasoning_effort"]
        if not settings:
            return
        result = await self.request("thread-follower-update-thread-settings", {
            "conversationId": self.thread_id, "threadSettings": settings})
        if result.get("applied") is not True:
            raise CodexRpcError(-32600, "App 未应用会话设置", definite=True)

    async def respond(self, message):
        request = self.pending.get(message.get("id"))
        if not request or request.get("method") not in RESPONSES:
            raise ValueError("App 请求已失效")
        if "error" in message:
            raise ValueError("不能替 App 自动拒绝未知请求")
        method, key = RESPONSES[request["method"]]
        value = message["result"]
        await self.request(method, {"conversationId": self.thread_id,
            "requestId": request["id"], key: value.get("decision") if key == "decision" else value})

    async def close(self):
        self.closing = True
        self.enabled = False
        if self.writer:
            self.writer.close()
        tasks = [t for t in (self.reader, self.interaction_worker) if t and t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.writer:
            try:
                await asyncio.wait_for(self.writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                pass
