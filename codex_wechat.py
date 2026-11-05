#!/usr/bin/env python3
"""Local WeChat ↔ Codex bridge. Media transport uses cryptography."""
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

import autostart
from lifecycle import can_restart
import service_adapter
import service_control
import wechat_auth
from backend import CodexBackend
from output_delivery import OutputDelivery
from input_delivery import InputDelivery
from ingress import Ingress
from backend_session import BackendSession
from storage import Store
from wechat import WeChat, login
from commands import dispatch, notice, parse_control
from command_specs import CONTROL_LANES
from configuration import ConfigLoader, ConfigurationError, HotSettings, write_config
from prompts import (prompt_text)
from quota import quota_warnings
from profiles import backend_label, definitions, ensure_backend_config, snapshot
from rebind import run_rebind
from media import Media
from latency import Latency
from typing_indicator import TypingIndicator
from errors import UserFacingError, WeChatAuthExpired, WeChatPollTimeout
from platform_support import IS_WINDOWS, data_root, is_elevated, root_override_enabled
from diagnostics import error_fields, log_event, log_path
from log_viewer import show_logs
from prompts import (render_ack, status_fields, duration_label)

ROOT = Path(__file__).resolve().parent
SERVICE_CHILD_ENV = "CODEX_WECHAT_SERVICE_CHILD"
__all__ = ["Bridge", "parse_control"]

# The bridge assumes these keys always exist. A state file can be written before
# the bridge ever runs (the login action persists auth on its own), so loading
# must fill them in rather than trust the file.
BRIDGE_STATE_DEFAULTS = {
    "cursor": "",
    "thread": None,
    "inbox": [],
    "outbox": [],
    "seen": [],
    "running": False,
}


def load_bridge_state(store):
    """Read the saved state, repairing keys a partial writer never set."""
    saved = store.load("bridge")
    if not isinstance(saved, dict):
        saved = {}
    return {**deepcopy(BRIDGE_STATE_DEFAULTS), **saved}


def mark_active_release():
    """Keep the release a running bridge was loaded from out of pruning."""
    if ROOT.parent.name != "releases":
        return
    target = ROOT.parent.parent / "active-release"
    try:
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(ROOT.name + "\n", encoding="utf-8")
        if not IS_WINDOWS:
            temporary.chmod(0o600)
        os.replace(temporary, target)
    except OSError:
        pass


def show_log_path():
    """Point foreground users at the full structured log without copying it."""
    if sys.stdout.isatty():
        print(f"完整日志: {log_path()}", flush=True)


