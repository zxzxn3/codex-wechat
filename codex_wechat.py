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
from configuration import ConfigLoader, ConfigurationError
from quota import quota_warnings
from records import InputPhase
from profiles import backend_label, definitions, snapshot
from rebind import run_rebind
from media import Media, MAX_ATTACHMENTS
from latency import Latency, message_timestamps
from errors import UserFacingError, BackendStartupError, WeChatAuthExpired, backend_reason
from platform_support import IS_WINDOWS, data_root
from diagnostics import BACKUP_COUNT, error_fields, log_event, log_path, new_request_id
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
        self.state.setdefault("consecutiveSendFailures", 0)
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

    def save(self):
        self.state["backendName"] = self.backend_name
        self.state.setdefault("backendSessions", {})[self.backend_name] = snapshot(self.state)
        self.store.save("bridge", self.state)

    def make_backend(self, name):
        backend = self.backend_factory(self.backend_configs[name],
            str(Path(self.config.get("cwd", data_root() / "workspace")).expanduser().resolve()),
            lambda text: self.reply(text) if getattr(self, "backend", None) is backend else None)
        backend.on_activity = self.backend_activity
        backend.source_name = name
        backend.refresh_config = self.reload_config
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
                await service_adapter.notify("微信绑定已失效", "重新绑定\n" + command)
            except Exception:
                print("微信绑定已失效；重新绑定\n" + wechat_auth.rebind_command(), file=sys.stderr)

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
            request_id = new_request_id()
            if (msg.get("from_user_id") != self.account["user"] or msg.get("group_id")
                    or msg.get("message_type") != 1):
                continue
            # Use a stable fallback when the backend omits message_id.
            identity = msg.get("message_id") or msg.get("client_id") or hashlib.sha256(
                json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            identity = str(identity)
            if not msg.get("context_token"):
                # Cannot safely reply without a context token; don't execute.
                continue
            wechat_auth.accept_context(self.state, msg["context_token"])
            if identity in self.state["seen"]:
                continue
            self.state["seen"].append(identity)
            text = "\n".join(item.get("text_item", {}).get("text", "")
                             for item in msg.get("item_list", []) if item.get("type") == 1).strip()
            attachments = [item for item in msg.get("item_list", []) if item.get("type") in (2, 4)]
            command, argument = parse_control(text)
            spec = REGISTRY.get(command)
            queued = bool(spec and spec.route == "input")
            received = self.latency.received_any(message_timestamps(msg))
            if queued:
                text = argument
            kind = ("queued" if queued else "command" if text.startswith("/") and not attachments
                    else "input" if text or attachments else "unsupported")
            log_event("input.received", component="input", request_id=request_id,
                      kind=kind, chars=len(text), count=len(attachments))
            if queued and not text and not attachments:
                self.reply(f"用法: `{REGISTRY['/queue'].usage}`")
            elif len(attachments) > MAX_ATTACHMENTS:
                self.reply(fill(TOO_MANY_ATTACHMENTS, {"数量": MAX_ATTACHMENTS}))
            elif text.startswith("/") and not attachments and not queued:
                received["request_id"] = request_id
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
                                               "ingress_ms": received.get("ingress_ms"),
                                               "requestId": request_id})
                    if queued:
                        self.reply("已排队", persist=False)
            else:
                self.reply(fill(UNSUPPORTED_MESSAGE, {"数量": 20000}))
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
                self.reply("指令等待过多，请稍后重试。")
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
            raise UserFacingError("自定义状态或配置路径不能交给默认服务，请自行管理服务。")
        if (self.restarting or self.running or self.steering or self.active_turn()
                or (self.session_lock.locked() and not self.backend_recovering)
                or self.backend.pending):
            raise UserFacingError("首次启用自启需要移交后台服务，请等任务结束或用 /stop 停止后再试。")
        self.restarting = True
        try:
            await autostart.set_enabled(True, start_if_missing=False)
            self.reply("自启: 开启")
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
            raise UserFacingError(BUSY_RESTART)
        # No await before closing the dispatch barrier. Preparing work is recoverable;
        # queued work must not make a disconnected bridge impossible to restart.
        self.restarting = True
        try:
            try:
                await service_control.require_service_owner()
            except ValueError:
                raise UserFacingError(RESTART_NOT_SERVICE) from None
            self.reply(RESTART_STARTED)
            try:
                await asyncio.wait_for(self.outbox_drained.wait(), 20)
            except asyncio.TimeoutError:
                raise UserFacingError(RESTART_CONFIRM_UNSENT) from None
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
                self.reply("控制请求未确认，请查看 `/status`；不会自动重发。")
                log_event("control.timeout", level="warning", component="input",
                          kind=lane, request_id=received.get("request_id"))
            except Exception as error:
                self.reply("指令未完成，请查看 `/status` 后重试。")
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
                        self.reply(fill(BACKEND_CONNECT_FAILED, {"原因": reason}))
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
                self.reply(RESTART_READY)
            self.recover_inputs()
            if self.wechat_paused:
                await self.require_wechat_rebind()
            tasks = [asyncio.create_task(fn()) for fn in
                     (self.poller, self.worker, self.steering_worker,
                      self.external_result_worker, self.sender, self.maintain_backend)]
            tasks.extend(asyncio.create_task(self.control_worker(lane)) for lane in self.controls)
            log_event("bridge.start", component="bridge", backend=self.backend_name,
                      connected=self.backend.connected)
            print("微信消息桥已启动；Ctrl+C 停止。", flush=True)
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


def _log_files():
    try:
        primary = log_path()
    except (OSError, RuntimeError):
        return []
    candidates = [primary]
    candidates.extend(primary.with_name(f"{primary.name}.{number}")
                      for number in range(1, BACKUP_COUNT + 1))
    candidates.append(primary.parent / "service.log")
    return [path for path in candidates if path.is_file()]


def _tail_lines(path, limit=200):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return stream.read().splitlines()[-limit:]
    except OSError:
        return []


def show_logs(follow=False):
    files = _log_files()
    if not files:
        print("暂无诊断日志；运行 start 或 service-start 后生成。")
        return
    for path in files:
        print(f"==> {path} <==")
        print("\n".join(_tail_lines(path)))
    if not follow:
        return
    positions = {path: path.stat().st_size for path in files}
    while True:
        time.sleep(0.5)
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < positions.get(path, 0):
                positions[path] = 0
            try:
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(positions.get(path, 0))
                    chunk = stream.read()
                    positions[path] = stream.tell()
                if chunk:
                    print(chunk, end="", flush=True)
            except OSError:
                continue


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "start", "status", "rebind", "logs"))
    parser.add_argument("--state-dir", type=Path, default=data_root() / ".state")
    parser.add_argument("--config", type=Path, default=data_root() / "config.json")
    parser.add_argument("--stop-active", action="store_true")
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    if args.action != "rebind" and args.stop_active:
        parser.error("--stop-active 只能与 rebind 一起使用")
    if args.action != "logs" and args.follow:
        parser.error("--follow 只能与 logs 一起使用")
    os.umask(0o077)
    if args.action == "logs":
        show_logs(args.follow)
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
