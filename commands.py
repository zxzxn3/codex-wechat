"""WeChat conversation controls; lists come from the current backend."""
from functools import wraps
from lifecycle import can_change_session
from pathlib import Path
import asyncio
import sys
import time
import uuid
import autostart

from profiles import backend_argument, backend_label, switch
from errors import UserFacingError, attachment_reason, backend_reason
from codex_transport import CodexRpcError
from backend import WRITER_NATIVE
from media import MediaError
from records import InputPhase
from latency import provider_latency
from diagnostics import error_fields, log_event, request_context

from prompts import (
    MAX_NOTICE_BYTES, COMMAND_FAILURE_LABELS, panel, display_path, effort_label,
    session_age, session_name, session_summary, setting_summary, settings_summary,
    ping_channel, status_from_fields, prompt_text,
)
from command_specs import REGISTRY, COMMANDS, help_text
from configuration import processing_template, completion_template


def notice(bridge):
    return processing_template(bridge.reload_config(), bridge.state)


def info(bridge, action=None, *, include_backend=False):
    action = bridge.prompt("CURRENT_SESSION") if action is None else action
    details = bridge.backend.info
    status = bridge.prompt_fields()["status"]
    text = session_summary(action, name=details.get("name"), model=details.get("model"),
                           effort=details.get("effort"), cwd=details.get("cwd") or bridge.backend.cwd,
                           backend=backend_label(bridge.backend_name) if include_backend else None,
                           status=status, text=bridge.prompt)
    if bridge.backend.blocked:
        return text + "\n" + bridge.prompt("BLOCKED_NOTE")
    return text


async def adopt(bridge, thread_id, overrides, thread):
    """Resume the target, or adopt it without a writer when the desktop owns it.

    A desktop that holds the thread and cannot be followed must not fail the
    session: the bridge would stay unloaded, pause input and retry forever.
    """
    try:
        return await bridge.backend.thread(thread_id, overrides)
    except CodexRpcError as error:
        if not (error.code == -32600 and "already has an active writer" in str(error.reason)):
            raise
        return await bridge.backend.adopt_thread(thread_id, thread)


async def activate(bridge, thread_id=None, overrides=None):
    # Only this explicit command may override native settings. Never replay a mobile cache.
    saved = dict(overrides or {})
    previous = bridge.state.get("thread")
    desktop = False
    thread = None
    if thread_id:
        thread = await bridge.backend.read_thread(thread_id)
        status = thread.get("status", {}).get("type")
        if status == "systemError":
            raise UserFacingError(bridge.prompt("SESSION_TARGET_BLOCKED"))
        if status == "active":
            desktop = True
            if not bridge.backend.shared:
                raise UserFacingError(bridge.prompt("SESSION_TARGET_ACTIVE"))
            if overrides:
                raise UserFacingError(bridge.prompt("BUSY_CHANGE"))
            # Adopt the desktop's current settings instead of applying stale mobile overrides.
            saved = {}
    bridge.loaded = False
    selected = await adopt(bridge, thread_id, saved, thread)
    if previous and previous != selected:
        # Leaving a thread must not keep its writer, or the App shows it locked.
        await bridge.backend.release_thread(previous)
    bridge.state["thread"] = selected
    bridge.state.pop("sessionOptions", None)  # Retire legacy override replay after successful adoption.
    bridge.loaded = True
    bridge.save()
    # Adoption itself needs no writer: return it so the App can open this thread
    # while the bridge is idle. The next turn resumes it or follows the App.
    if bridge.backend.writer_mode in (None, WRITER_NATIVE):
        await bridge.backend.release_thread(selected, follow=True)
    return desktop


async def ensure_session(bridge):
    await bridge.ensure_backend()
    if not bridge.loaded:
        had_thread = bool(bridge.state["thread"])
        desktop = await activate(bridge, bridge.state["thread"])
        if desktop:
            action = bridge.prompt("SESSION_DESKTOP")
        elif had_thread:
            action = bridge.prompt("SESSION_RESTORED")
        else:
            action = bridge.prompt("SESSION_CREATED")
        bridge.reply(info(bridge, action))


