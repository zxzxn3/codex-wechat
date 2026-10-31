"""Codex App Server adapter. Command/env are injectable for future launchers.

Interface used by Bridge: start(), thread(id), run(id, text, attachments=None), stop(),
steer(id, turn_id, text, attachments=None), answer_approval(token, decision), close();
pending contains UI approval data.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from records import Source, TurnResult
from media import OUTPUT_HINT
from usage import UsageCache
from prompts import (
    APPROVAL_TOO_LONG,
    COMPACT_STARTED,
    COMPACT_FINISHED,
    COMPACT_INTERRUPTED,
    TURN_COMPLETED_EMPTY,
    TURN_ENDED_UNEXPECTEDLY,
    TURN_FAILED,
    TURN_INTERRUPTED,
    UNSUPPORTED_INTERACTION,
    command_approval,
    file_approval,
)

from codex_transport import CodexTransport, CodexRpcError

class DeferTurn(RuntimeError):
    """The desktop owns an active turn that did not accept this input."""

class CodexBackend:
    def __init__(self, config, cwd, notify):
        self.config, self.cwd, self.notify = config, cwd, notify
        self.pending = {}
        self.active = None
        self.stop_requested = False
        self.info = {}
        self.usage_by_thread = {}
        self.live_usage_threads = set()
        self.stored_usage = UsageCache()
        self.compactions = {}
        self.manual_compactions = set()
        self.compaction_notice = False
        self.refresh_config = lambda: None
        self.input_hint = lambda: OUTPUT_HINT
        self.on_limits = lambda payload: None
        self.selected_thread = None
        self.blocked = False
        self.runtime_generation = 0
        self.rate_limits_checked_at = 0
        self.on_activity = lambda: None
        self.on_external_result = lambda *args: None
        self.source_name = "codex"
        self.connection_id = uuid.uuid4().hex
        self.selection_epoch = 0
        self.account_limits = None
        self.model_catalog = None
        self.models_checked_at = 0
        self.transport = CodexTransport(config, cwd,
            on_request=self.server_request, on_event=self.event,
            on_connect=self.reset_connection, on_disconnect=self.connection_lost)

    def reset_connection(self):
        self.connection_id = uuid.uuid4().hex
        self.usage_by_thread.clear()
        self.live_usage_threads.clear()
        self.stored_usage.clear()
        self.compactions.clear()
        self.manual_compactions.clear()
        self.model_catalog = None
        self.account_limits = None
        self.rate_limits_checked_at = 0

    def connection_lost(self, error):
        if self.active and not self.active["done"].done():
            if self.active.get("owned"):
                self.active["done"].set_exception(error)
            else:
                self.active["done"].cancel()
        self.pending.clear()
        self.blocked = False
        self.on_activity()

    @property
    def connected(self):
        return self.transport.connected

    async def start(self):
        await self.transport.start()

    async def rpc(self, method, params):
        return await self.transport.rpc(method, params)

    async def write(self, message):
        await self.transport.write(message)

    async def close(self):
        await self.transport.close()

    @property
    def proc(self):
        return self.transport.proc

    @proc.setter
    def proc(self, value):
        self.transport.proc = value

    @property
    def reader(self):
        return self.transport.reader

    @reader.setter
    def reader(self, value):
        self.transport.reader = value

    @property
    def shared(self):
        return self.transport.shared

    @shared.setter
    def shared(self, value):
        self.transport.shared = value

    def source(self, thread_id=None):
        return Source(self.source_name, self.connection_id, self.selection_epoch,
                      thread_id or self.selected_thread)

    async def server_request(self, msg):
        params = msg.get("params", {})
        if self.shared and params.get("threadId") != self.selected_thread:
            # Other desktop sessions keep their own request owner.
            return
        if self.shared and self.active and not self.active.get("owned"):
            # Observing a desktop turn does not transfer its tool/approval request ownership.
            return
        if (self.active and params.get("threadId") == self.active["thread"]
                and msg["method"] in ("item/commandExecution/requestApproval",
                                       "item/fileChange/requestApproval")):
            token = secrets.token_hex(4)
            self.pending[token] = msg
            if msg["method"] == "item/commandExecution/requestApproval":
                summary = command_approval(
                    command=params.get("command") or "未提供",
                    cwd=params.get("cwd") or self.cwd,
                    reason=params.get("reason"),
                    token=token,
                )
            else:
                summary = file_approval(
                    scope=params.get("grantRoot") or params.get("cwd") or self.cwd,
                    reason=params.get("reason"),
                    token=token,
                )
            # Keep the preview complete: reject very large approval requests rather than truncate.
            if len(summary) <= 1200:
                self.notify(summary)
                return
            self.pending.pop(token)
            await self.write({"id": msg["id"], "error": {
                "code": -32601, "message": "This bridge does not support this request"}})
            self.notify(APPROVAL_TOO_LONG)
            return
        await self.write({"id": msg["id"], "error": {
            "code": -32601, "message": "This bridge does not support this request"}})
        self.notify(UNSUPPORTED_INTERACTION)

    def event(self, msg):
        params = msg.get("params", {})
        if msg.get("method") == "serverRequest/resolved":
            for token, request in list(self.pending.items()):
                if request["id"] == params.get("requestId"):
                    self.pending.pop(token, None)
        method = msg.get("method")
        if method == "account/rateLimits/updated":
            update = params.get("rateLimits")
            if isinstance(update, dict):
                current = self.account_limits
                if not isinstance(current, dict):
                    current = {}
                current["rateLimits"] = self.merge_available(
                    current.get("rateLimits"), update)
                self.account_limits = current
                self.on_limits(current)
            return
        if method == "thread/tokenUsage/updated":
            thread_id, usage = params.get("threadId"), params.get("tokenUsage")
            if isinstance(thread_id, str) and isinstance(usage, dict):
                # Resume can publish usage before its response selects the thread.
                # Cache counters by thread rather than discarding that early event.
                self.live_usage_threads.add(thread_id)
                self.usage_by_thread.pop(thread_id, None)
                self.usage_by_thread[thread_id] = usage
                while len(self.usage_by_thread) > 20:
                    self.usage_by_thread.pop(next(iter(self.usage_by_thread)))
                if thread_id == self.selected_thread:
                    self.info["usage"] = usage
            return
        if params.get("threadId") != self.selected_thread:
            return
        item = params.get("item", {})
        if method in ("item/started", "item/completed") and item.get("type") == "contextCompaction":
            self.refresh_config()
            key = (self.selection_epoch, self.selected_thread, params.get("turnId"), item.get("id"))
            previous = self.compactions.get(key)
            phase = "started" if method == "item/started" else "completed"
            if previous not in ("completed", "interrupted") and previous != phase:
                self.compactions[key] = phase
                manual = (self.selection_epoch, self.selected_thread) in self.manual_compactions
                if self.compaction_notice or (manual and phase == "completed"):
                    self.notify(COMPACT_STARTED if phase == "started" else COMPACT_FINISHED)
                while len(self.compactions) > 64:
                    self.compactions.pop(next(iter(self.compactions)))
            return
        if method == "turn/completed":
            turn = params.get("turn", {})
            unfinished = [key for key, phase in self.compactions.items()
                          if phase == "started" and key[:3] ==
                          (self.selection_epoch, self.selected_thread, turn.get("id"))]
            if unfinished:
                for key in unfinished:
                    self.compactions[key] = "interrupted"
                if self.compaction_notice or (self.selection_epoch, self.selected_thread) in self.manual_compactions:
                    self.notify(COMPACT_INTERRUPTED if turn.get("status") != "completed"
                                else "上下文压缩结果未确认。")
        if method == "thread/settings/updated":
            # Native settings are authoritative; no replay of previously observed defaults.
            self.runtime_generation += 1
            settings = params.get("threadSettings", {})
            for source, target in (("model", "model"), ("effort", "effort"), ("cwd", "cwd"),
                    ("modelProvider", "provider"), ("approvalPolicy", "approval"),
                    ("approvalsReviewer", "reviewer"), ("sandboxPolicy", "sandbox"),
                    ("activePermissionProfile", "profile"), ("collaborationMode", "collaboration")):
                if source in settings:
                    self.info[target] = settings[source]
            self.on_activity()
            return
        if method == "thread/name/updated":
            self.info["name"] = params.get("threadName")
            return
        if method in ("turn/started", "turn/completed"):
            self.runtime_generation += 1
        if method == "thread/status/changed":
            status = params.get("status", {})
            self.blocked = bool(status.get("activeFlags")) or status.get("type") == "systemError"
            self.on_activity()
            return
        if (self.active and self.active.get("owned") and self.active["turn"] is None
                and self.active.get("starting")):
            # The start response identifies OUR turn. Do not bind a concurrent desktop
            # notification to it merely because that notification arrived first.
            if method in ("turn/started", "item/completed", "turn/completed"):
                self.active["earlyEvents"].append(msg)
            return
        if method == "turn/started":
            turn_id = params["turn"]["id"]
            if not self.active or self.active["turn"] != turn_id:
                self.active = self.new_turn(self.selected_thread, turn_id, owned=False)
            self.on_activity()
        current = self.active
        if not current:
            return
        if current["turn"] is None:
            return
        if params.get("turnId") and current["turn"] != params["turnId"]:
            return
        if method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                identity = item.get("id")
                if identity and any(message.get("id") == identity for message in current["messages"]):
                    return
                current["messages"].append(item)
        elif method == "turn/completed" and not current["done"].done():
            turn = params["turn"]
            if current["turn"] and turn["id"] != current["turn"]:
                return
            messages = current["messages"]
            finals = [m["text"] for m in messages if m.get("phase") == "final_answer"]
            legacy = [m["text"] for m in messages if not m.get("phase")]
            text = "\n\n".join(finals or legacy)
            status = turn["status"]
            if status != "completed":
                status_text = {
                    "failed": TURN_FAILED,
                    "interrupted": TURN_INTERRUPTED,
                }.get(status, TURN_ENDED_UNEXPECTEDLY)
                text = (text + "\n\n" if text else "") + status_text
            result = TurnResult(current["source"], current["turn"],
                                text or TURN_COMPLETED_EMPTY, current["cwd"], status)
            current["done"].set_result(result)
            self.pending.clear()
            compact_turn = any(key[:3] == (self.selection_epoch, self.selected_thread, turn["id"])
                               for key in self.compactions)
            self.manual_compactions.discard((self.selection_epoch, self.selected_thread))
            if not current.get("owned"):
                if text or not compact_turn:
                    self.on_external_result(result)
                if self.active is current:
                    self.active = None
            self.on_activity()

    def new_turn(self, thread_id, turn_id=None, *, owned):
        return {"thread": thread_id, "turn": turn_id, "messages": [], "owned": owned,
                "starting": False,
                "earlyEvents": [],
                "source": self.source(thread_id),
                "cwd": self.info.get("cwd") or self.cwd,
                "done": asyncio.get_running_loop().create_future()}

    async def refresh_runtime(self):
        if not self.shared or not self.selected_thread:
            return
        generation = self.runtime_generation
        thread = await self.read_thread(self.selected_thread)
        if generation != self.runtime_generation:
            return
        for source, target in (
            ("model", "model"), ("reasoningEffort", "effort"), ("cwd", "cwd"),
            ("modelProvider", "provider"), ("name", "name"), ("path", "path"),
        ):
            if thread.get(source) is not None:
                self.info[target] = thread[source]
        status = thread.get("status", {})
        self.blocked = bool(status.get("activeFlags")) or status.get("type") == "systemError"
        if status.get("type") == "active":
            turns = await self.rpc("thread/turns/list", {"threadId": self.selected_thread,
                "limit": 10, "itemsView": "notLoaded"})
            if generation != self.runtime_generation:
                return
            turn = next((t for t in turns["data"] if t["status"] == "inProgress"), None)
            if turn and (not self.active or self.active["turn"] != turn["id"]):
                self.active = self.new_turn(self.selected_thread, turn["id"], owned=False)
        elif self.active and not self.active.get("owned"):
            if not self.active["done"].done():
                self.active["done"].cancel()
            self.active = None
        self.on_activity()

    @staticmethod
    def merge_available(current, update):
        """Merge a sparse update without clearing previously available fields."""
        merged = dict(current) if isinstance(current, dict) else {}
        if not isinstance(update, dict):
            return merged
        for key, value in update.items():
            if value is None:
                continue
            if isinstance(value, dict):
                merged[key] = CodexBackend.merge_available(merged.get(key), value)
            else:
                merged[key] = value
        return merged

    async def rate_limits(self, max_age=30):
        """Best-effort generic account limit snapshot for status display."""
        now = asyncio.get_running_loop().time()
        cached = self.account_limits
        if self.rate_limits_checked_at and now - self.rate_limits_checked_at < max_age:
            return cached
        if not self.proc or self.proc.returncode is not None:
            return cached
        self.rate_limits_checked_at = now
        try:
            result = await asyncio.wait_for(
                self.rpc("account/rateLimits/read", {}), 3)
        except Exception:
            return cached
        if isinstance(result, dict) and isinstance(result.get("rateLimits"), dict):
            self.account_limits = result
            self.on_limits(result)
        return self.account_limits

    async def thread(self, thread_id=None, overrides=None):
        # Existing sessions keep their own settings; new sessions use configured defaults.
        options = {} if thread_id else {"cwd": self.cwd, **self.config.get("threadOptions", {})}
        overrides = dict(overrides or {})
        effort = overrides.pop("effort", None)
        options.update(overrides)
        if effort is not None:
            options["config"] = {**options.get("config", {}), "model_reasoning_effort": effort}
        if thread_id:
            result = await self.rpc("thread/resume", {**options, "threadId": thread_id,
                                                      "excludeTurns": True})
        else:
            result = await self.rpc("thread/start", options)
        thread = result["thread"]
        previous = self.info if self.info.get("id") == thread["id"] else {}
        self.info = {"id": thread["id"], "name": thread.get("name"), "path": thread.get("path"),
                     "model": result.get("model", thread.get("model")),
                     "provider": result.get("modelProvider",
                                           thread.get("modelProvider")),
                     "effort": result.get("reasoningEffort", thread.get("reasoningEffort")),
                     "cwd": result.get("cwd", thread.get("cwd", self.cwd)),
                     "approval": result.get("approvalPolicy"),
                     "reviewer": result.get("approvalsReviewer"),
                     "sandbox": result.get("sandbox"),
                     "profile": result.get("activePermissionProfile"),
                     "collaboration": result.get(
                         "collaborationMode", previous.get("collaboration")),
                     "usage": self.usage_by_thread.get(thread["id"], previous.get("usage"))}
        if self.selected_thread != thread["id"]:
            self.selection_epoch += 1
            self.active = None
            self.pending.clear()
        self.selected_thread = thread["id"]
        await self.refresh_runtime()
        await self.refresh_usage()
        return result["thread"]["id"]

    async def compact(self, thread_id):
        source = (self.selection_epoch, thread_id)
        self.manual_compactions.add(source)
        try:
            await self.rpc("thread/compact/start", {"threadId": thread_id})
            await self.refresh_runtime()
        except BaseException:
            self.manual_compactions.discard(source)
            raise

    async def refresh_usage(self):
        thread = self.selected_thread
        if not thread or thread in self.live_usage_threads:
            return
        source = self.source(thread)
        usage = await asyncio.to_thread(self.stored_usage.read, self.info.get("path"), thread)
        if usage and source == self.source() and thread not in self.live_usage_threads:
            self.usage_by_thread[thread] = usage
            self.info["usage"] = usage

    async def probe_target(self):
        """Resolve endpoint using native effective config; never read auth secrets."""
        if not self.connected:
            return None
        result = await self.rpc("config/read", {"includeLayers": False,
                                "cwd": self.info.get("cwd") or self.cwd})
        config = result.get("config", {})
        provider = self.info.get("provider") or config.get("model_provider") or "openai"
        settings = (config.get("model_providers") or {}).get(provider, {})
        # The launcher has already applied its environment before exec. Select
        # network variables only; never copy API keys into the probe process.
        keys = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "OPENAI_BASE_URL"}
        keys |= {key.lower() for key in keys}
        selected = {}
        if self.proc and self.proc.returncode is None:
            for entry in Path(f"/proc/{self.proc.pid}/environ").read_bytes().split(b"\0"):
                key, sep, value = entry.partition(b"=")
                name = key.decode(errors="replace")
                if sep and name in keys:
                    selected[name] = value.decode()
        endpoint = settings.get("base_url")
        if not endpoint and provider == "openai":
            endpoint = selected.get("OPENAI_BASE_URL")
            if not endpoint:
                account = await self.rpc("account/read", {"refreshToken": False})
                kind = (account.get("account") or {}).get("type")
                if kind == "chatgpt":
                    endpoint = config.get("chatgpt_base_url") or "https://chatgpt.com"
                elif kind == "apiKey":
                    endpoint = "https://api.openai.com"
        if not isinstance(endpoint, str):
            return None
        proxy_keys = keys - {"OPENAI_BASE_URL", "openai_base_url"}
        env = {k: v for k, v in os.environ.items() if k in
               {"PATH", "HOME", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"}}
        env.update({k: v for k, v in selected.items() if k in proxy_keys})
        return endpoint, env

    async def list_threads(self, cursor=None, search=None):
        params = {"limit": 10, "sortKey": "updated_at", "cursor": cursor,
                  "sourceKinds": ["cli", "vscode", "appServer"]}
        if search:
            params["searchTerm"] = search
        return await self.rpc("thread/list", params)

    async def read_thread(self, thread_id):
        result = await self.rpc("thread/read", {"threadId": thread_id})
        return result["thread"]

    async def models(self, max_age=60):
        if self.model_catalog is not None and time.monotonic() - self.models_checked_at < max_age:
            return self.model_catalog
        data, cursor = [], None
        while True:
            result = await self.rpc("model/list", {"limit": 100, "cursor": cursor})
            data.extend(result["data"])
            cursor = result.get("nextCursor")
            if not cursor:
                self.model_catalog = data
                self.models_checked_at = time.monotonic()
                return data

    async def input_items(self, text, attachments=None):
        inputs = [{"type": "text", "text": text}] if text else []
        if attachments:
            images = [asset for asset in attachments if asset["kind"] == "image"]
            references = [asset for asset in attachments if asset["kind"] != "image"]
            if images:
                catalog = await self.models()
                model = next((m for m in catalog if m["model"] == self.info.get("model")), {})
                if "image" in model.get("inputModalities", ["text", "image"] if model else []):
                    inputs.extend({"type": "localImage", "path": asset["path"]} for asset in images)
                else:
                    references.extend(images)
                    inputs.append({"type": "text", "text":
                        "当前模型未声明图片输入支持，本轮只提供图片路径；如无法读取，请说明并建议切换视觉模型。"})
            if references:
                inputs.append({"type": "text", "text": json.dumps(
                    {"attachments": references}, ensure_ascii=False, separators=(",", ":"))})
        hint = self.input_hint()
        if hint:
            inputs.append({"type": "text", "text": hint})
        return inputs

    async def steer(self, thread_id, turn_id, text, attachments=None, *,
                    client_id=None, dispatch=None):
        current = self.active
        if (not current or current["thread"] != thread_id or current["turn"] != turn_id
                or current["done"].done() or self.stop_requested):
            return False
        inputs = await self.input_items(text, attachments)
        if self.active is not current or current["done"].done() or self.stop_requested:
            return False
        if dispatch and not dispatch("dispatching", turn_id):
            return False
        try:
            await self.rpc("turn/steer", {"threadId": thread_id,
                "expectedTurnId": turn_id, "input": inputs,
                **({"clientUserMessageId": client_id} if client_id else {})})
        except CodexRpcError as error:
            if error.definite_rejection:
                return False
            raise
        if dispatch:
            dispatch("accepted", turn_id)
        return True

    async def run(self, thread_id, text, attachments=None, *, client_id=None, dispatch=None,
                  allow_steer=True):
        # Claim preparation before model lookup; stop is remembered across awaits.
        if self.active and not self.active["done"].done():
            if not allow_steer:
                raise DeferTurn("排队消息等待当前轮次结束")
            accepted = await self.steer(thread_id, self.active["turn"], text, attachments,
                                        client_id=client_id, dispatch=dispatch)
            if accepted:
                return None
            if self.active and not self.active["done"].done():
                raise DeferTurn("活动轮次暂不接受补充")
        current = self.new_turn(thread_id, owned=True)
        self.active = current
        self.stop_requested = False
        try:
            await self.refresh_runtime()
            current["cwd"] = self.info.get("cwd") or self.cwd
            inputs = await self.input_items(text, attachments)
            if self.stop_requested:
                return TurnResult(current["source"], None, "已停止，未启动模型轮次。",
                                  current["cwd"], "interrupted")
            if self.active is not current:
                if not allow_steer:
                    raise DeferTurn("排队消息等待桌面轮次结束")
                if self.active and await self.steer(thread_id, self.active["turn"], text,
                        attachments, client_id=client_id, dispatch=dispatch):
                    return None
                raise DeferTurn("桌面会话状态已改变")
            if dispatch and not dispatch("dispatching", None):
                return TurnResult(current["source"], None, "已停止，未启动模型轮次。",
                                  current["cwd"], "interrupted")
            current["starting"] = True
            response = await self.rpc("turn/start", {"threadId": thread_id,
                "input": inputs,
                **({"clientUserMessageId": client_id} if client_id else {})})
            current["turn"] = response["turn"]["id"]
            if dispatch:
                dispatch("accepted", current["turn"])
            for event in current["earlyEvents"]:
                params = event.get("params", {})
                event_turn = params.get("turnId") or params.get("turn", {}).get("id")
                if event_turn == current["turn"]:
                    self.event(event)
            current["earlyEvents"].clear()
            if self.stop_requested and not current["done"].done():
                await self.stop()
            return await current["done"]
        except CodexRpcError as error:
            if error.definite_rejection:
                await self.refresh_runtime()
                if self.active and self.active is not current and not self.active["done"].done():
                    raise DeferTurn("桌面抢先启动了轮次，稍后重试本条消息") from None
            raise
        finally:
            if self.active is current:
                self.active = None
                self.pending.clear()
            self.stop_requested = False

    async def stop(self):
        if not self.active:
            return False
        self.stop_requested = True
        if not self.active["turn"]:
            return True
        await self.rpc("turn/interrupt", {"threadId": self.active["thread"],
                                           "turnId": self.active["turn"]})
        return True

    async def answer_approval(self, token, decision):
        request = self.pending.pop(token, None)
        if not request:
            return False
        await self.write({"id": request["id"], "result": {"decision": decision}})
        return True
