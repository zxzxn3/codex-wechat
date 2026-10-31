#!/usr/bin/env python3
"""Local WeChat ↔ Codex bridge. Media transport uses cryptography."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import uuid

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
from configuration import ConfigLoader
from quota import quota_warnings
from records import InputPhase
from profiles import backend_label, definitions, snapshot
import service_control
from media import Media, MAX_ATTACHMENTS
from latency import Latency
from errors import UserFacingError, backend_reason
from prompts import (
    BACKEND_CONNECT_FAILED,
    BUSY_RESTART,
    QUEUE_FULL,
    RESTART_CONFIRM_UNSENT,
    RESTART_NOT_SERVICE,
    RESTART_READY,
    RESTART_STARTED,
    TOO_MANY_ATTACHMENTS,
    UNSUPPORTED_MESSAGE,
    fill,
    render_ack,
    status_fields,
)

ROOT = Path(__file__).resolve().parent


class Bridge:
    def __init__(self, store, account, config, backend_factory=CodexBackend, channel=None, config_path=None):
        self.store, self.account, self.config = store, account, config
        self.settings = ConfigLoader(config, config_path)
        self.state = store.load("bridge", {"cursor": "", "thread": None,
            "inbox": [], "outbox": [], "seen": [], "running": False})
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
        backend.refresh_config = self.reload_config
        backend.input_hint = self.current_input_hint
        backend.compaction_notice = self.notifications.compaction
        backend.on_limits = lambda payload: self.check_quota(backend, payload)
        backend.on_external_result = self.external_results.put_nowait
        return backend

    def current_input_hint(self):
        self.reload_config()
        return "\n\n" + self.source_hint if self.source_hint else ""

    def check_quota(self, backend, payload):
        if backend is not self.backend or not self.state.get("context"):
            return
        self.reload_config()
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
        return self.output.text_entries(text, group=group, context=context, semantic=semantic, source=source, role=role)

    def enqueue(self, entries, *, persist=True):
        return self.output.enqueue(entries, persist=persist)

    def reply(self, text, *, persist=True):
        return self.output.reply(text, persist=persist)

    def asset_entry(self, asset, *, group=None, context=None, source=None):
        return self.output.asset_entry(asset, group=group, context=context, source=source)

    async def queue_file(self, path, kind="auto", *, origin=None):
        return await self.output.queue_file(path, kind, origin=origin)

    def media_origin(self):
        return {"cwd": self.backend.info.get("cwd") or self.backend.cwd,
                "context": self.state["context"], "source": self.backend.source(self.state["thread"])}

    def current_source(self, source):
        return source == self.backend.source(self.state["thread"])

    async def deliver_answer(self, answer, *, finish_running=True, cwd=None,
                             notify_completion=False, source=None, require_current=False):
        return await self.output.deliver_answer(answer, finish_running=finish_running, cwd=cwd, notify_completion=notify_completion, source=source, require_current=require_current)

    def dispatch_input(self, job, phase, turn_id, backend):
        return self.inputs.dispatch_input(job, phase, turn_id, backend)

    def record_input(self, job, phase):
        return self.inputs.record_input(job, phase)

    def recover_inputs(self):
        return self.inputs.recover_inputs()

    async def receive(self, batch):
        self.reload_config()
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
                received = self.latency.received(msg.get("create_time_ms"))
                if spec and spec.lane == "media":
                    received["media"] = self.media_origin()
                controls.append((text, received))
            elif (text or attachments) and len(text) <= 20000:
                if len(self.state["inbox"]) >= 20:
                    self.reply(fill(QUEUE_FULL, {"数量": 20}))
                else:
                    self.state["inbox"].append({"id": identity, "text": text,
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
        await dispatch(self, text, received=received)


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
        bridge = Bridge(store, account, config, config_path=args.config)
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