async def model_entries(bridge):
    return await bridge.backend.models()


def effort_choices(model):
    return [item["reasoningEffort"] for item in model.get("supportedReasoningEfforts", [])]


async def command_settings(bridge, command, argument, received=None):
    if argument:
        parts = argument.split()
        if len(parts) != 2 or parts[0] != "autostart" or parts[1] not in ("on", "off"):
            bridge.reply(bridge.prompt("SETTINGS_USAGE"))
            return
        try:
            if (parts[1] == "on" and not autostart.IS_WINDOWS
                    and not await autostart.installed()):
                await bridge.enable_background_service()
                return
            enabled = await autostart.set_enabled(parts[1] == "on")
        except UserFacingError:
            raise
        except (TimeoutError, ValueError, RuntimeError, OSError):
            raise UserFacingError(bridge.prompt("AUTOSTART_FAILED")) from None
        bridge.reply(bridge.prompt("LABEL_AUTOSTART") + ": " +
                     bridge.prompt("VALUE_ON" if enabled else "VALUE_OFF"))
        return
    try:
        autostart_text = bridge.prompt("VALUE_ON" if await autostart.status() else "VALUE_OFF")
    except (TimeoutError, ValueError, RuntimeError, OSError):
        autostart_text = bridge.prompt("VALUE_UNAVAILABLE")
    bridge.reply(settings_summary(
        notice=notice(bridge),
        completion_notice=completion_template(bridge.reload_config(), bridge.state),
        source_hint=bridge.source_hint,
        attachment_mib=bridge.media.limit // (1024 * 1024),
        slicing=bridge.slicing,
        notifications=bridge.notifications,
        autostart=autostart_text,
        connection_idle_seconds=bridge.settings.hot.connection_idle_seconds,
        text=bridge.prompt,
    ))
    return


async def command_backend_view(bridge, command, argument, received=None):
    current = backend_argument(bridge.backend_name)
    names = list(bridge.backend_configs)
    available = " | ".join(backend_argument(name) for name in names)
    bridge.reply(bridge.prompt("BACKEND_SUMMARY", {
        "current": current,
        "available": available,
    }))
    return


async def command_notice(bridge, command, argument, received=None):
    label = bridge.prompt("LABEL_ACK" if command == "/ack" else "LABEL_FINISH")
    key = "processingNotice" if command == "/ack" else "completionNotice"
    prefix = "ACK" if command == "/ack" else "FINISH"
    usage = bridge.prompt(prefix + "_USAGE")
    if argument == "fields":
        bridge.reply(panel(*(f"{{{name}}}" for name in bridge.prompt_fields())))
        return
    template = (notice(bridge) if command == "/ack" else
                completion_template(bridge.reload_config(), bridge.state))
    if not argument:
        bridge.reply(bridge.prompt(prefix + "_VIEW", {"template": template or bridge.prompt("VALUE_OFF")}))
        return
    if argument == "text":
        value = ""
    elif argument.startswith("text "):
        value = argument[5:].strip()
        if value in ('""', "''"):
            value = ""
    else:
        raise UserFacingError(usage)
    if len(value.encode()) > MAX_NOTICE_BYTES:
        raise UserFacingError(bridge.prompt(prefix + "_TOO_LONG", {"max_bytes": MAX_NOTICE_BYTES}))
    bridge.state[key] = value
    bridge.save()
    bridge.reply(panel(label + ": " + (value or bridge.prompt("VALUE_OFF"))))
    return


