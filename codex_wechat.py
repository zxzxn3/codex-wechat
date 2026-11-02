#!/usr/bin/env python3
"""Local WeChat ↔ Codex bridge. Media transport uses cryptography."""
import argparse
import asyncio
import codecs
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

import service_adapter
import service_control
import wechat_auth
from backend import CodexBackend
from output_delivery import OutputDelivery
from input_delivery import InputDelivery
from storage import Store
from wechat import WeChat, login
from commands import (
    ensure_session,
    dispatch,
    notice,
    parse_control,
)
from command_specs import REGISTRY, CONTROL_LANES, command_lane
from configuration import ConfigLoader, ConfigurationError, HotSettings
from prompts import (prompt_text)
from quota import quota_warnings
from records import InputPhase
from profiles import backend_label, definitions, snapshot
from rebind import run_rebind
from media import Media, MAX_ATTACHMENTS
from latency import Latency, message_timestamps
from typing_indicator import TypingIndicator
from wechat_content import (
    message_key,
    message_summary,
    message_text,
    quote_context,
    quoted_prompt,
    remember_quote,
)
from errors import UserFacingError, BackendStartupError, WeChatAuthExpired, backend_reason
from platform_support import IS_WINDOWS, data_root
from diagnostics import BACKUP_COUNT, error_fields, log_event, log_path, new_request_id
from prompts import (render_ack, status_fields)

ROOT = Path(__file__).resolve().parent


