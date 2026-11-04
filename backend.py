"""Codex App Server adapter. Command/env are injectable for future launchers.

Interface used by Bridge: start(), thread(id), run(id, text, attachments=None), stop(),
steer(id, turn_id, text, attachments=None), answer_approval(token, decision), close();
pending contains UI approval data.
"""
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from records import AgentMessage, Source, TurnResult
from media import OUTPUT_HINT
from diagnostics import error_reason, log_event
from usage import UsageCache
from questions import (
    ASYNC_QUESTION_METHOD,
    QUESTION_METHOD,
    async_question_reply,
    async_questions,
    async_reply_question_ids,
    question_answer,
    question_prompt,
)
from prompts import (prompt_text)
from platform_support import IS_WINDOWS
from prompts import (command_approval, file_approval)

from codex_transport import CodexTransport, CodexRpcError

PENDING_TOKEN_ALPHABET = "023456789abcdefghjkmnpqrstuvwxyz"
PENDING_TOKEN_LENGTH = 3
PENDING_TOKEN_RECENT = 128
# A turn that reports nothing for this long is treated as stalled. Disconnects
# already fail the turn; this covers "connection alive but events stopped",
# which would otherwise freeze the delivery worker until the service restarts.
TURN_IDLE_SECONDS = 900

# Writer ownership for the selected thread. NONE means this connection does not
# hold it; only the two WAITING modes are retried at the start of the next turn.
WRITER_NATIVE = "native"        # this connection holds the writer
WRITER_FOLLOWED = "followed"    # the App holds it and its event stream feeds us
WRITER_RELEASED = "released"    # handed back after a turn; take it back next turn
WRITER_BUSY = "busy"            # the desktop holds it and we could not follow


class DeferTurn(RuntimeError):
    """The desktop owns an active turn that did not accept this input."""


class TurnStalled(RuntimeError):
    """The turn stopped reporting events; it was abandoned locally."""