async def _session_catalog(bridge, search):
    catalog, cursor, cursors, identities = [], None, set(), set()
    while True:
        result = await bridge.backend.list_threads(cursor, search)
        for thread in result["data"]:
            if thread["id"] not in identities:
                identities.add(thread["id"])
                catalog.append(thread)
        cursor = result.get("nextCursor")
        if not cursor:
            break
        if cursor in cursors:
            raise UserFacingError(bridge.prompt("SESSION_LIST_FAILED"))
        cursors.add(cursor)
    return catalog


def _session_lines(bridge, threads):
    lines = []
    for index, thread in enumerate(threads, 1):
        title = session_name(thread.get("name"))
        current = thread["id"] == bridge.state["thread"]
        status = thread.get("status", {}).get("type")
        labels = []
        if current:
            labels.append("当前")
        if status == "active":
            labels.append("工作中")
        elif status == "systemError":
            labels.append("需要处理")
        if current or not labels:
            age = session_age(thread.get("updatedAt"))
            if age:
                labels.append(age)
        state = "  ".join(labels)
        lines.append(f"{index}. {title}" + (f"  [{state}]" if state else ""))
    if not threads:
        lines.append(bridge.prompt("NO_SESSIONS"))
    return lines


async def command_sessions(bridge, command, argument, received=None):
    if argument == "next":
        raise UserFacingError(bridge.prompt("COMMAND_USAGE", {"usage": REGISTRY['/sessions'].usage}))
    page = int(argument) if argument.isdecimal() else 1
    search = bridge.state.get("sessionSearch") if argument.isdecimal() else argument or None
    if page < 1:
        raise UserFacingError(bridge.prompt("PAGE_INVALID"))
    cache = bridge.session_catalog
    key = (bridge.backend.connection_id, search)
    if (not argument.isdecimal() or cache is None or cache[0] != key
            or time.monotonic() - cache[1] > 60):
        catalog = await _session_catalog(bridge, search)
        bridge.session_catalog = (key, time.monotonic(), catalog)
    else:
        catalog = cache[2]
    total = max(1, (len(catalog) + 9) // 10)
    if page > total:
        raise UserFacingError(bridge.prompt("PAGE_RANGE", {"count": total}))
    threads = catalog[(page - 1) * 10:page * 10]
    bridge.state["sessionChoices"] = [thread["id"] for thread in threads]
    bridge.state.pop("sessionCursor", None)
    bridge.state["sessionSearch"] = search
    bridge.save()
    lines = _session_lines(bridge, threads)
    hints = [f"`{REGISTRY['/switch'].usage}`",
             f"`{REGISTRY['/sessions'].usage}` " +
             bridge.prompt("SESSION_PAGE", {"page": page, "total_pages": total})]
    bridge.reply(panel(*lines) + "\n" + "\n".join(hints))
    return


async def command_model_view(bridge, command, argument, received=None):
    models = await model_entries(bridge)
    bridge.model_choices = models
    lines = []
    for index, model in enumerate(models, 1):
        model_name = model["model"]
        lines.append(f"{index}. {model_name}")
    if not models:
        lines.append(bridge.prompt("NO_MODELS"))
    bridge.reply(panel(*lines) + f"\n`{REGISTRY['/model'].usage}`")
    return


async def command_cwd_view(bridge, command, argument, received=None):
    bridge.reply(bridge.prompt("WORKING_DIRECTORY", {
        "cwd": display_path(bridge.backend.info.get("cwd") or bridge.backend.cwd)}))
    return


def session_control(handler):
    """Keep the existing session mutation barrier around each command handler."""
    @wraps(handler)
    async def guarded(bridge, command, argument, received=None):
        if command == "/compact" and argument:
            raise UserFacingError(bridge.prompt("COMMAND_USAGE", {"usage": REGISTRY['/compact'].usage}))
        if command == "/new" and argument:
            raise UserFacingError(bridge.prompt("NEW_USAGE"))
        if command == "/switch" and not argument:
            raise UserFacingError(bridge.prompt("SESSION_USAGE"))
        if not can_change_session(bridge):
            raise UserFacingError(bridge.prompt("BUSY_CHANGE"))
        async with bridge.session_lock:
            return await handler(bridge, command, argument, received)
    return guarded


@session_control
async def _change_backend(bridge, command, argument, received=None):
    await switch(bridge, argument)
    bridge.reply(info(bridge, bridge.prompt("BACKEND_SWITCHED"), include_backend=True))
    return


@session_control
async def command_new(bridge, command, argument, received=None):
    await activate(bridge)
    bridge.reply(info(bridge, bridge.prompt("SESSION_CREATED")))
    return


@session_control
async def command_switch(bridge, command, argument, received=None):
    target = argument
    if argument.isdecimal():
        choices = bridge.state.get("sessionChoices", [])
        index = int(argument) - 1
        if not 0 <= index < len(choices):
            raise UserFacingError(bridge.prompt("SESSION_INDEX_INVALID"))
        target = choices[index]
    elif not _is_thread_id(argument):
        matches = await _session_catalog(bridge, argument)
        exact = next((thread for thread in matches
                      if thread["id"] == argument), None)
        if exact is not None:
            matches = [exact]
        if len(matches) == 1:
            target = matches[0]["id"]
        elif not matches:
            raise UserFacingError(bridge.prompt(
                "SESSION_SEARCH_EMPTY", {"query": argument}))
        else:
            shown = matches[:10]
            bridge.state["sessionChoices"] = [thread["id"] for thread in shown]
            bridge.state["sessionSearch"] = argument
            bridge.save()
            text = panel(*_session_lines(bridge, shown)) + "\n" + bridge.prompt(
                "SESSION_SEARCH_MULTIPLE", {"count": len(matches)})
            if len(matches) > len(shown):
                text += "\n" + bridge.prompt("SESSION_SEARCH_MORE", {
                    "count": len(shown), "query": argument})
            bridge.reply(text)
            return
    desktop = await activate(bridge, target)
    bridge.reply(info(bridge, bridge.prompt("SESSION_DESKTOP") if desktop else bridge.prompt("SESSION_SWITCHED")))
    return


@session_control
async def command_compact(bridge, command, argument, received=None):
    await ensure_session(bridge)
    if bridge.backend.blocked:
        raise UserFacingError(bridge.prompt("COMPACT_BLOCKED"))
    await bridge.backend.compact(bridge.state["thread"])
    bridge.reply(bridge.prompt("COMPACT_REQUESTED"))
    return


@session_control
async def _change_model(bridge, command, argument, received=None):
    await ensure_session(bridge)
    models = (bridge.model_choices if argument.isdecimal() and bridge.model_choices
              else await model_entries(bridge))
    overrides = {}
    selected = None
    if argument.isdecimal():
        index = int(argument) - 1
        if 0 <= index < len(models):
            selected = models[index]
    else:
        selected = next((m for m in models if m["model"] == argument), None)
    if selected is None:
        raise UserFacingError(bridge.prompt("MODEL_INVALID"))
    overrides["model"] = selected["model"]
    # Model-specific defaults prevent carrying an unsupported effort to another model.
    overrides["effort"] = selected.get("defaultReasoningEffort")
    await _apply_settings(bridge, command, overrides)


async def _current_effort_choices(bridge):
    await ensure_session(bridge)
    models = await model_entries(bridge)
    current = next((m for m in models
                    if m["model"] == bridge.backend.info.get("model")), None)
    if current is None:
        raise UserFacingError(bridge.prompt("MODEL_NOT_IN_CATALOG"))
    return effort_choices(current)


async def command_effort_view(bridge, command, argument, received=None):
    choices = await _current_effort_choices(bridge)
    bridge.reply(bridge.prompt("EFFORT_SUMMARY", {
        "current": effort_label(bridge.backend.info.get("effort")),
        "choices": " | ".join(effort_label(value) for value in choices) or "无",
    }))


@session_control
async def _change_effort(bridge, command, argument, received=None):
    choices = await _current_effort_choices(bridge)
    selected_effort = next(
        (value for value in choices
         if value.casefold() == argument.casefold()), None)
    if selected_effort is None:
        raise UserFacingError(bridge.prompt("EFFORT_INVALID", {
            "choices": " | ".join(effort_label(value) for value in choices) or "无"}))
    await _apply_settings(bridge, command, {"effort": selected_effort})


@session_control
async def _change_cwd(bridge, command, argument, received=None):
    await ensure_session(bridge)
    overrides = {}
    base = Path(bridge.backend.info.get("cwd") or bridge.backend.cwd)
    path = Path(argument).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise UserFacingError(bridge.prompt("DIRECTORY_INVALID")) from None
    if not path.is_dir():
        raise UserFacingError(bridge.prompt("DIRECTORY_NOT_FOLDER"))
    overrides["cwd"] = str(path)
    await _apply_settings(bridge, command, overrides)


async def _apply_settings(bridge, command, overrides):
    await activate(bridge, bridge.state["thread"], overrides)
    details = bridge.backend.info
    bridge.reply(setting_summary(model=details.get("model"), effort=details.get("effort"),
                                 cwd=details.get("cwd") or bridge.backend.cwd,
                                 field=command.removeprefix("/"), text=bridge.prompt))


def _is_thread_id(value):
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


async def command_backend(bridge, command, argument, received=None):
    if not argument:
        return await command_backend_view(bridge, command, argument, received)
    return await _change_backend(bridge, command, argument, received)


async def command_model(bridge, command, argument, received=None):
    if not argument:
        return await command_model_view(bridge, command, argument, received)
    return await _change_model(bridge, command, argument, received)


async def command_effort(bridge, command, argument, received=None):
    if not argument:
        return await command_effort_view(bridge, command, argument, received)
    return await _change_effort(bridge, command, argument, received)


async def command_cwd(bridge, command, argument, received=None):
    if not argument:
        return await command_cwd_view(bridge, command, argument, received)
    return await _change_cwd(bridge, command, argument, received)


async def command_help(bridge, command, argument, received=None):
    bridge.reply(help_text(text=bridge.prompt))


async def command_ping(bridge, command, argument, received=None):
    if argument:
        bridge.reply(bridge.prompt("PING_USAGE"))
    else:
        ingress = received.get("ingress_ms") if received else None
        channel = ping_channel(bridge.latency.values(ingress), text=bridge.prompt)
        # The provider probe can block for the full connect timeout, so answer
        # the channel leg first and report the connection phase afterwards.
        bridge.reply(channel)
        backend = bridge.backend
        connection = backend.connection_id
        provider = await provider_latency(backend)
        if backend is bridge.backend and connection == backend.connection_id:
            bridge.latency.provider_sample = (connection, provider)
        bridge.reply(bridge.prompt("PING_ROUTE", {"channel": channel, "provider_latency": provider}))


async def command_send(bridge, command, argument, received=None):
    try:
        if not argument:
            bridge.reply(bridge.prompt("MEDIA_USAGE"))
            return
        if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in ("'", '"'):
            argument = argument[1:-1]
        await bridge.queue_file(argument, {"/sendfile": "file", "/sendimage": "image",
                                           "/sendvideo": "video"}.get(command, "auto"),
                              origin=(received or {}).get("media"))
        bridge.reply(bridge.prompt("ATTACHMENT_QUEUED"))
    except (MediaError, ValueError, OSError) as error:
        bridge.reply(bridge.prompt("SEND_FILE_FAILED", {"reason": attachment_reason(error)}))


async def command_restart(bridge, command, argument, received=None):
    if argument:
        bridge.reply(bridge.prompt("RESTART_USAGE"))
        return
    try:
        await bridge.restart_service()
    except UserFacingError as error:
        bridge.reply(bridge.prompt("RESTART_REJECTED", {"reason": str(error)}))
    except (TimeoutError, RuntimeError, OSError) as error:
        bridge.reply(bridge.prompt("RESTART_FAILED", {"reason": backend_reason(error)}))


async def command_status(bridge, command, argument, received=None):
    bridge.reply(status_from_fields(bridge.prompt_fields(), text=bridge.prompt))


async def command_unqueue(bridge, command, argument, received=None):
    # No await between selecting, removing and saving: the worker cannot claim it midway.
    inbox = bridge.state["inbox"]
    for index in range(len(inbox) - 1, -1, -1):
        job = inbox[index]
        if (job.get("mode", "steer") == "queue"
                and job.get("phase", InputPhase.PREPARING) == InputPhase.PREPARING):
            inbox.pop(index)
            bridge.record_input(job, InputPhase.CANCELLED)
            bridge.save()
            count = len(inbox)  # Same waiting-only count as /queue; currentInput is excluded.
            bridge.reply(bridge.prompt("UNQUEUE_CANCELLED", {
                "count": count, "unit": "task" if count == 1 else "tasks"}))
            return
    bridge.reply(bridge.prompt("UNQUEUE_EMPTY"))


async def command_stop(bridge, command, argument, received=None):
    bridge.stopping = True
    cleared = len(bridge.state["inbox"])
    cleared_note = bridge.prompt("STOP_CLEARED", {"count": cleared}) if cleared else ""
    for job in bridge.state["inbox"]:
        if job.get("phase") in (InputPhase.DISPATCHING, InputPhase.ACCEPTED):
            bridge.record_input(job, InputPhase.UNCERTAIN if job["phase"] == InputPhase.DISPATCHING
                              else InputPhase.ACCEPTED)
    bridge.state["inbox"].clear()
    bridge.save()
    try:
        stopped = await bridge.backend.stop()
        if stopped:
            bridge.reply(bridge.prompt("STOP_REQUESTED") + cleared_note)
        elif bridge.running or bridge.active_turn():
            bridge.reply(bridge.prompt("STOP_DEFERRED") + cleared_note)
        else:
            bridge.reply(bridge.prompt("STOP_IDLE") + cleared_note)
    except (TimeoutError, RuntimeError, OSError):
        bridge.reply(bridge.prompt("STOP_UNCONFIRMED") + cleared_note)


async def command_approval(bridge, command, argument, received=None):
    try:
        accept = command == "/approve"
        ok = await bridge.backend.answer_approval(
            argument, "accept" if accept else "decline")
        bridge.reply(bridge.prompt("APPROVAL_ACCEPTED") if ok and accept else
                   bridge.prompt("APPROVAL_DECLINED") if ok else bridge.prompt("APPROVAL_EXPIRED"))
    except (TimeoutError, RuntimeError, OSError):
        bridge.reply(bridge.prompt("APPROVAL_FAILED"))


async def command_answer(bridge, command, argument, received=None):
    parts = argument.split(maxsplit=1)
    if len(parts) != 2:
        raise UserFacingError(bridge.prompt("COMMAND_USAGE", {"usage": REGISTRY[command].usage}))
    try:
        ok = await bridge.backend.answer_question(*parts)
        bridge.reply(bridge.prompt("ANSWER_ACCEPTED") if ok else bridge.prompt("ANSWER_EXPIRED"))
    except ValueError as error:
        bridge.reply(str(error))
    except (TimeoutError, RuntimeError, OSError):
        bridge.reply(bridge.prompt("ANSWER_FAILED"))


_HANDLER_FUNCTIONS = {
    "status": command_status,
    "settings": command_settings,
    "sessions": command_sessions,
    "new": command_new,
    "compact": command_compact,
    "unqueue": command_unqueue,
    "stop": command_stop,
    "ping": command_ping,
    "switch": command_switch,
    "backend": command_backend,
    "model": command_model,
    "effort": command_effort,
    "cwd": command_cwd,
    "notice": command_notice,
    "approval": command_approval,
    "answer": command_answer,
    "send": command_send,
    "restart": command_restart,
    "help": command_help,
}
HANDLERS = {
    spec.name: _HANDLER_FUNCTIONS[spec.handler or spec.name.removeprefix("/")]
    for spec in COMMANDS if spec.route != "input"
}


def parse_control(text):
    """Trim only command boundaries; preserve the argument's internal whitespace."""
    parts = str(text or "").strip().split(maxsplit=1)
    return (parts[0], parts[1].strip() if len(parts) > 1 else "") if parts else ("", "")


def validate(spec, argument, *, text=prompt_text):
    if (spec.usage == spec.name and argument) or (spec.required and not argument):
        raise UserFacingError(text("COMMAND_USAGE", {"usage": spec.usage}))
    if spec.name == "/sessions":
        if argument == "next":
            raise UserFacingError(text("COMMAND_USAGE", {"usage": spec.usage}))
        if argument.isdecimal() and int(argument) < 1:
            raise UserFacingError(text("PAGE_INVALID"))


async def handle(bridge, command, argument, received=None):
    """Invoke a registered handler; also usable with injected backends in offline tests."""
    spec = REGISTRY.get(command)
    if spec is None or spec.route == "input":
        bridge.reply(bridge.prompt("UNKNOWN_COMMAND"))
        return
    validate(spec, argument, text=bridge.prompt)
    await HANDLERS[spec.name](bridge, spec.name, argument, received)


async def dispatch(bridge, text, *, received=None):
    command, argument = parse_control(text)
    spec = REGISTRY.get(command)
    request_id = received.get("request_id") if isinstance(received, dict) else None
    started = time.perf_counter()
    fields = {"request_id": request_id, "command": spec.name if spec else None}
    arrival = received.get("received") if isinstance(received, dict) else None
    wait = {"queue_wait_ms": max(0, (started - arrival) * 1000)} if type(arrival) in (int, float) else {}
    with request_context(request_id):
        log_event("control.started", component="input", kind="command", **fields, **wait)
        try:
            if spec is None or spec.route == "input":
                log_event("control.rejected", component="input", kind="command",
                          status="unknown", duration_ms=(time.perf_counter() - started) * 1000, **fields)
                bridge.reply(bridge.prompt("UNKNOWN_COMMAND"))
                return
            validate(spec, argument, text=bridge.prompt)  # Reject syntax before opening a backend connection.
            if spec.needs_backend and not (spec.query_local and not argument):
                await bridge.ensure_backend()
            await handle(bridge, spec.name, argument, received)
            log_event("control.completed", component="input", kind="command",
                      duration_ms=(time.perf_counter() - started) * 1000, **fields)
        except asyncio.CancelledError:
            log_event("control.cancelled", level="debug", component="input", kind="command",
                      duration_ms=(time.perf_counter() - started) * 1000, **fields)
            raise
        except UserFacingError as error:
            log_event("control.rejected", component="input", kind="command",
                      status="invalid", duration_ms=(time.perf_counter() - started) * 1000, **fields)
            bridge.reply(str(error))
        except (TimeoutError, ValueError, RuntimeError, OSError) as error:
            failed_command = spec.name if spec else command
            label = (bridge.prompt("FAILURE_" + failed_command[1:].upper())
                     if failed_command in COMMAND_FAILURE_LABELS else bridge.prompt("GENERIC_FAILURE"))
            bridge.reply(bridge.prompt("COMMAND_FAILURE", {"action": label, "reason": str(error) if isinstance(error, CodexRpcError) else backend_reason(error)}))
            log_event("control.failed", level="warning", component="input",
                      kind="command", duration_ms=(time.perf_counter() - started) * 1000,
                      **fields, **error_fields(error))
            print(f"{command}失败：{str(error) if isinstance(error, CodexRpcError) else type(error).__name__}", file=sys.stderr, flush=True)
