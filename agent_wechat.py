#!/usr/bin/env python3
"""Local WeChat ↔ Codex bridge. Media transport uses cryptography."""
import argparse
from dataclasses import asdict
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from backend import CodexBackend, CodexRpcError, DeferTurn
from storage import Store
from wechat import WeChat, login
from commands import ensure_session, handle, notice
from command_specs import HELP, REGISTRY, command_lane
from configuration import Slicing, Notifications
from quota import quota_warnings
from records import InputPhase, TurnResult
from slicing import split_text
from profiles import backend_label, definitions, snapshot
import service_control
from media import Media, MediaError, MAX_ATTACHMENTS, split_answer
from latency import Latency, provider_latency
from errors import UserFacingError, attachment_reason, backend_reason
from prompts import (
    APPROVAL_ACCEPTED,
    APPROVAL_DECLINED,
    APPROVAL_EXPIRED,
    APPROVAL_FAILED,
    ATTACHMENT_INCOMING_FAILED,
    ATTACHMENT_QUEUED,
    ATTACHMENT_SEND_FAILED,
    AUTO_ATTACHMENT_FAILED,
    AUTO_ATTACHMENT_LIMIT,
    AUTO_ATTACHMENT_PATH_HINT,
    BACKEND_CONNECT_FAILED,
    BUSY_RESTART,
    COMMAND_FAILURE,
    COMMAND_FAILURE_LABELS,
    COMPLETION_NOTICE,
    MEDIA_USAGE,
    PING_REPLY,
    PING_USAGE,
    QUEUED_STEER,
    QUEUE_FULL,
    RESTART_CONFIRM_UNSENT,
    RESTART_FAILED,
    RESTART_NOT_SERVICE,
    RESTART_READY,
    RESTART_STARTED,
    RESTART_USAGE,
    STOP_DEFERRED,
    STOP_IDLE,
    STOP_REQUESTED,
    STOP_UNCONFIRMED,
    TASK_FAILED,
    TOO_MANY_ATTACHMENTS,
    UNCERTAIN_STEER,
    UNKNOWN_COMMAND,
    UNSUPPORTED_MESSAGE,
    fill,
    render_ack,
    status_fields,
    status_from_fields,
)

ROOT = Path(__file__).resolve().parent
SEND_ATTEMPTS = 3


def parse_control(text):
    """Split a WeChat command after trimming surrounding whitespace."""
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