class Bridge:
    def __init__(self, store, account, config, backend_factory=CodexBackend, channel=None, config_path=None):
        self.store, self.account, self.config = store, account, config
        self.settings = ConfigLoader(config, config_path)
        self.state = store.load("bridge", {"cursor": "", "thread": None,
            "inbox": [], "outbox": [], "seen": [], "running": False})
        self.state.setdefault("consecutiveSendFailures", 0)
        self.state.setdefault("quoteCache", {})
        self.wechat_paused = wechat_auth.state(self.state) == wechat_auth.AUTH_REQUIRED
        self.auth_wake = asyncio.Event()
        self.channel = channel or WeChat(account)
        self.media = Media(store, self.channel, config.get("media"))
        self.backend_factory = backend_factory
        self.backend_configs = definitions(config)
        self.backend_name = self.state.get("backendName", "codex")
        if self.backend_name not in self.backend_configs:
            raise ValueError("保存的后端不存在于配置中：" + self.backend_name)
        self.controls = {lane: asyncio.Queue(maxsize=20) for lane in CONTROL_LANES}
        self.connection_lock = asyncio.Lock()
        self.input_paused = False
        self.backend_recovering = False
        self.latency = Latency()
        self.typing = TypingIndicator(self)
        self.external_results = asyncio.Queue()
        self.backend = self.make_backend(self.backend_name)
        self.backend_started = False
        self.wake = asyncio.Event()
        self.steer_wake = asyncio.Event()
        self.send_wake = asyncio.Event()
        self.loaded = False
        self.running = False
        self.steering = False
        self.stopping = False
        self.session_lock = asyncio.Lock()
        self.model_choices = []
        self.session_catalog = None
        self.restarting = False
        self.shutdown_requested = lambda: None
        self.outbox_drained = asyncio.Event()
        if not self.state["outbox"]:
            self.outbox_drained.set()
        self.output = OutputDelivery(self)
        self.inputs = InputDelivery(self)

    @property
    def slicing(self):
        return self.settings.hot.slicing

    @property
    def notifications(self):
        return self.settings.hot.notifications

    @property
    def source_hint(self):
        return self.settings.hot.source_hint

    def reload_config(self):
        changed = self.settings.reload(self.state)
        self.backend.compaction_notice = self.notifications.compaction
        if changed:
            self.save()
        return self.config

    def prompt(self, name, values=None):
        self.reload_config()
        fields = self.prompt_fields() if name in self.settings.hot.prompts else {}
        return prompt_text(name, {**fields, **(values or {})},
                           overrides=self.settings.hot.prompts)

    def save(self):
        self.state["backendName"] = self.backend_name
        self.state.setdefault("backendSessions", {})[self.backend_name] = snapshot(self.state)
        self.store.save("bridge", self.state)

    def make_backend(self, name):
        backend = self.backend_factory(self.backend_configs[name],
            str(Path(self.config.get("cwd", data_root() / "workspace")).expanduser().resolve()),
            lambda text: self.reply(text) if getattr(self, "backend", None) is backend else None)
        backend.on_activity = self.backend_activity
        backend.on_connection = lambda connected, error=None: self.backend_connection(
            backend, connected, error)
        backend.source_name = name
        backend.refresh_config = self.reload_config
        backend.prompt = self.prompt
        backend.input_hint = self.current_input_hint
        backend.compaction_notice = self.notifications.compaction
        backend.on_limits = lambda payload: self.check_quota(backend, payload)
        backend.on_external_result = self.external_results.put_nowait
        backend.on_message = lambda message: self.output.deliver_message(message) \
            if getattr(self, "backend", None) is backend else None
        return backend

    def current_input_hint(self):
        self.reload_config()
        return "\n\n" + self.source_hint if self.source_hint else ""

    def mark_wechat_valid(self):
        # Once rebind is required, only the stopped-service rebind flow may
        # clear it. A request already in flight must not silently resume polling.
        if wechat_auth.state(self.state) == wechat_auth.AUTH_REQUIRED:
            return
        changed = wechat_auth.mark_valid(self.state, self.account)
        if changed:
            self.save()

    async def require_wechat_rebind(self):
        wechat_auth.require_rebind(self.state, pending=len(self.state.get("outbox", [])))
        self.wechat_paused = True
        details = self.state["wechatAuth"]
        command = wechat_auth.rebind_command()
        notify = (not details.get("notificationAttempted", False)
                  or details.get("notificationCommand") != command)
        if notify:
            details["notificationAttempted"] = True
            details["notificationCommand"] = command
        self.save()
        log_event("wechat.auth_required", level="warning", component="wechat",
                  queued=len(self.state.get("outbox", [])))
        if notify:
            try:
                await service_adapter.notify(self.prompt("WECHAT_AUTH_TITLE"),
                                             self.prompt("WECHAT_REBIND_HINT", {"command": command}))
            except Exception:
                print("微信绑定已失效；重新绑定\n" + wechat_auth.rebind_command(), file=sys.stderr)

    def check_quota(self, backend, payload):
        if backend is not self.backend or not self.state.get("context"):
            return
        self.reload_config()
        records = self.state.setdefault("quotaWarnings", {})
        scope = (self.backend_name, backend.info.get("provider"))
        before = json.dumps(records, sort_keys=True)
        messages = quota_warnings(records, payload, scope, self.notifications.quota_thresholds,
                                  text=self.prompt)
        for message in messages:
            self.reply(self.prompt("QUOTA_WARNING", {"backend": backend_label(self.backend_name),
                                                     "content": message}), persist=False)
        if messages or before != json.dumps(records, sort_keys=True):
            self.save()

    def backend_activity(self):
        # Notifications never start work or change the selected session.
        if hasattr(self, "wake"):
            self.wake.set()
            self.steer_wake.set()

    def backend_connection(self, backend, connected, error=None):
        if getattr(self, "backend", None) is not backend:
            return
        fields = {"component": "backend", "backend": self.backend_name,
                  "connected": connected}
        if error is not None:
            fields.update(error_fields(error))
        log_event("backend.connected" if connected else "backend.disconnected",
                  **fields)

    def active_turn(self):
        active = self.backend.active
        return active if active and not active["done"].done() else None

    def acknowledge(self, job):
        template = notice(self)
        if template:
            self.reply(render_ack(template, self.prompt_fields(job.get("ingress_ms"))),
                       request_id=job.get("requestId"))

    def prompt_fields(self, ingress_ms=None, *, finishing=False):
        if self.backend.blocked:
            status = "等待处理"
        elif (self.running and not finishing) or self.steering or self.active_turn():
            status = "运行中"
        else:
            status = "空闲"
        details = self.backend.info
        rate_limits = self.backend.account_limits
        if not self.backend_started or not self.backend.connected:
            status = "后端连接未就绪"
        values = status_fields(
            status=status,
            backend=backend_label(self.backend_name),
            queued=len(self.state["inbox"]),
            pending=len(self.backend.pending),
            failed=self.state["consecutiveSendFailures"],
            name=details.get("name"),
            model=details.get("model"),
            provider=details.get("provider"),
            effort=details.get("effort"),
            cwd=details.get("cwd") or self.backend.cwd,
            approval=details.get("approval"),
            sandbox=details.get("sandbox"),
            reviewer=details.get("reviewer"),
            profile=details.get("profile"),
            collaboration=details.get("collaboration"),
            usage=details.get("usage"),
            rate_limits=rate_limits,
            connection="本地后台" if self.backend.shared else "独立进程",
        )
        values.update(self.latency.values(ingress_ms))
        sample = self.latency.provider_sample
        values["provider_latency_ms"] = (sample[1].removesuffix("ms")
                              if sample and sample[0] == self.backend.connection_id
                              and sample[1].endswith("ms") else "-")
        return values


    async def ensure_backend(self):
        async with self.connection_lock:
            if self.backend_started and self.backend.connected:
                return
            self.backend_started = False
            self.loaded = False
            await self.backend.close()
            try:
                await self.backend.start()
            except BaseException:
                await self.backend.close()
                raise
            self.backend_started = True
            self.backend_connection(self.backend, True)
            self.input_paused = False
            self.wake.set()

    def text_entries(self, text, *, group, context, semantic=False, source=None, role="body",
                     request_id=None):
        return self.output.text_entries(text, group=group, context=context, semantic=semantic,
                                        source=source, role=role, request_id=request_id)

    def enqueue(self, entries, *, persist=True, request_id=None, request_ids=()):
        return self.output.enqueue(entries, persist=persist, request_id=request_id,
                                   request_ids=request_ids)

    def reply(self, text, *, persist=True, request_id=None):
        return self.output.reply(text, persist=persist, request_id=request_id)

    def asset_entry(self, asset, *, group=None, context=None, source=None, request_id=None):
        return self.output.asset_entry(asset, group=group, context=context, source=source,
                                       request_id=request_id)

    async def queue_file(self, path, kind="auto", *, origin=None):
        return await self.output.queue_file(path, kind, origin=origin)

    def media_origin(self):
        return {"cwd": self.backend.info.get("cwd") or self.backend.cwd,
                "context": self.state["context"], "source": self.backend.source(self.state["thread"])}

    def current_source(self, source):
        return source == self.backend.source(self.state["thread"])

    async def deliver_answer(self, answer, *, finish_running=True, cwd=None,
                             notify_completion=False, source=None, require_current=False,
                             request_id=None):
        return await self.output.deliver_answer(answer, finish_running=finish_running, cwd=cwd,
                                                notify_completion=notify_completion,
                                                source=source, require_current=require_current,
                                                request_id=request_id)

    def dispatch_input(self, job, phase, turn_id, backend):
        return self.inputs.dispatch_input(job, phase, turn_id, backend)

    def record_input(self, job, phase):
        return self.inputs.record_input(job, phase)

    def recover_inputs(self):
        return self.inputs.recover_inputs()

    def reject_input(self, request_id, kind, status):
        log_event("input.rejected", component="input", request_id=request_id,
                  kind=kind, status=status)

    async def receive(self, batch):
        self.reload_config()
        controls = []
        for msg in batch.get("msgs", []):
            request_id = new_request_id()
            if msg.get("from_user_id") != self.account["user"] or msg.get("group_id"):
                continue
            # Use item IDs as the protocol fallback; hash only when every ID is absent.
            identity = message_key(msg) or hashlib.sha256(
                json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            # Describe transport shape only, including messages skipped before routing.
            # No body, IDs, context tokens or media references enter diagnostics.
            kinds = {item.get("type") for item in msg.get("item_list", [])
                     if isinstance(item, dict) and isinstance(item.get("type"), int)}
            skip = ("unsupported_type" if msg.get("message_type") != 1 else
                    "missing_context" if not msg.get("context_token") else
                    "duplicate" if identity in self.state["seen"] else "eligible")
            log_event("input.observed", component="input", request_id=request_id,
                      status=skip, message_type=msg.get("message_type"),
                      message_state=msg.get("message_state"),
                      kind="voice" if 3 in kinds else "text" if 1 in kinds else "other",
                      chars=len(message_text(msg)), count=len(msg.get("item_list", [])))
            if msg.get("message_type") != 1:
                continue
            if not msg.get("context_token"):
                # Cannot safely reply without a context token; don't execute.
                continue
            wechat_auth.accept_context(self.state, msg["context_token"])
            if identity in self.state["seen"]:
                continue
            self.state["seen"].append(identity)
            body_text = message_text(msg)
            quote = quote_context(msg, self.state["quoteCache"])
            text = quoted_prompt(body_text, quote)
            attachments = [item for item in msg.get("item_list", []) if item.get("type") in (2, 4, 5)]
            remember_quote(self.state, identity, message_summary(msg))
            command, argument = parse_control(body_text)
            spec = REGISTRY.get(command)
            queued = bool(spec and spec.route == "input")
            received = self.latency.received_any(message_timestamps(msg))
            if queued:
                text = quoted_prompt(argument, quote)
            kind = ("queued" if queued else
                    "command" if body_text.startswith("/") and not attachments
                    else "input" if text or attachments else "unsupported")
            log_event("input.received", component="input", request_id=request_id,
                      kind=kind, chars=len(text), count=len(attachments))
            if queued and not text and not attachments:
                self.reject_input(request_id, kind, "missing_argument")
                self.reply(self.prompt("COMMAND_USAGE", {"usage": REGISTRY['/queue'].usage}),
                           request_id=request_id)
            elif len(attachments) > MAX_ATTACHMENTS:
                self.reject_input(request_id, kind, "too_many_attachments")
                self.reply(self.prompt("TOO_MANY_ATTACHMENTS", {"count": MAX_ATTACHMENTS}),
                           request_id=request_id)
            elif body_text.startswith("/") and not attachments and not queued:
                received["request_id"] = request_id
                if spec and spec.lane == "media":
                    received["media"] = self.media_origin()
                controls.append((body_text, received))
            elif (text or attachments) and len(text) <= 20000:
                if len(self.state["inbox"]) >= 20:
                    self.reject_input(request_id, kind, "queue_full")
                    self.reply(self.prompt("QUEUE_FULL", {"count": 20}), request_id=request_id)
                else:
                    self.state["inbox"].append({"id": identity, "text": text,
                                               "attachments": attachments, "mode": "queue" if queued else "steer",
                                               "clientId": str(uuid.uuid4()), "phase": InputPhase.PREPARING,
                                               "ingress_ms": received.get("ingress_ms"),
                                               "requestId": request_id})
                    if queued:
                        count = len(self.state["inbox"])
                        self.reply(self.prompt("QUEUE_CONFIRMED", {"count": count,
                                                         "unit": "task" if count == 1 else "tasks"}),
                                   persist=False, request_id=request_id)
            else:
                status = "too_long" if len(text) > 20000 else "unsupported"
                self.reject_input(request_id, kind, status)
                self.reply(self.prompt("UNSUPPORTED_MESSAGE", {"count": 20000}),
                           request_id=request_id)
        self.state["seen"] = self.state["seen"][-2000:]
        if batch.get("get_updates_buf"):
            self.state["cursor"] = batch["get_updates_buf"]
        # Persist cursor and incoming work together, before starting any agent work.
        self.save()
        self.send_wake.set()
        for command, received in controls:
            name, argument = parse_control(command)
            queue = self.controls[command_lane(name, argument)]
            try:
                queue.put_nowait((command, received))
            except asyncio.QueueFull:
                request_id = received.get("request_id")
                log_event("control.rejected", component="input", kind="command",
                          status="queue_full", request_id=request_id)
                self.reply(self.prompt("CONTROL_QUEUE_FULL"), request_id=request_id)
        self.wake.set()
        self.steer_wake.set()

    async def control(self, text, *, received=None):
        await dispatch(self, text, received=received)


    async def enable_background_service(self):
        """First opt-in can hand an idle foreground bridge to its new service."""
        import autostart
        default = data_root()
        if (self.store.root.resolve() != (default / ".state").resolve() or
                (self.settings.path is not None and
                 self.settings.path.resolve() != (default / "config.json").resolve())):
            raise UserFacingError(self.prompt("AUTOSTART_CUSTOM_PATH"))
        if (self.restarting or self.running or self.steering or self.active_turn()
                or (self.session_lock.locked() and not self.backend_recovering)
                or self.backend.pending):
            raise UserFacingError(self.prompt("AUTOSTART_BUSY"))
        self.restarting = True
        try:
            await autostart.set_enabled(True, start_if_missing=False)
            self.reply(self.prompt("LABEL_AUTOSTART") + ": " + self.prompt("VALUE_ON"))
            await asyncio.wait_for(self.outbox_drained.wait(), 20)
            await service_control.systemctl("--no-block", "start", service_control.UNIT)
            self.shutdown_requested()
            await asyncio.Event().wait()
        finally:
            if not asyncio.current_task().cancelling():
                self.restarting = False
                self.wake.set()
                self.steer_wake.set()


    async def restart_service(self):
        current = self.state.get("currentInput")
        submitted = current and current.get("phase") != InputPhase.PREPARING
        if (self.restarting or self.steering or self.active_turn() or submitted
                or (self.running and not current)
                or (self.session_lock.locked() and not self.backend_recovering)):
            raise UserFacingError(self.prompt("BUSY_RESTART"))
        # No await before closing the dispatch barrier. Preparing work is recoverable;
        # queued work must not make a disconnected bridge impossible to restart.
        self.restarting = True
        try:
            try:
                await service_control.require_service_owner()
            except ValueError:
                raise UserFacingError(self.prompt("RESTART_NOT_SERVICE")) from None
            self.reply(self.prompt("RESTART_STARTED"))
            try:
                await asyncio.wait_for(self.outbox_drained.wait(), 20)
            except asyncio.TimeoutError:
                raise UserFacingError(self.prompt("RESTART_CONFIRM_UNSENT")) from None
            self.state["restartRequested"] = True
            self.save()
            await service_control.restart()
            # Native service shutdown cancels preflight; saved preparing jobs recover.
            await asyncio.Event().wait()
        finally:
            if not asyncio.current_task().cancelling():
                self.state.pop("restartRequested", None)
                self.restarting = False
                self.save()
                self.wake.set()
                self.steer_wake.set()

    async def control_worker(self, lane):
        queue = self.controls[lane]
        while True:
            text, received = await queue.get()
            try:
                if lane == "urgent":
                    await asyncio.wait_for(self.control(text, received=received), 5)
                else:
                    await self.control(text, received=received)
            except asyncio.TimeoutError:
                self.reply(self.prompt("CONTROL_TIMEOUT"))
                log_event("control.timeout", level="warning", component="input",
                          kind=lane, request_id=received.get("request_id"))
            except Exception as error:
                self.reply(self.prompt("CONTROL_FAILED"))
                log_event("control.failed", level="warning", component="input",
                          kind=lane, request_id=received.get("request_id"),
                          **error_fields(error))
                print(f"控制失败：{type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                queue.task_done()
                self.wake.set()

    async def maintain_backend(self):
        reported = None
        while True:
            delay = 10
            if (not self.restarting and not self.running and not self.steering
                    and not self.session_lock.locked()):
                try:
                    async with self.session_lock:
                        self.backend_recovering = True
                        try:
                            await self.ensure_backend()
                            if self.state["thread"]:
                                await ensure_session(self)
                        finally:
                            self.backend_recovering = False
                    self.input_paused = False
                    self.wake.set()
                    reported = None
                except Exception as error:
                    self.backend_started = self.backend.connected
                    self.input_paused = True
                    reason = backend_reason(error)
                    if reason != reported:
                        self.reply(self.prompt("BACKEND_CONNECT_FAILED", {"reason": reason}))
                        reported = reason
                    if isinstance(error, BackendStartupError):
                        delay = error.retry_after
                    log_event("backend.recover_failed", level="warning", component="backend",
                              retry_in_ms=int(delay * 1000), **error_fields(error))
                    print(f"后端恢复失败：{type(error).__name__}", file=sys.stderr, flush=True)
            # Account reads are independent of the active model turn.
            if not self.restarting and self.backend_started and self.backend.connected:
                await self.backend.refresh_usage()
                await self.backend.rate_limits()
            await asyncio.sleep(delay)

    async def worker(self):
        return await self.inputs.worker()

    async def steering_worker(self):
        return await self.inputs.steering_worker()

    async def external_result_worker(self):
        return await self.output.external_result_worker()

    def fail_output(self, entry):
        return self.output.fail_output(entry)

    async def sender(self):
        return await self.output.sender()

    async def poller(self):
        delay = 2
        while True:
            if self.wechat_paused:
                self.auth_wake.clear()
                await self.auth_wake.wait()
                continue
            try:
                batch = await self.channel.poll(self.state["cursor"])
                if self.wechat_paused:
                    continue
                self.mark_wechat_valid()
                await self.receive(batch)
                delay = 2
            except WeChatAuthExpired:
                await self.require_wechat_rebind()
            except Exception as error:
                log_event("wechat.poll_failed", level="warning", component="wechat",
                          retry_in_ms=int(delay * 1000), **error_fields(error))
                print(f"接收失败：{error}", file=sys.stderr, flush=True)
                await asyncio.sleep(delay)
                # Long reconnect backoff leaves newly arrived commands unread.
                delay = min(delay * 2, 5)

    async def run(self):
        tasks = []
        try:
            if self.state.pop("restartRequested", False):
                self.reply(self.prompt("RESTART_READY"))
            self.recover_inputs()
            if self.wechat_paused:
                await self.require_wechat_rebind()
            tasks = [asyncio.create_task(fn()) for fn in
                     (self.poller, self.worker, self.steering_worker,
                      self.external_result_worker, self.sender, self.maintain_backend)]
            tasks.extend(asyncio.create_task(self.control_worker(lane)) for lane in self.controls)
            log_event("bridge.start", component="bridge", backend=self.backend_name,
                      connected=self.backend.connected)
            print("微信桥已启动；Ctrl+C 停止。", flush=True)
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.backend.close()
            log_event("bridge.stop", component="bridge")


async def startup_store(args):
    """A newly started service waits briefly for its foreground predecessor."""
    deadline = asyncio.get_running_loop().time() + 30
    managed = False
    while True:
        try:
            return Store(args.state_dir, lock=args.action != "status")
        except RuntimeError:
            if IS_WINDOWS or args.action != "start":
                raise
            if not managed:
                try:
                    await service_control.require_service_owner()
                except ValueError:
                    raise RuntimeError("另一个 codex-wechat 进程正在使用此状态目录") from None
                managed = True
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("前台进程尚未退出，后台服务等待状态锁超时。") from None
            await asyncio.sleep(0.2)


async def startup_account(store):
    """Only an unbound foreground launch may ask the user to scan a QR code."""
    account = store.load("account")
    if account:
        state = store.load("bridge", {})
        if account.get("bindingPending") or isinstance(state, dict) and state.get("bindingPending"):
            raise ConfigurationError("重新绑定尚未完成；运行 " + wechat_auth.rebind_command() + " 继续。")
        if not all(account.get(key) for key in ("user", "token")):
            raise ConfigurationError("微信凭据不完整；运行 " + wechat_auth.rebind_command() + " 重新绑定。")
        return account
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ConfigurationError("尚未绑定微信，请在交互终端运行 codex-wechat start 完成扫码。")
    print("尚未绑定微信，请在当前终端扫码；绑定后会继续启动。", flush=True)
    await login(store, starting=True)
    account = store.load("account")
    if not account:
        raise ConfigurationError("微信绑定未完成，未启动桥接。")
    return account


def status_payload(store):
    state = store.load("bridge", {})
    auth = wechat_auth.state(state)
    payload = {
        "bound": bool(store.load("account")),
        "auth": auth,
        "available": (True if auth == wechat_auth.AUTH_VALID else
                      False if auth == wechat_auth.AUTH_REQUIRED else None),
        "rebind_required": auth == wechat_auth.AUTH_REQUIRED,
        "rebind_command": wechat_auth.rebind_command(),
        "thread": state.get("thread"),
        "backend": state.get("backendName", "codex"),
        "unfinished_at_last_save": bool(
            state.get("currentInput") or state.get("running", False)
        ),
        "queued": len(state.get("inbox", [])),
        "unsent": len(state.get("outbox", [])),
    }
    auth_details = state.get("wechatAuth")
    if isinstance(auth_details, dict):
        if "detectedAt" in auth_details:
            payload["auth_detected_at"] = auth_details["detectedAt"]
        if "unsent" in auth_details:
            payload["auth_unsent_at_detection"] = auth_details["unsent"]
    return payload


def stop_notification_text(config_path):
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError("配置必须是对象")
        overrides = HotSettings.from_config(config).prompts
    except (AttributeError, OSError, ValueError):
        overrides = {}
    return prompt_text("SERVICE_STOPPING", overrides=overrides)


async def send_stop_notification(state_dir, config_path):
    try:
        store = Store(state_dir, lock=False)
        account = store.load("account")
        state = store.load("bridge", {})
    except (OSError, ValueError):
        return
    context = state.get("context") if isinstance(state, dict) else None
    if (not isinstance(account, dict) or not account.get("user")
            or not account.get("token") or not context):
        return
    text = stop_notification_text(config_path)
    if not text:
        return
    entry = {"id": str(uuid.uuid4()), "user": account["user"],
             "context": context, "text": text}
    try:
        await WeChat(account).send(entry)
    except Exception as error:
        print(f"关闭通知未发送：{type(error).__name__}", file=sys.stderr, flush=True)


async def stop_background_service(state_dir, config_path):
    """Stop only the installed background service without taking the bridge lock."""
    current = await service_adapter.status()
    if not current.installed:
        print("未安装后台服务。")
        return
    if not current.running:
        print("后台服务未运行。")
        return
    await send_stop_notification(state_dir, config_path)
    await service_adapter.stop_and_wait()
    print("后台服务已停止。")


def _log_files(include_service=False):
    try:
        primary = log_path()
    except (OSError, RuntimeError):
        return []
    candidates = [primary]
    candidates.extend(primary.with_name(f"{primary.name}.{number}")
                      for number in range(1, BACKUP_COUNT + 1))
    if include_service:
        candidates.extend((primary.parent / "service.log",
                           primary.parent / "service.log.1"))
    return [path for path in candidates if path.is_file()]


def _tail_stream(stream, limit=200):
    stream.seek(0, os.SEEK_END)
    end = stream.tell()
    boundary = end
    data = b""
    scanned = 0
    while end > 0 and scanned < 1024 * 1024:
        size = min(8192, end, 1024 * 1024 - scanned)
        end -= size
        scanned += size
        stream.seek(end)
        data = stream.read(size) + data
        if data.count(b"\n") > limit:
            break
    # Keep the cursor at the exact boundary whose tail was displayed, not a later stat().
    stream.seek(boundary)
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def _tail_lines(path, limit=200):
    try:
        with path.open("rb") as stream:
            return _tail_stream(stream, limit)
    except (OSError, ValueError):
        return []


def _print_log_header(path):
    suffix = "（原始控制台输出，可能包含路径或错误文本）" \
        if path.name.startswith("service.log") else ""
    print(f"==> {path} <=={suffix}")


def _read_log_updates(files, positions, *, initial=False):
    # Rotated names are aliases for old file identities, not new streams to replay.
    for path in reversed(files):
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                position = positions.get(identity)
                if initial:
                    _print_log_header(path)
                    print("\n".join(_tail_stream(stream)), flush=True)
                    position = stream.tell()
                else:
                    if position is None:
                        _print_log_header(path)
                        position = 0
                    if stat.st_size < position:
                        position = 0
                    stream.seek(position)
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    while chunk := stream.read(65536):
                        print(decoder.decode(chunk), end="", flush=True)
                    # Retry an incomplete UTF-8 suffix on the next poll.
                    position = stream.tell() - len(decoder.getstate()[0])
                positions.pop(identity, None)
                positions[identity] = position
                while len(positions) > 2 * (BACKUP_COUNT + 3):
                    positions.pop(next(iter(positions)))
        except (OSError, ValueError):
            continue


def show_logs(follow=False, include_service=False):
    files = _log_files(include_service)
    if not files and not follow:
        label = "诊断日志" if include_service else "结构化诊断日志"
        print(f"暂无{label}；运行 start 或 service-start 后生成。")
        if not include_service:
            print("需要 supervisor 控制台输出时使用 --service / -Service。")
        return
    positions = {}
    _read_log_updates(files, positions, initial=True)
    if not follow:
        return
    if not files:
        hint = "" if include_service else "；supervisor 输出请加 --service / -Service"
        print(f"等待诊断日志{hint}；按 Ctrl+C 停止。", flush=True)
    while True:
        time.sleep(0.5)
        _read_log_updates(_log_files(include_service), positions)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "start", "stop", "status", "rebind", "logs"))
    parser.add_argument("--state-dir", type=Path, default=data_root() / ".state")
    parser.add_argument("--config", type=Path, default=data_root() / "config.json")
    parser.add_argument("--stop-active", action="store_true")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--service", action="store_true",
                        help="同时查看未脱敏的 supervisor/service.log")
    args = parser.parse_args()
    if args.action != "rebind" and args.stop_active:
        parser.error("--stop-active 只能与 rebind 一起使用")
    if args.action != "logs" and args.follow:
        parser.error("--follow 只能与 logs 一起使用")
    if args.action != "logs" and args.service:
        parser.error("--service 只能与 logs 一起使用")
    os.umask(0o077)
    if args.action == "logs":
        show_logs(args.follow, args.service)
        return
    if args.action == "stop":
        await stop_background_service(args.state_dir, args.config)
        return
    if args.action == "rebind":
        await run_rebind(
            args.state_dir,
            args.config,
            stop_active=args.stop_active,
        )
        return
    store = await startup_store(args)
    if args.action == "login":
        result = await login(store)
        state = store.load("bridge", {})
        if wechat_auth.mark_valid(state, result.account):
            store.save("bridge", state)
    elif args.action == "status":
        print(json.dumps(status_payload(store), ensure_ascii=False, indent=2))
    else:
        account = await startup_account(store)
        try:
            config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config.exists() else {}
            if not isinstance(config, dict):
                raise ValueError("配置必须是 JSON 对象。")
            bridge = Bridge(store, account, config, config_path=args.config)
        except ValueError as error:
            raise ConfigurationError(str(error)) from None
        cwd = Path(config.get("cwd", data_root() / "workspace")).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        bridge.shutdown_requested = main_task.cancel
        for signum in (signal.SIGINT, signal.SIGTERM):
            if IS_WINDOWS:
                signal.signal(signum, lambda *_: loop.call_soon_threadsafe(main_task.cancel))
            else:
                loop.add_signal_handler(signum, main_task.cancel)
        await bridge.run()


if __name__ == "__main__":
    if IS_WINDOWS:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except ConfigurationError as error:
        log_event("startup.failed", level="error", component="startup",
                  status="configuration", **error_fields(error))
        print(str(error), file=sys.stderr)
        sys.exit(78)  # Persistent setup/configuration errors need user correction.
    except Exception as error:
        log_event("startup.failed", level="error", component="startup",
                  status="exception", **error_fields(error))
        print(str(error), file=sys.stderr)
        sys.exit(1)
    sys.exit(service_control.exit_code())