class CodexBackend:
    def __init__(self, config, cwd, notify):
        self.config, self.cwd, self.notify = config, cwd, notify
        self.pending = {}
        self.recent_tokens = deque(maxlen=PENDING_TOKEN_RECENT)
        self.active = None
        # Who holds the writer for the selected thread. Exactly one field, so the
        # four cases below cannot drift out of sync with each other.
        self.writer_mode = None
        self.writer_thread = None
        self.attach_error = None
        self.last_event_at = time.monotonic()
        self.turn_idle_seconds = TURN_IDLE_SECONDS
        self.stop_requested = False
        self.info = {}
        self.usage_by_thread = {}
        self.live_usage_threads = set()
        self.stored_usage = UsageCache()
        self.compactions = {}
        self.manual_compactions = set()
        self.compaction_notice = False
        self.refresh_config = lambda: None
        self.prompt = prompt_text
        self.input_hint = lambda: OUTPUT_HINT
        self.on_limits = lambda payload: None
        self.selected_thread = None
        self.blocked = False
        self.runtime_generation = 0
        self.rate_limits_checked_at = 0
        self.on_activity = lambda: None
        self.on_connection = lambda connected, error=None: None
        self.on_external_result = lambda *args: None
        self.on_message = lambda message: None
        self.source_name = "codex"
        self.connection_id = uuid.uuid4().hex
        self.selection_epoch = 0
        self.account_limits = None
        self.model_catalog = None
        self.models_checked_at = 0
        self.app = None
        self.transport = CodexTransport(config, cwd,
            on_request=self.server_request, on_event=self.transport_event,
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
        self.on_connection(False, error)
        self.on_activity()

    @property
    def connected(self):
        return self.transport.connected and (not self.app or self.app.connected)

    async def start(self):
        await self.transport.start()

    async def rpc(self, method, params):
        if self.app and method != "thread/resume" and params.get("threadId") == self.app.thread_id:
            return await self.app.rpc(method, params)
        return await self.transport.rpc(method, params)

    async def write(self, message):
        if self.app and "id" in message:
            return await self.app.respond(message)
        await self.transport.write(message)

    async def close(self):
        app, self.app = self.app, None
        if app:
            await app.close()
        await self.transport.close()

    def set_writer(self, mode, thread_id=None):
        self.writer_mode, self.writer_thread = mode, thread_id

    def writer_elsewhere(self, thread_id):
        """True when the next turn must take the writer back for this thread."""
        return (self.writer_thread == thread_id
                and self.writer_mode in (WRITER_RELEASED, WRITER_BUSY))

    async def attach_follower(self, thread_id, options, *, activate=True):
        """Attach to the App's copy of a thread and make it the selection.

        Both entry points use this (a resume writer conflict and a thread we
        handed back), so session setup and event publishing cannot drift apart.
        Callers that select a runtime turn first pass activate=False.
        """
        app = await self.attach_app(thread_id, options)
        if app is None:
            return None
        previous_app, self.app = self.app, app
        if previous_app and previous_app is not app:
            await previous_app.close()
        if self.selected_thread != thread_id:
            self.selection_epoch += 1
            self.active = None
            self.pending.clear()
        self.selected_thread = thread_id
        self.attach_error = None
        self.set_writer(WRITER_FOLLOWED, thread_id)
        if activate:
            self.activate_follower(app)
        return app

    @staticmethod
    def activate_follower(app):
        if not app.enabled:
            # Without this the follower stays silent: broadcast() only publishes
            # state changes while enabled, so desktop turns would never reach us.
            app.activate()

    def transport_event(self, message):
        # The App owns this live stream. The other backend may still publish
        # stale thread metadata; account-wide events keep their original route.
        if self.app and message.get("params", {}).get("threadId") == self.app.thread_id:
            return
        self.event(message)

    async def attach_app(self, thread_id, options):
        from app_ipc import AppSession, ipc_path
        path = ipc_path(self.config)
        if path is None or (not IS_WINDOWS and not path.exists()):
            return None
        candidate = None
        candidate = AppSession(path, thread_id,
            on_event=lambda event: self.event(event) if self.app is candidate else None,
            on_request=lambda request: self.app_request(candidate, request),
            on_disconnect=lambda error: self.connection_lost(error) if self.app is candidate else None)
        try:
            await candidate.attach()
            await candidate.settings(options)
        except FileNotFoundError:
            # Missing App endpoint does not turn a native writer conflict into
            # a guessed attachment or a new independent writer.
            await candidate.close()
            return None
        except (CodexRpcError, ValueError) as error:
            # No capable owner, a foreign relay or an unusable endpoint: keep the
            # caller's own conflict reason instead of an attach detail.
            await candidate.close()
            reason = error_reason(error) or type(error).__name__
            if reason != self.attach_error:
                # The idle retry would otherwise repeat the same warning.
                self.attach_error = reason
                self.writer_event("backend.app_attach_failed", level="warning",
                                  thread=thread_id, status="failed",
                                  error_type=type(error).__name__, reason=reason)
            return None
        except BaseException:
            await candidate.close()
            raise
        return candidate

    async def app_request(self, candidate, request):
        if self.app is candidate:
            await self.server_request(request)

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

    def _new_token(self):
        while True:
            token = "".join(secrets.choice(PENDING_TOKEN_ALPHABET)
                            for _ in range(PENDING_TOKEN_LENGTH))
            if token not in self.pending and token not in self.recent_tokens:
                self.recent_tokens.append(token)
                return token

    async def server_request(self, msg):
        params = msg.get("params", {})
        if self.shared and params.get("threadId") != self.selected_thread:
            # Other desktop sessions keep their own request owner.
            return
        if msg["method"] == QUESTION_METHOD and params.get("threadId") == self.selected_thread:
            questions = params.get("questions", [])
            if (questions and all(isinstance(q, dict) and isinstance(q.get("id"), str)
                                  and isinstance(q.get("question"), str) for q in questions)
                    and len({q["id"] for q in questions}) == len(questions)
                    and (not self.active or not self.active["turn"]
                         or params.get("turnId") == self.active["turn"])):
                # Questions may belong to a desktop turn. Answer only requests actually
                # delivered to this connection; leave other interaction ownership intact.
                if any(request.get("id") == msg["id"] for request in self.pending.values()):
                    return
                token = self._new_token()
                self.pending[token] = dict(msg, _question={
                    "source": self.source(), "index": 0, "answers": {}})
                self.notify(question_prompt(questions[0], token, 0, len(questions), text=self.prompt))
                return
        if self.shared and not self.app and self.active and not self.active.get("owned"):
            # Observing a desktop turn does not transfer its tool/approval request ownership.
            return
        if (self.active and params.get("threadId") == self.active["thread"]
                and msg["method"] in ("item/commandExecution/requestApproval",
                                       "item/fileChange/requestApproval")):
            token = self._new_token()
            self.pending[token] = msg
            if msg["method"] == "item/commandExecution/requestApproval":
                summary = command_approval(
                    command=params.get("command") or self.prompt("COMMAND_NOT_PROVIDED"),
                    cwd=params.get("cwd") or self.cwd,
                    reason=params.get("reason"),
                    token=token,
                    text=self.prompt,
                )
            else:
                summary = file_approval(
                    scope=params.get("grantRoot") or params.get("cwd") or self.cwd,
                    reason=params.get("reason"),
                    token=token,
                    text=self.prompt,
                )
            # Keep the preview complete: reject very large approval requests rather than truncate.
            if len(summary) <= 1200:
                self.notify(summary)
                return
            self.pending.pop(token)
            await self.write({"id": msg["id"], "error": {
                "code": -32601, "message": "This bridge does not support this request"}})
            self.notify(self.prompt("APPROVAL_TOO_LONG"))
            return
        await self.write({"id": msg["id"], "error": {
            "code": -32601, "message": "This bridge does not support this request"}})
        self.notify(self.prompt("UNSUPPORTED_INTERACTION"))

    def event(self, msg):
        params = msg.get("params", {})
        self.last_event_at = time.monotonic()
        if msg.get("method") == "serverRequest/resolved":
            for token, request in list(self.pending.items()):
                if (request.get("id") == params.get("requestId") and
                        request.get("params", {}).get("threadId") == params.get("threadId")):
                    self.pending.pop(token, None)
        method = msg.get("method")
        if method == "account/rateLimits/updated":
            self._update_rate_limits(params)
            return
        if method == "thread/tokenUsage/updated":
            self._update_token_usage(params)
            return
        if params.get("threadId") != self.selected_thread:
            return
        item = params.get("item", {})
        if method == "item/completed":
            answered = async_reply_question_ids(item)
            if answered:
                for token, request in list(self.pending.items()):
                    state = request.get("_async")
                    if state and any(question["id"] in answered
                                     for question in state["questions"]):
                        self.pending.pop(token, None)
        if method in ("item/started", "item/completed") and item.get("type") == "contextCompaction":
            self._handle_compaction(method, params, item)
            return
        if method == "turn/completed":
            self._interrupt_compactions(params)
        if method == "thread/settings/updated":
            self._update_thread_settings(params)
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
        if current["done"].done():
            return  # No late item may become progress for an already-ended turn.
        if params.get("turnId") and current["turn"] != params["turnId"]:
            return
        self._consume_turn_event(current, method, params, item)

    def _handle_compaction(self, method, params, item):
        self.refresh_config()
        key = (self.selection_epoch, self.selected_thread, params.get("turnId"), item.get("id"))
        previous = self.compactions.get(key)
        phase = "started" if method == "item/started" else "completed"
        if previous not in ("completed", "interrupted") and previous != phase:
            self.compactions[key] = phase
            manual = (self.selection_epoch, self.selected_thread) in self.manual_compactions
            if self.compaction_notice or (manual and phase == "completed"):
                self.notify(self.prompt("COMPACT_STARTED") if phase == "started" else self.prompt("COMPACT_FINISHED"))
            while len(self.compactions) > 64:
                self.compactions.pop(next(iter(self.compactions)))

    def _interrupt_compactions(self, params):
        turn = params.get("turn", {})
        unfinished = [key for key, phase in self.compactions.items()
                      if phase == "started" and key[:3] ==
                      (self.selection_epoch, self.selected_thread, turn.get("id"))]
        if unfinished:
            for key in unfinished:
                self.compactions[key] = "interrupted"
            if self.compaction_notice or (self.selection_epoch, self.selected_thread) in self.manual_compactions:
                self.notify(self.prompt("COMPACT_INTERRUPTED") if turn.get("status") != "completed"
                            else self.prompt("COMPACT_UNCONFIRMED"))

    def _consume_turn_event(self, current, method, params, item):
        if method == "item/completed":
            self._record_agent_message(current, item)
        elif method == "turn/completed" and not current["done"].done():
            self._complete_turn(current, params["turn"])

    def _update_rate_limits(self, params):
        update = params.get("rateLimits")
        if isinstance(update, dict):
            current = self.account_limits
            if not isinstance(current, dict):
                current = {}
            current["rateLimits"] = self.merge_available(
                current.get("rateLimits"), update)
            self.account_limits = current
            self.on_limits(current)

    def _update_token_usage(self, params):
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

    def _update_thread_settings(self, params):
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

    def _record_agent_message(self, current, item):
        if item.get("type") == "agentMessage":
            identity = item.get("id")
            if identity and any(message.get("id") == identity for message in current["messages"]):
                return
            current["messages"].append(item)
            questions = async_questions(item)
            if questions is not None:
                token = self._new_token()
                self.pending[token] = {
                    "method": ASYNC_QUESTION_METHOD,
                    "_async": {
                        "source": current["source"],
                        "thread": current["thread"],
                        "turn": current["turn"],
                        "item": identity,
                        "questions": questions,
                        "index": 0,
                        "answers": [],
                    },
                }
                self.notify(question_prompt(questions[0], token, 0, len(questions),
                                            text=self.prompt))
            elif item.get("phase") in ("commentary", "partial_answer") and item.get("text"):
                self.on_message(AgentMessage(current["source"], current["turn"],
                    identity, item["text"], current["cwd"], item["phase"],
                    tuple(current.get("requestIds", ()))))

    def _complete_turn(self, current, turn):
        if current["turn"] and turn["id"] != current["turn"]:
            return
        messages = current["messages"]
        finals = [m["text"] for m in messages
                  if async_questions(m) is None and m.get("phase") == "final_answer"]
        legacy = [m["text"] for m in messages
                  if async_questions(m) is None and not m.get("phase")]
        text = "\n\n".join(finals or legacy)
        has_partial_answer = any(async_questions(m) is None
                                 and m.get("phase") == "partial_answer" and m.get("text")
                                 for m in messages)
        status = turn["status"]
        if status != "completed":
            status_text = {
                "failed": self.prompt("TURN_FAILED"),
                "interrupted": self.prompt("TURN_INTERRUPTED"),
            }.get(status, self.prompt("TURN_ENDED_UNEXPECTEDLY"))
            text = (text + "\n\n" if text else "") + status_text
        result = TurnResult(current["source"], current["turn"],
                            text if text or has_partial_answer else self.prompt("TURN_COMPLETED_EMPTY"),
                            current["cwd"], status, tuple(current.get("requestIds", ())))
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
        if not self.connected:
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
        app = None
        if thread_id and self.app and self.app.thread_id == thread_id and self.app.connected:
            await self.app.settings(options)
            app = self.app
            result = app.thread_result()
        elif thread_id:
            try:
                result = await self.rpc("thread/resume", {
                    **options, "threadId": thread_id, "excludeTurns": True})
            except CodexRpcError as error:
                # Only this explicit writer conflict permits the App follower path.
                # Other RPC failures must never silently change transports.
                if error.code != -32600 or "already has an active writer" not in error.reason:
                    raise
                app = await self.attach_follower(thread_id, options, activate=False)
                if app is None:
                    raise
                result = app.thread_result()
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
        if app is None:
            # Native writer path: any follower from the previous selection is gone.
            previous_app, self.app = self.app, None
            if previous_app:
                await previous_app.close()
            self.set_writer(WRITER_NATIVE, thread["id"])
        await self.refresh_runtime()
        if app is not None:
            # Requests are queued by activate(). Establish the selected runtime
            # turn first so a pending approval is not rejected as unsupported.
            self.activate_follower(app)
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
        # This is a bridge-originated connection probe, not model-request RTT.
        # Attached servers may have a different environment; never inspect their
        # credentials or claim that a local HEAD reproduces their network path.
        keys = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "OPENAI_BASE_URL"}
        keys |= {key.lower() for key in keys}
        selected = {}
        if IS_WINDOWS or self.transport.websocket_url:
            selected = dict(self.transport.launch_env)
        elif self.proc and self.proc.returncode is None:
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
               {"PATH", "HOME", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE",
                "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA"}}
        env.update({k: v for k, v in selected.items() if k in proxy_keys})
        # A backend without proxy variables follows the machine setting, so the
        # probe mirrors that rather than reporting a direct path it never uses.
        proxy_variables = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                           "http_proxy", "https_proxy", "all_proxy"}
        if not any(env.get(name) for name in proxy_variables):
            from platform_support import system_proxy
            env.update(system_proxy())
        return endpoint, env

    async def release_thread(self, thread_id, *, follow=False):
        """Drop the writer this client holds for a thread.

        The app-server exposes no unload call; unsubscribing is the only way to
        stop holding the writer, which is what leaves another client (the App)
        looking at a locked thread. Best effort: an older server that does not
        know the method must not fail the caller, and a followed App thread is
        left alone because its events still arrive through IPC.
        """
        if not thread_id or not self.connected or self.active is not None or self.app is not None:
            if thread_id:
                self.writer_event("backend.writer_kept", thread=thread_id,
                                  status=("followed" if self.app is not None else
                                          "busy" if self.active is not None else "offline"))
            return False
        try:
            await self.rpc("thread/unsubscribe", {"threadId": thread_id})
        except (CodexRpcError, OSError, RuntimeError) as error:
            self.writer_event("backend.writer_release_failed", level="warning",
                              thread=thread_id, status="failed",
                              error_type=type(error).__name__)
            return False
        self.set_writer(WRITER_RELEASED, thread_id)
        self.writer_event("backend.writer_released", thread=thread_id, status="released")
        if follow:
            # The App may still be running: follow it so desktop turns keep
            # reaching WeChat while this client holds no writer at all.
            await self.follow_app(thread_id)
        return True

    async def follow_app(self, thread_id):
        """Follow the desktop's copy of a thread we just let go.

        A released thread stops feeding this connection, so a desktop turn would
        be invisible until the next inbound message. Re-attaching as an App
        follower restores that stream without taking the writer back.
        """
        if not thread_id or not self.connected or self.active is not None or self.app is not None:
            return False
        if not self.app_available():
            return False
        try:
            app = await self.attach_follower(thread_id, {})
        except (CodexRpcError, OSError, RuntimeError, asyncio.TimeoutError) as error:
            # Following is best effort: it must never fail the caller's turn or
            # the bridge startup.
            self.writer_event("backend.app_attach_failed", level="warning",
                              thread=thread_id, status="failed",
                              error_type=type(error).__name__)
            return False
        if app is None:
            return False
        self.writer_event("backend.app_followed", thread=thread_id, status="followed")
        return True

    def app_available(self):
        from app_ipc import ipc_path
        path = ipc_path(self.config)
        if path is None:
            return False
        try:
            return Path(path).exists()
        except OSError:
            return False

    def writer_event(self, event, **fields):
        report = getattr(self, "report", None)
        if report:
            report(event, **fields)

    async def list_threads(self, cursor=None, search=None):
        params = {"limit": 10, "sortKey": "updated_at", "cursor": cursor,
                  "sourceKinds": ["cli", "vscode", "appServer"]}
        if search:
            params["searchTerm"] = search
        return await self.rpc("thread/list", params)

    async def read_thread(self, thread_id):
        result = await self.rpc("thread/read", {"threadId": thread_id})
        return result["thread"]

    async def adopt_thread(self, thread_id, thread=None):
        """Take a thread as the selection without holding or following its writer.

        Used when the desktop owns it and its IPC cannot be followed: the bridge
        stays usable, keeps retrying, and reports the conflict per message
        instead of failing backend recovery in a loop.
        """
        thread = thread or {}
        previous = self.info if self.info.get("id") == thread_id else {}
        self.info = {"id": thread_id, "name": thread.get("name"),
                     "path": thread.get("path"),
                     "model": thread.get("model", previous.get("model")),
                     "provider": thread.get("modelProvider", previous.get("provider")),
                     "effort": thread.get("reasoningEffort", previous.get("effort")),
                     "cwd": thread.get("cwd") or previous.get("cwd") or self.cwd,
                     "approval": thread.get("approvalPolicy", previous.get("approval")),
                     "sandbox": thread.get("sandbox", previous.get("sandbox")),
                     "collaboration": thread.get("collaborationMode",
                                                 previous.get("collaboration")),
                     "usage": self.usage_by_thread.get(thread_id, previous.get("usage"))}
        if self.selected_thread != thread_id:
            self.selection_epoch += 1
            self.active = None
            self.pending.clear()
        self.selected_thread = thread_id
        self.set_writer(WRITER_BUSY, thread_id)
        return thread_id

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

    async def input_items(self, text, attachments=None, *, hint=True):
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
        if hint:
            source_hint = self.input_hint()
            if source_hint:
                inputs.append({"type": "text", "text": source_hint})
        return inputs

    async def steer(self, thread_id, turn_id, text, attachments=None, *,
                    client_id=None, dispatch=None, hint=True):
        current = self.active
        if (not current or current["thread"] != thread_id or current["turn"] != turn_id
                or current["done"].done() or self.stop_requested):
            return False
        inputs = await self.input_items(text, attachments, hint=hint)
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

    async def await_turn(self, current):
        """Wait for the turn, aborting it when nothing reports for too long.

        A broken event stream leaves an owned turn pending forever, which freezes
        the delivery worker and makes /stop look ineffective. Give up locally so
        the next message can re-establish the session.
        """
        limit = self.turn_idle_seconds
        while True:
            idle = max(1.0, limit - (time.monotonic() - self.last_event_at))
            try:
                return await asyncio.wait_for(asyncio.shield(current["done"]), idle)
            except asyncio.TimeoutError:
                if current["done"].done():
                    return current["done"].result()
                if time.monotonic() - self.last_event_at < limit:
                    continue
                await self.abandon_turn(current)
                raise TurnStalled("回合长时间没有进展，已在本地中止") from None

    async def abandon_turn(self, current):
        log_event("backend.turn_stalled", level="warning", component="backend",
                  thread=current.get("thread"), turn=current.get("turn"),
                  idle_ms=int((time.monotonic() - self.last_event_at) * 1000))
        if self.active is current:
            self.active = None
        if not current["done"].done():
            current["done"].cancel()
        self.pending.clear()
        app, self.app = self.app, None
        if app is not None:
            # The follower never published, so stop pretending to follow it.
            await app.close()

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
            if self.writer_elsewhere(thread_id):
                # The writer was handed back (or never taken): take it back, or
                # follow the desktop if it claimed the thread meanwhile.
                previous = self.writer_mode
                self.set_writer(None)
                try:
                    await self.thread(thread_id)
                except Exception:
                    # Keep watching: the desktop still owns the thread.
                    self.set_writer(previous, thread_id)
                    raise
            await self.refresh_runtime()
            current["cwd"] = self.info.get("cwd") or self.cwd
            inputs = await self.input_items(text, attachments)
            if self.stop_requested:
                return TurnResult(current["source"], None, self.prompt("PREPARATION_STOPPED"),
                                  current["cwd"], "interrupted")
            if self.active is not current:
                if not allow_steer:
                    raise DeferTurn("排队消息等待桌面轮次结束")
                if self.active and await self.steer(thread_id, self.active["turn"], text,
                        attachments, client_id=client_id, dispatch=dispatch):
                    return None
                raise DeferTurn("桌面会话状态已改变")
            if dispatch and not dispatch("dispatching", None):
                return TurnResult(current["source"], None, self.prompt("PREPARATION_STOPPED"),
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
            return await self.await_turn(current)
        except CodexRpcError as error:
            early_events = list(current["earlyEvents"])
            if error.definite_rejection:
                await self.refresh_runtime()
                if self.active and self.active is not current and not self.active["done"].done():
                    desktop_turn = self.active["turn"]
                    for event in early_events:
                        params = event.get("params", {})
                        event_turn = params.get("turnId") or params.get("turn", {}).get("id")
                        if event_turn == desktop_turn:
                            self.event(event)
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
        if self.pending.get(token, {}).get("method") in (QUESTION_METHOD, ASYNC_QUESTION_METHOD):
            return False
        request = self.pending.get(token)
        if not request:
            return False
        if "id" not in request:
            return False
        self.pending.pop(token, None)
        await self.write({"id": request["id"], "result": {"decision": decision}})
        return True

    async def answer_question(self, token, text):
        request = self.pending.get(token)
        if not request:
            return False
        if request.get("method") == ASYNC_QUESTION_METHOD:
            return await self.answer_async_question(token, request, text)
        if request.get("method") != QUESTION_METHOD:
            return False
        state = request["_question"]
        if state["source"] != self.source():
            self.pending.pop(token, None)
            return False
        questions = request["params"]["questions"]
        index = state["index"]
        question = questions[index]
        answer = question_answer(question, text, render=self.prompt)
        answers = {**state["answers"], question["id"]: {"answers": [answer]}}
        # Consume before writing: a transport failure is uncertain, never auto-resend.
        self.pending.pop(token)
        if index + 1 == len(questions):
            await self.write({"id": request["id"], "result": {"answers": answers}})
        else:
            state.update(index=index + 1, answers=answers)
            next_token = self._new_token()
            self.pending[next_token] = request
            self.notify(question_prompt(questions[index + 1], next_token, index + 1, len(questions),
                                        text=self.prompt))
        return True

    async def answer_async_question(self, token, request, text):
        state = request["_async"]
        current = self.active
        if (state["source"] != self.source() or not current
                or current["thread"] != state["thread"] or current["turn"] != state["turn"]
                or current["done"].done()):
            self.pending.pop(token, None)
            return False
        questions = state["questions"]
        index = state["index"]
        answer = question_answer(questions[index], text, render=self.prompt)
        answers = [*state["answers"], {
            "questionItemId": questions[index]["id"],
            "question": questions[index]["question"],
            "answer": answer,
        }]
        # Consume before steering: once sent, a transport error is uncertain and
        # must not replay an answer.
        self.pending.pop(token)
        if index + 1 < len(questions):
            state.update(index=index + 1, answers=answers)
            next_token = self._new_token()
            self.pending[next_token] = request
            self.notify(question_prompt(questions[index + 1], next_token, index + 1,
                                        len(questions), text=self.prompt))
            return True
        return await self.steer(state["thread"], state["turn"],
                                async_question_reply(answers), hint=False)