class Bridge:
    def __init__(self, store, account, config, backend_factory=CodexBackend, channel=None, config_path=None):
        self.store, self.account, self.config = store, account, config
        ensure_backend_config(self.config)
        self.settings = ConfigLoader(config, config_path)
        self.state = load_bridge_state(store)
        self.state.setdefault("consecutiveSendFailures", 0)
        self.state.setdefault("quoteCache", {})
        self.wechat_paused = wechat_auth.state(self.state) == wechat_auth.AUTH_REQUIRED
        self.auth_wake = asyncio.Event()
        self.auth_notice_lock = asyncio.Lock()
        self.auth_notice_attempted = False
        self.channel = channel or WeChat(
            account, idle_seconds=self.settings.hot.connection_idle_seconds)
        self.media = Media(store, self.channel, config.get("media"))
        self.backend_factory = backend_factory
        self.backend_configs = definitions(config)
        saved_backend = self.state.get("backendName")
        self.backend_name = (saved_backend if saved_backend is not None else
                             "codex" if "codex" in self.backend_configs else
                             next(iter(self.backend_configs)))
        if self.backend_name not in self.backend_configs:
            raise ValueError("保存的后端不存在于配置中：" + self.backend_name)
        self.controls = {lane: asyncio.Queue(maxsize=20) for lane in CONTROL_LANES}
        self.connection_lock = asyncio.Lock()
        self.input_paused = False
        self.backend_recovering = False
        self.latency = Latency()
        self.last_turn_seconds = None
        last_send = self.state.get("lastSendMs")
        if type(last_send) in (int, float) and last_send >= 0:
            self.latency.sends.append(float(last_send))
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
        self.outbox_drained = asyncio.Event()
        if not self.state["outbox"]:
            self.outbox_drained.set()
        self.output = OutputDelivery(self)
        self.inputs = InputDelivery(self)
        self.ingress = Ingress(self)
        self.backend_session = BackendSession(self)

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
        set_idle = getattr(self.channel, "set_idle_seconds", None)
        if set_idle is not None:
            set_idle(self.settings.hot.connection_idle_seconds)
        self.backend.compaction_notice = self.notifications.compaction
        if changed:
            self.save()
        if self.settings.failure_pending and (self.state.get("context") or self.state.get("wechatContextPending")):
            self.settings.failure_pending = False
            self.reply(self._render_prompt("CONFIG_RELOAD_FAILED"))
        return self.config

    def prompt(self, name, values=None):
        self.reload_config()
        return self._render_prompt(name, values)

    def _render_prompt(self, name, values=None):
        # Render the accepted snapshot without recursively reading a failing config.
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
        backend.report = lambda event, **fields: log_event(
            event, component="backend", backend=name, **fields)
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
        self.save()
        log_event("wechat.auth_required", level="warning", component="wechat",
                  queued=len(self.state.get("outbox", [])))
        async with self.auth_notice_lock:
            if self.auth_notice_attempted or (
                    details.get("notificationDelivered", False)
                    and details.get("notificationCommand") == command):
                return
            self.auth_notice_attempted = True
            for attempt in range(1, 4):
                try:
                    delivered = bool(await service_adapter.notify(
                        self.prompt("WECHAT_AUTH_TITLE"),
                        self.prompt("WECHAT_REBIND_HINT", {"command": command})))
                except Exception as exc:
                    delivered = False
                    log_event("wechat.auth_notice_failed", level="warning",
                              component="wechat", attempt=attempt, **error_fields(exc))
                else:
                    log_event("wechat.auth_notice", level="info" if delivered else "warning",
                              component="wechat", attempt=attempt, ok=delivered)
                if delivered:
                    details["notificationDelivered"] = True
                    details["notificationCommand"] = command
                    details.pop("notificationAttempted", None)
                    self.save()
                    return
                if attempt < 3:
                    await asyncio.sleep(5)

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
            self.reply(self.prompt("QUOTA_WARNING", {"backend": backend_label(
                self.backend_name, self.backend_configs),
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
        # Every delivery attempt re-sends the receipt: while a desktop holds the
        # writer the retries back off, so the repeats stay few and tell the user
        # the message is still being tried.
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
            backend=backend_label(self.backend_name, self.backend_configs),
            queued=len(self.state["inbox"]),
            pending=len(self.backend.pending),
            failed=self.state["consecutiveSendFailures"],
            name=details.get("name"),
            session_id=self.state.get("thread"),
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
            shared_fallback=self.backend.shared_fallback,
        )
        values.update(self.latency.values(ingress_ms))
        values["elapsed"] = duration_label(self.last_turn_seconds)
        sample = self.latency.provider_sample
        values["provider_latency_ms"] = (sample[1].removesuffix("ms")
                              if sample and sample[0] == self.backend.connection_id
                              and sample[1].endswith("ms") else "-")
        return values


    async def ensure_backend(self):
        await self.backend_session.ensure()

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

    async def prepare_asset(self, path, kind="auto", *, origin=None):
        return await self.output.prepare_asset(path, kind, origin=origin)

    def queue_asset(self, asset, *, origin=None):
        return self.output.queue_asset(asset, origin=origin)

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
        await self.ingress.receive(batch)

    async def control(self, text, *, received=None):
        await dispatch(self, text, received=received)


    async def restart_service(self):
        if not can_restart(self):
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
            except TimeoutError:
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
            started = time.perf_counter()
            try:
                if lane == "urgent":
                    await asyncio.wait_for(self.control(text, received=received), 5)
                else:
                    await self.control(text, received=received)
            except TimeoutError:
                self.reply(self.prompt("CONTROL_TIMEOUT"))
                log_event("control.timeout", level="warning", component="input",
                          kind=lane, request_id=received.get("request_id"), command=received.get("command"),
                          duration_ms=(time.perf_counter() - started) * 1000)
            except Exception as error:
                self.reply(self.prompt("CONTROL_FAILED"))
                log_event("control.failed", level="warning", component="input",
                          kind=lane, request_id=received.get("request_id"), command=received.get("command"),
                          duration_ms=(time.perf_counter() - started) * 1000,
                          **error_fields(error))
                print(f"控制失败：{type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                queue.task_done()
                self.wake.set()

    async def maintain_backend(self):
        await self.backend_session.maintain()

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
            except WeChatPollTimeout:
                # The transport reached the long-poll wait deadline. No cursor or
                # authentication state changes, and no reconnect backoff is needed.
                delay = 2
                continue
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
            show_log_path()
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.backend.close()
            finally:
                close_channel = getattr(self.channel, "close", None)
                if close_channel is not None:
                    await close_channel()
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


async def require_login_allowed():
    """Scanning a QR needs a terminal and no running service, on every platform.

    The PowerShell entry enforced this on Windows only; Linux reaches the same
    action directly, so the gate belongs here instead.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ConfigurationError("微信登录需要交互终端，不能重定向输入或输出。")
    try:
        current = await service_adapter.status()
    except ValueError as error:
        raise ConfigurationError(str(error)) from None
    except (OSError, RuntimeError):
        # A machine without a usable service manager still supports the
        # foreground bridge. The later state lock prevents overwriting a live
        # bridge if one happens to exist.
        return
    if current.running:
        raise ConfigurationError("微信登录前请先停止后台服务。")


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
        raise ConfigurationError("尚未绑定微信。请在终端运行 "
                                 + wechat_auth.start_command() + "，然后按提示扫码。")
    print("尚未绑定微信，请在当前终端扫码；绑定后会继续启动。", flush=True)
    await login(store, starting=True)
    account = store.load("account")
    if not account:
        raise ConfigurationError("微信绑定未完成，未启动桥接。")
    return account


def status_payload(store):
    state = load_bridge_state(store)
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


async def status_payload_with_service(store):
    """Bridge state plus service state when the host can be read."""
    payload = status_payload(store)
    try:
        current = await service_adapter.status()
    except (ValueError, RuntimeError, OSError) as error:
        log_event("service.status_unavailable", level="warning",
                  component="service", **error_fields(error))
        return payload
    service = {"installed": current.installed, "running": current.running}
    if current.state:
        service["state"] = current.state
    if current.last_result is not None:
        service["last_result"] = current.last_result
    payload["service"] = service
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
        client = WeChat(account)
        try:
            await client.send(entry)
        finally:
            await client.close()
    except Exception as error:
        print(f"关闭通知未发送：{type(error).__name__}", file=sys.stderr, flush=True)


async def stop_background_service(state_dir, config_path):
    """Stop only the installed background service without taking the bridge lock."""
    try:
        current = await service_adapter.status()
    except (OSError, RuntimeError, ValueError) as error:
        raise ConfigurationError(f"无法读取后台服务状态：{error}") from None
    if not current.installed:
        print("未安装后台服务。")
        return
    if not current.running:
        print("后台服务未运行。")
        return
    await send_stop_notification(state_dir, config_path)
    await service_adapter.stop_and_wait()
    print("后台服务已停止。")


async def restart_background_service():
    try:
        await service_adapter.restart()
    except (OSError, RuntimeError, ValueError) as error:
        # Same condition and same exit code as service-start.
        raise ConfigurationError(str(error)) from None
    print("后台服务已重启。")
    show_log_path()


async def start_background_service(args):
    """Start the installed service; bind interactively if this terminal can show QR."""
    try:
        current = await service_adapter.status()
    except (OSError, RuntimeError, ValueError) as error:
        raise ConfigurationError(str(error)) from None
    if not current.installed:
        raise ConfigurationError(service_adapter.NOT_INSTALLED)
    if current.stable_running:
        print("后台服务已在运行。")
        show_log_path()
        return
    try:
        store = await startup_store(args)
    except RuntimeError as error:
        raise ConfigurationError(str(error)) from None
    try:
        # service-start is still terminal-driven; a background task cannot show
        # a QR code. Use the same binding gate as foreground start.
        await startup_account(store)
    finally:
        # The service must be able to acquire the same state lock after launch;
        # holding it here makes Windows Task Scheduler exit immediately.
        store.close()
    try:
        await service_adapter.start()
    except (OSError, RuntimeError, ValueError) as error:
        raise ConfigurationError(str(error)) from None
    print("后台服务已启动；终端可以关闭。")
    show_log_path()


async def require_foreground_start_allowed():
    """A foreground bridge cannot share the state directory with the service."""
    try:
        current = await service_adapter.status()
    except (OSError, RuntimeError):
        return
    except ValueError as error:
        raise ConfigurationError(str(error)) from None
    if current.running:
        raise ConfigurationError("后台服务正在运行；请先运行 codex-wechat stop。")


async def manage_autostart(value):
    try:
        actual = await autostart.status() if value is None else await autostart.set_enabled(
            value == "true")
    except (OSError, RuntimeError, ValueError) as error:
        raise ConfigurationError(f"无法管理登录自启：{error}") from None
    print("true" if actual else "false")


def require_regular_user(action):
    if (is_elevated() and not root_override_enabled()
            and action not in {"status", "logs"}):
        raise ConfigurationError("请以普通用户身份运行，不要使用 root/sudo 或管理员终端。")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="status", choices=(
        "login", "start", "stop", "status", "rebind", "logs", "service-start",
        "service-restart", "autostart"))
    parser.add_argument("value", nargs="?", choices=("true", "false"))
    parser.add_argument("--state-dir", type=Path, default=data_root() / ".state")
    parser.add_argument("--config", type=Path, default=data_root() / "config.json")
    parser.add_argument("--stop-active", action="store_true")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--service", action="store_true",
                        help="同时查看服务控制台日志（Linux: journalctl；Windows: service.log；未脱敏）")
    parser.add_argument("--starting", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require_regular_user(args.action)
    if args.action != "rebind" and args.stop_active:
        parser.error("--stop-active 只能与 rebind 一起使用")
    if args.action != "logs" and args.follow:
        parser.error("--follow 只能与 logs 一起使用")
    if args.action != "logs" and args.service:
        parser.error("--service 只能与 logs 一起使用")
    if args.action != "login" and args.starting:
        parser.error("--starting 只能与 login 一起使用")
    if args.value is not None and args.action != "autostart":
        parser.error("true|false 只能与 autostart 一起使用")
    os.umask(0o077)
    if args.action == "logs":
        show_logs(args.follow, args.service)
        return
    if args.action == "stop":
        await stop_background_service(args.state_dir, args.config)
        return
    if args.action == "service-start":
        await start_background_service(args)
        return
    if args.action == "service-restart":
        await restart_background_service()
        return
    if args.action == "autostart":
        await manage_autostart(args.value)
        return
    if args.action == "rebind":
        await run_rebind(
            args.state_dir,
            args.config,
            stop_active=args.stop_active,
        )
        return
    if args.action == "login":
        await require_login_allowed()
    if args.action == "start" and os.environ.get(SERVICE_CHILD_ENV) != "1":
        await require_foreground_start_allowed()
    store = await startup_store(args)
    if args.action == "login":
        result = await login(store, starting=args.starting)
        state = load_bridge_state(store)
        if wechat_auth.mark_valid(state, result.account):
            store.save("bridge", state)
    elif args.action == "status":
        payload = await status_payload_with_service(store)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        account = await startup_account(store)
        try:
            config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config.exists() else {}
            if not isinstance(config, dict):
                raise ValueError("配置必须是 JSON 对象。")
            if ensure_backend_config(config):
                write_config(args.config, config)
            bridge = Bridge(store, account, config, config_path=args.config)
        except ValueError as error:
            raise ConfigurationError(str(error)) from None
        cwd = Path(config.get("cwd", data_root() / "workspace")).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        for signum in (signal.SIGINT, signal.SIGTERM):
            if IS_WINDOWS:
                signal.signal(signum, lambda *_: loop.call_soon_threadsafe(main_task.cancel))
            else:
                loop.add_signal_handler(signum, main_task.cancel)
        # Only a process that holds the bridge state lock and is ready to run
        # may replace the release-protection marker.
        if args.action == "start":
            mark_active_release()
        await bridge.run()


if __name__ == "__main__":
    if IS_WINDOWS:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
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