class Bridge:
    def __init__(self, store, account, config, backend_factory=CodexBackend, channel=None):
        self.store, self.account, self.config = store, account, config
        self.state = store.load("bridge", {"cursor": "", "thread": None,
            "inbox": [], "outbox": [], "seen": [], "running": False})
        self.channel = channel or WeChat(account)
        self.media = Media(store, self.channel, config.get("media"))
        self.backend_factory = backend_factory
        self.backend_configs = definitions(config)
        self.backend_name = self.state.get("backendName", "codex")
        if self.backend_name not in self.backend_configs:
            raise ValueError("保存的后端不存在于配置中：" + self.backend_name)
        self.slicing = Slicing.from_config(config.get("slicing"))
        self.notifications = Notifications.from_config(config)
        self.controls = {lane: asyncio.Queue(maxsize=20) for lane in ("local", "urgent", "backend", "probe")}
        self.connection_lock = asyncio.Lock()
        self.input_paused = False
        self.latency = Latency()
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
        self.outbox_drained = asyncio.Event()
        if not self.state["outbox"]:
            self.outbox_drained.set()

    def save(self):
        self.state["backendName"] = self.backend_name
        self.state.setdefault("backendSessions", {})[self.backend_name] = snapshot(self.state)
        self.store.save("bridge", self.state)

    def make_backend(self, name):
        backend = self.backend_factory(self.backend_configs[name],
            str(Path(self.config.get("cwd", ROOT / "workspace")).expanduser().resolve()),
            lambda text: self.reply(text) if getattr(self, "backend", None) is backend else None)
        backend.on_activity = self.backend_activity
        backend.source_name = name
        backend.compaction_notice = self.notifications.compaction
        backend.on_limits = lambda payload: self.check_quota(backend, payload)
        backend.on_external_result = self.external_results.put_nowait
        return backend

    def check_quota(self, backend, payload):
        if backend is not self.backend or not self.state.get("context"):
            return
        records = self.state.setdefault("quotaWarnings", {})
        scope = (self.backend_name, backend.info.get("provider"))
        before = json.dumps(records, sort_keys=True)
        messages = quota_warnings(records, payload, scope, self.notifications.quota_thresholds)
        for message in messages:
            self.reply(f"限额提醒: {backend_label(self.backend_name)}\n{message}", persist=False)
        if messages or before != json.dumps(records, sort_keys=True):
            self.save()

    def backend_activity(self):
        # Notifications never start work or change the selected session.
        if hasattr(self, "wake"):
            self.wake.set()
            self.steer_wake.set()

    def active_turn(self):
        active = self.backend.active
        return active if active and not active["done"].done() else None

    def acknowledge(self, job):
        template = notice(self)
        if template:
            self.reply(render_ack(template, self.prompt_fields(job.get("ingress_ms"))))

    def prompt_fields(self, ingress_ms=None):
        if self.backend.blocked:
            status = "等待处理"
        elif self.running or self.steering or self.active_turn():
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
            failed=len(self.state.get("failedOutbox", [])),
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
        values["供应商延迟"] = (sample[1].removesuffix("ms")
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
            self.input_paused = False
            self.wake.set()

    def text_entries(self, text, *, group, context, semantic=False, source=None, role="body"):
        return [{"id": str(uuid.uuid4()), "group": group, "role": role,
                 "user": self.account["user"], "context": context, "text": chunk,
                 **({"source": asdict(source)} if source else {})}
                for chunk in split_text(text, self.slicing, semantic=semantic)]

    def enqueue(self, entries, *, persist=True):
        for index, entry in enumerate(entries, 1):
            entry.update(part=index, parts=len(entries))
        self.state["outbox"].extend(entries)
        if entries:
            self.outbox_drained.clear()
        if persist:
            self.save()
        self.send_wake.set()

    def reply(self, text, *, persist=True):
        context = self.state.get("context")
        if context:
            self.enqueue(self.text_entries(text, group=str(uuid.uuid4()), context=context),
                         persist=persist)

    def asset_entry(self, asset, *, group=None, context=None, source=None):
        return {"id": str(uuid.uuid4()), "group": group or str(uuid.uuid4()), "role": "asset",
                "user": self.account["user"], "context": context or self.state["context"],
                "asset": asset, **({"source": asdict(source)} if source else {})}

    async def queue_file(self, path, kind="auto"):
        cwd = self.backend.info.get("cwd") or self.backend.cwd
        context, source = self.state["context"], self.backend.source(self.state["thread"])
        asset = await asyncio.to_thread(self.media.snapshot, path, cwd, kind)
        self.enqueue([self.asset_entry(asset, context=context, source=source)])

    def current_source(self, source):
        return source == self.backend.source(self.state["thread"])

    async def deliver_answer(self, answer, *, finish_running=True, cwd=None,
                             notify_completion=False, source=None, require_current=False):
        if isinstance(answer, TurnResult):
            source, cwd, notify_completion = answer.source, answer.cwd, answer.completed
            answer = answer.text
        text, specs = split_answer(answer)
        context = self.state.get("context")
        if not context:
            return
        group = str(uuid.uuid4())
        assets, errors = [], []
        if len(specs) > MAX_ATTACHMENTS:
            errors.append(fill(AUTO_ATTACHMENT_LIMIT, {"数量": MAX_ATTACHMENTS}))
        for spec in specs[:MAX_ATTACHMENTS]:
            try:
                asset = await asyncio.to_thread(self.media.snapshot, spec["path"],
                    cwd or self.backend.info.get("cwd") or self.backend.cwd,
                    spec.get("kind", "auto"), automatic=True)
                assets.append(self.asset_entry(asset, group=group, context=context, source=source))
            except (MediaError, ValueError, OSError) as error:
                reason = attachment_reason(error)
                errors.append(fill(AUTO_ATTACHMENT_FAILED, {
                    "原因": reason,
                    "建议": (AUTO_ATTACHMENT_PATH_HINT
                             if reason == "只能自动发送当前会话目录内的文件" else ""),
                }))
        # Snapshot work may await disk I/O across a session/backend switch.
        if require_current and not self.current_source(source):
            return
        entries = self.text_entries(text, group=group, context=context, semantic=True, source=source)
        if errors:
            entries.extend(self.text_entries("\n".join(errors), group=group, context=context, source=source))
        entries.extend(assets)
        if notify_completion and not errors and COMPLETION_NOTICE:
            completion = render_ack(COMPLETION_NOTICE, self.prompt_fields())
            entries.extend(self.text_entries(completion, group=group, context=context,
                                             source=source, role="completion"))
        if finish_running:
            self.state.pop("currentInput", None)
        # The input is retired in the same snapshot as its frozen output group.
        self.enqueue(entries)

    def dispatch_input(self, job, phase, turn_id, backend):
        if phase == InputPhase.DISPATCHING and (self.stopping or backend is not self.backend):
            return False
        job.update(phase=phase, turn=turn_id, source=asdict(backend.source(self.state["thread"])))
        self.save()
        return True

    def record_input(self, job, phase):
        job["phase"] = phase
        self.state["inputRecords"] = (self.state.get("inputRecords", []) + [dict(job)])[-20:]

    def recover_inputs(self):
        legacy_running = self.state.pop("running", False)
        self.state.pop("phase", None)
        current = self.state.pop("currentInput", None)
        jobs = ([current] if current else []) + self.state["inbox"]
        self.state["inbox"] = []
        uncertain = False
        for job in jobs:
            phase = job.get("phase", InputPhase.PREPARING)
            if job.get("dispatching") or phase in (InputPhase.DISPATCHING, InputPhase.ACCEPTED):
                self.record_input(job, InputPhase.ACCEPTED if phase == InputPhase.ACCEPTED else InputPhase.UNCERTAIN)
                uncertain = True
            else:
                job["phase"] = InputPhase.PREPARING
                job.setdefault("clientId", str(uuid.uuid4()))
                self.state["inbox"].append(job)
        if uncertain or legacy_running:
            self.reply("上次有任务已提交或提交结果未确认，未自动重发。未提交的待办已保留。")
        self.save()

    async def receive(self, batch):
        controls = []
        for msg in batch.get("msgs", []):
            if (msg.get("from_user_id") != self.account["user"] or msg.get("group_id")
                    or msg.get("message_type") != 1):
                continue
            # Use a stable fallback when the backend omits message_id.
            identity = msg.get("message_id") or msg.get("client_id") or hashlib.sha256(
                json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            identity = str(identity)
            if identity in self.state["seen"]:
                continue
            if not msg.get("context_token"):
                # Cannot safely reply without a context token; don't execute.
                continue
            self.state["context"] = msg["context_token"]
            self.state["seen"].append(identity)
            text = "\n".join(item.get("text_item", {}).get("text", "")
                             for item in msg.get("item_list", []) if item.get("type") == 1).strip()
            attachments = [item for item in msg.get("item_list", []) if item.get("type") in (2, 4)]
            command, argument = parse_control(text)
            spec = REGISTRY.get(command)
            queued = bool(spec and spec.route == "input")
            if queued:
                text = argument
            if queued and not text and not attachments:
                self.reply(f"用法: `{REGISTRY['/queue'].usage}`")
            elif len(attachments) > MAX_ATTACHMENTS:
                self.reply(fill(TOO_MANY_ATTACHMENTS, {"数量": MAX_ATTACHMENTS}))
            elif text.startswith("/") and not attachments and not queued:
                controls.append((text, self.latency.received(msg.get("create_time_ms"))))
            elif (text or attachments) and len(text) <= 20000:
                if len(self.state["inbox"]) >= 20:
                    self.reply(fill(QUEUE_FULL, {"数量": 20}))
                else:
                    self.state["inbox"].append({"id": identity, "text": text or "请查看我发送的附件。",
                                               "attachments": attachments, "mode": "queue" if queued else "steer",
                                               "clientId": str(uuid.uuid4()), "phase": InputPhase.PREPARING,
                                               "ingress_ms": self.latency.received(msg.get("create_time_ms")).get("ingress_ms")})
                    if queued:
                        self.reply("已排队", persist=False)
            else:
                self.reply(fill(UNSUPPORTED_MESSAGE, {"数量": 20000}))
        self.state["seen"] = self.state["seen"][-2000:]
        if batch.get("get_updates_buf"):
            self.state["cursor"] = batch["get_updates_buf"]
        # Persist cursor and incoming work together, before starting any agent work.
        self.save()
        for command, received in controls:
            name, argument = parse_control(command)
            queue = self.controls[command_lane(name, argument)]
            try:
                queue.put_nowait((command, received))
            except asyncio.QueueFull:
                self.reply("指令等待过多，请稍后重试。")
        self.wake.set()
        self.steer_wake.set()

    async def control(self, text, *, received=None):
        command, argument = parse_control(text)
        spec = REGISTRY.get(command)
        if spec:
            command = spec.name
        if spec and spec.route == "managed":
            try:
                if spec.needs_backend and not (command == "/cwd" and not argument):
                    await self.ensure_backend()
                await handle(self, command, argument)
            except UserFacingError as error:
                self.reply(str(error))
            except ValueError as error:
                label = COMMAND_FAILURE_LABELS.get(command, "操作失败")
                detail = backend_reason(error)
                self.reply(fill(COMMAND_FAILURE, {"动作": label, "原因": detail}))
                print(f"{command}失败：{error}", file=sys.stderr, flush=True)
            except (RuntimeError, OSError, asyncio.TimeoutError) as error:
                label = COMMAND_FAILURE_LABELS.get(command, "操作失败")
                detail = backend_reason(error)
                self.reply(fill(COMMAND_FAILURE, {"动作": label, "原因": detail}))
                print(f"{command}失败：{error}", file=sys.stderr, flush=True)
        elif command == "/help":
            self.reply(HELP)
        elif command == "/ping":
            if argument:
                self.reply(PING_USAGE)
            else:
                ingress = received.get("ingress_ms") if received else None
                channel = render_ack(PING_REPLY, self.latency.values(ingress))
                backend = self.backend
                connection = backend.connection_id
                provider = await provider_latency(backend)
                if backend is self.backend and connection == backend.connection_id:
                    self.latency.provider_sample = (connection, provider)
                self.reply(channel + "─ Bridge ─" + provider + "─ Provider")
        elif command in ("/send", "/sendfile", "/sendimage"):
            try:
                if not argument:
                    self.reply(MEDIA_USAGE)
                    return
                if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in ("'", '"'):
                    argument = argument[1:-1]
                await self.queue_file(argument, {"/sendfile": "file", "/sendimage": "image"}.get(command, "auto"))
                self.reply(ATTACHMENT_QUEUED)
            except (MediaError, ValueError, OSError) as error:
                self.reply(f"附件未发送: {attachment_reason(error)}\n请检查路径后重试。")
        elif command == "/restart":
            if argument:
                self.reply(RESTART_USAGE)
                return
            try:
                await self.restart_service()
            except UserFacingError as error:
                self.reply("拒绝重启: " + str(error))
            except (RuntimeError, OSError, asyncio.TimeoutError) as error:
                self.reply(fill(RESTART_FAILED, {"原因": backend_reason(error)}))
        elif command == "/status":
            self.reply(status_from_fields(self.prompt_fields()))
        elif command == "/stop":
            self.stopping = True
            cleared = len(self.state["inbox"])
            cleared_note = f"\n已清空待办: {cleared}" if cleared else ""
            for job in self.state["inbox"]:
                if job.get("phase") in (InputPhase.DISPATCHING, InputPhase.ACCEPTED):
                    self.record_input(job, InputPhase.UNCERTAIN if job["phase"] == InputPhase.DISPATCHING
                                      else InputPhase.ACCEPTED)
            self.state["inbox"].clear()
            self.save()
            try:
                stopped = await self.backend.stop()
                if stopped:
                    self.reply(STOP_REQUESTED + cleared_note)
                elif self.running or self.active_turn():
                    self.reply(STOP_DEFERRED + cleared_note)
                else:
                    self.reply(STOP_IDLE + cleared_note)
            except (RuntimeError, OSError, asyncio.TimeoutError):
                self.reply(STOP_UNCONFIRMED + cleared_note)
        elif command in ("/approve", "/deny"):
            try:
                accept = command == "/approve"
                ok = await self.backend.answer_approval(
                    argument, "accept" if accept else "decline")
                self.reply(APPROVAL_ACCEPTED if ok and accept else
                           APPROVAL_DECLINED if ok else APPROVAL_EXPIRED)
            except (RuntimeError, OSError, asyncio.TimeoutError):
                self.reply(APPROVAL_FAILED)
        else:
            self.reply(UNKNOWN_COMMAND)

    async def restart_service(self):
        if self.running or self.steering or self.active_turn() or self.state["inbox"] or self.session_lock.locked():
            raise UserFacingError(BUSY_RESTART)
        async with self.session_lock:
            try:
                await service_control.require_service_owner()
            except ValueError:
                raise UserFacingError(RESTART_NOT_SERVICE) from None
            self.restarting = True
            try:
                self.reply(RESTART_STARTED)
                try:
                    await asyncio.wait_for(self.outbox_drained.wait(), 20)
                except asyncio.TimeoutError:
                    raise UserFacingError(RESTART_CONFIRM_UNSENT) from None
                self.state["restartRequested"] = True
                self.save()
                await service_control.restart()
                # Remain quiescent until systemd terminates us; don't accept more work.
                await asyncio.Event().wait()
            finally:
                if not asyncio.current_task().cancelling():
                    self.state.pop("restartRequested", None)
                    self.restarting = False
                    self.save()

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
                self.reply("控制请求未确认，请查看 `/status`；不会自动重发。")
            except Exception as error:
                self.reply("指令未完成，请查看 `/status` 后重试。")
                print(f"控制失败：{type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                queue.task_done()
                self.wake.set()

    async def maintain_backend(self):
        reported = False
        while True:
            if not self.running and not self.steering and not self.session_lock.locked():
                try:
                    async with self.session_lock:
                        await self.ensure_backend()
                        if self.state["thread"]:
                            await ensure_session(self)
                    self.input_paused = False
                    self.wake.set()
                    reported = False
                except Exception as error:
                    self.backend_started = self.backend.connected
                    self.input_paused = True
                    if not reported:
                        self.reply(fill(BACKEND_CONNECT_FAILED, {"原因": backend_reason(error)}))
                        reported = True
                    print(f"后端恢复失败：{type(error).__name__}", file=sys.stderr, flush=True)
            # Account reads are independent of the active model turn.
            if self.backend_started and self.backend.connected:
                await self.backend.refresh_usage()
                await self.backend.rate_limits()
            await asyncio.sleep(10)

    async def worker(self):
        while True:
            if (self.restarting or self.steering or self.session_lock.locked() or self.input_paused):
                await asyncio.sleep(0.1)
                continue
            if self.active_turn() or self.backend.blocked or not self.state["inbox"]:
                self.wake.clear()
                self.steer_wake.set()
                await self.wake.wait()
                continue
            self.running = True
            self.stopping = False
            job = self.state["inbox"].pop(0)
            job["phase"] = InputPhase.PREPARING
            self.state["currentInput"] = job
            self.save()
            backend = self.backend
            try:
                try:
                    await ensure_session(self)
                except Exception:
                    # Preparation has not submitted anything. Leave the exact job
                    # available for the connection supervisor, rather than dropping it.
                    if not self.stopping:
                        self.state["inbox"].insert(0, job)
                    self.state.pop("currentInput", None)
                    self.input_paused = True
                    self.save()
                    raise
                if self.stopping:
                    continue
                self.acknowledge(job)
                if "preparedAttachments" not in job:
                    job["preparedAttachments"] = await self.media.incoming(job.get("attachments", []))
                    self.save()
                if self.stopping:
                    continue
                self.steer_wake.set()
                answer = await backend.run(self.state["thread"], job["text"],
                    attachments=job["preparedAttachments"], client_id=job["clientId"],
                    allow_steer=job.get("mode", "steer") != "queue",
                    dispatch=lambda phase, turn: self.dispatch_input(job, phase, turn, backend))
                if answer is not None:
                    await self.deliver_answer(answer)
                else:
                    self.state.pop("currentInput", None)
                    self.save()
            except asyncio.CancelledError:
                raise
            except DeferTurn:
                if not self.stopping:
                    job.update(mode="queue", phase=InputPhase.PREPARING)
                    self.state["inbox"].insert(0, job)
                    self.reply(QUEUED_STEER)
                self.state.pop("currentInput", None)
                self.save()
            except Exception as error:
                if self.state.get("currentInput") is job:
                    phase = job["phase"]
                    uncertain = phase == InputPhase.DISPATCHING and not (
                        isinstance(error, CodexRpcError) and error.definite_rejection)
                    self.record_input(job, InputPhase.UNCERTAIN if uncertain else
                                      InputPhase.ACCEPTED if phase == InputPhase.ACCEPTED else InputPhase.FAILED)
                    self.state.pop("currentInput", None)
                    self.reply("消息提交结果未确认，未自动重发。" if uncertain else
                               "任务已提交，但结果接收中断，未重新执行。" if phase == InputPhase.ACCEPTED else
                               fill(ATTACHMENT_INCOMING_FAILED, {"原因": attachment_reason(error)})
                               if isinstance(error, MediaError) else TASK_FAILED)
                else:
                    self.reply("后端尚未就绪，未提交的消息已保留；可用 `/stop` 清空。")
                print(f"Agent backend error: {type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                if self.stopping and self.state.get("currentInput") is job:
                    self.record_input(job, InputPhase.CANCELLED if job["phase"] == InputPhase.PREPARING
                                      else job["phase"])
                    self.state.pop("currentInput", None)
                    self.save()
                self.running = False
                self.wake.set()

    async def steering_worker(self):
        while True:
            await self.steer_wake.wait()
            self.steer_wake.clear()
            while (self.running or self.active_turn()) and self.state["inbox"] and not self.stopping:
                # Explicitly queued work must not block later live corrections.
                job = next((item for item in self.state["inbox"]
                            if item.get("mode", "steer") != "queue"), None)
                if job is None:
                    break
                backend = self.backend
                active = backend.active
                if (not active or not active["turn"] or active["done"].done()
                        or backend.pending or backend.blocked):
                    await asyncio.sleep(0.1)
                    continue
                turn_id, thread_id = active["turn"], active["thread"]
                self.steering = True
                try:
                    if "preparedAttachments" not in job:
                        job["preparedAttachments"] = await self.media.incoming(job.get("attachments", []))
                        self.save()
                    if self.stopping or not any(item is job for item in self.state["inbox"]):
                        break
                    if backend.pending or backend.blocked:
                        continue
                    accepted = await backend.steer(thread_id, turn_id, job["text"],
                        attachments=job["preparedAttachments"], client_id=job["clientId"],
                        dispatch=lambda phase, turn: self.dispatch_input(job, phase, turn, backend))
                    if accepted:
                        self.state["inbox"] = [item for item in self.state["inbox"] if item is not job]
                        self.save()
                        self.acknowledge(job)
                    elif not self.stopping:
                        job.update(mode="queue", phase=InputPhase.PREPARING)
                        self.save()
                        self.reply(QUEUED_STEER)
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    uncertain = job.get("phase") == InputPhase.DISPATCHING
                    self.record_input(job, InputPhase.UNCERTAIN if uncertain else InputPhase.FAILED)
                    self.state["inbox"] = [item for item in self.state["inbox"] if item is not job]
                    self.save()
                    self.reply(UNCERTAIN_STEER if uncertain else
                               fill(ATTACHMENT_INCOMING_FAILED, {"原因": attachment_reason(error)})
                               if isinstance(error, MediaError) else TASK_FAILED)
                    print(f"追加消息失败：{type(error).__name__}", file=sys.stderr, flush=True)
                finally:
                    self.steering = False
                    self.wake.set()

    async def external_result_worker(self):
        while True:
            result = await self.external_results.get()
            try:
                if self.current_source(result.source):
                    await self.deliver_answer(result, finish_running=False, require_current=True)
            except Exception as error:
                self.reply("结果准备失败，请在桌面查看该轮回答。")
                print(f"外部结果失败：{type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                self.external_results.task_done()

    def fail_output(self, entry):
        """Freeze a partial group; never present a suffix as a complete answer."""
        self.state["outbox"].pop(0)
        group = entry.get("group")
        skipped = []
        if group:
            body_failed = entry.get("role", "body") == "body"
            kept = []
            for queued in self.state["outbox"]:
                if queued.get("group") == group and (body_failed or queued.get("role") == "completion"):
                    skipped.append(queued)
                else:
                    kept.append(queued)
            self.state["outbox"] = kept
        failed = {**entry, "status": "partial", "skipped": skipped}
        self.state["failedOutbox"] = (self.state.get("failedOutbox", []) + [failed])[-20:]
        # Never recursively report a failed failure notice.
        if not entry.get("failureNotice"):
            text = (fill(ATTACHMENT_SEND_FAILED, {"名称": entry["asset"]["name"], "次数": SEND_ATTEMPTS})
                    if "asset" in entry else "这条回复未能完整发送，已停止其余部分。请在桌面查看。")
            notes = self.text_entries(text, group=str(uuid.uuid4()), context=entry["context"])
            for note in notes:
                note["failureNotice"] = True
            self.state["outbox"][0:0] = notes
        self.save()
        if not self.state["outbox"]:
            self.outbox_drained.set()

    async def sender(self):
        while True:
            if not self.state["outbox"]:
                self.send_wake.clear()
                await self.send_wake.wait()
                continue
            entry = self.state["outbox"][0]
            try:
                if "asset" in entry and "item" not in entry:
                    entry["item"] = await self.media.upload(entry)
                    self.save()
                send_started = time.perf_counter()
                await self.channel.send(entry)
                send_ms = (time.perf_counter() - send_started) * 1000
            except Exception as error:
                entry["attempts"] = entry.get("attempts", 0) + 1
                if entry["attempts"] >= SEND_ATTEMPTS:
                    self.fail_output(entry)
                    continue
                self.save()
                print(f"发送失败（{type(error).__name__}），稍后重试；回复已保存。", file=sys.stderr)
                await asyncio.sleep(5)
                continue
            self.state["outbox"].pop(0)
            self.latency.sends.append(send_ms)
            self.save()
            if not self.state["outbox"]:
                self.outbox_drained.set()

    async def poller(self):
        delay = 2
        while True:
            try:
                batch = await self.channel.poll(self.state["cursor"])
                await self.receive(batch)
                delay = 2
            except Exception as error:
                print(f"接收失败：{error}", file=sys.stderr, flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def run(self):
        tasks = []
        try:
            if self.state.pop("restartRequested", False):
                self.reply(RESTART_READY)
            self.recover_inputs()
            tasks = [asyncio.create_task(fn()) for fn in
                     (self.poller, self.worker, self.steering_worker,
                      self.external_result_worker, self.sender, self.maintain_backend)]
            tasks.extend(asyncio.create_task(self.control_worker(lane)) for lane in self.controls)
            print("微信消息桥已启动；Ctrl+C 停止。", flush=True)
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.backend.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "start", "status"))
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".state")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.state_dir, lock=args.action != "status")
    if args.action == "login":
        await login(store)
    elif args.action == "status":
        state = store.load("bridge", {})
        print(json.dumps({"bound": bool(store.load("account")), "thread": state.get("thread"),
                          "backend": state.get("backendName", "codex"),
                          "unfinished_at_last_save": bool(state.get("currentInput") or state.get("running", False)),
                          "queued": len(state.get("inbox", [])),
                          "unsent": len(state.get("outbox", []))}, ensure_ascii=False, indent=2))
    else:
        account = store.load("account")
        if not account:
            raise RuntimeError("尚未绑定微信，请先运行 login")
        config = json.loads(args.config.read_text()) if args.config.exists() else {}
        cwd = Path(config.get("cwd", ROOT / "workspace")).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)
        bridge = Bridge(store, account, config)
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, main_task.cancel)
        await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
