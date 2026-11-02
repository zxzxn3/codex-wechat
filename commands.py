"""WeChat conversation controls; lists come from the current backend."""
from pathlib import Path
import asyncio
import sys
import time
import autostart

from profiles import backend_argument, backend_label, switch
from errors import UserFacingError, attachment_reason, backend_reason
from codex_transport import CodexRpcError
from media import MediaError
from records import InputPhase
from latency import provider_latency

from prompts import (
    ACK_USAGE,
    ACK_VIEW,
    ACK_TOO_LONG,
    BACKEND_SUMMARY,
    BLOCKED_NOTE,
    BUSY_CHANGE,
    DESKTOP_NOTE,
    DIRECTORY_INVALID,
    DIRECTORY_NOT_FOLDER,
    EFFORT_INVALID,
    EFFORT_SUMMARY,
    MAX_NOTICE_BYTES,
    MODEL_INVALID,
    MODEL_NOT_IN_CATALOG,
    NEW_USAGE,
    SESSION_INDEX_INVALID,
    SESSION_TARGET_ACTIVE,
    SESSION_TARGET_BLOCKED,
    SESSION_USAGE,
    SETTINGS_USAGE,
    WORKING_DIRECTORY,
    panel,
    display_path,
    effort_label,
    fill,
    session_age,
    session_name,
    session_summary,
    setting_summary,
    settings_summary,
    APPROVAL_ACCEPTED,
    APPROVAL_DECLINED,
    APPROVAL_EXPIRED,
    APPROVAL_FAILED,
    ATTACHMENT_QUEUED,
    COMMAND_FAILURE,
    COMMAND_FAILURE_LABELS,
    MEDIA_USAGE,
    ping_channel,
    PING_USAGE,
    RESTART_FAILED,
    RESTART_USAGE,
    STOP_DEFERRED,
    STOP_IDLE,
    STOP_REQUESTED,
    STOP_UNCONFIRMED,
    UNKNOWN_COMMAND,
    status_from_fields,
)
from command_specs import HELP, REGISTRY, COMMANDS
from configuration import processing_template, completion_template


def notice(bridge):
    return processing_template(bridge.reload_config(), bridge.state)


def info(bridge, action="当前会话", *, include_backend=False):
    details = bridge.backend.info
    text = session_summary(action, name=details.get("name"), model=details.get("model"),
                           effort=details.get("effort"), cwd=details.get("cwd") or bridge.backend.cwd,
                           backend=backend_label(bridge.backend_name) if include_backend else None)
    if bridge.backend.blocked:
        return text + "\n" + BLOCKED_NOTE
    if bridge.active_turn() and not bridge.active_turn().get("owned"):
        return text + "\n" + DESKTOP_NOTE
    return text


async def activate(bridge, thread_id=None, overrides=None):
    # Only this explicit command may override native settings. Never replay a mobile cache.
    saved = dict(overrides or {})
    desktop = False
    if thread_id:
        thread = await bridge.backend.read_thread(thread_id)
        status = thread.get("status", {}).get("type")
        if status == "systemError":
            raise UserFacingError(SESSION_TARGET_BLOCKED)
        if status == "active":
            desktop = True
            if not bridge.backend.shared:
                raise UserFacingError(SESSION_TARGET_ACTIVE)
            if overrides:
                raise UserFacingError(BUSY_CHANGE)
            # Adopt the desktop's current settings instead of applying stale mobile overrides.
            saved = {}
    bridge.loaded = False
    selected = await bridge.backend.thread(thread_id, saved)
    bridge.state["thread"] = selected
    bridge.state.pop("sessionOptions", None)  # Retire legacy override replay after successful adoption.
    bridge.loaded = True
    bridge.save()
    return desktop


async def ensure_session(bridge):
    await bridge.ensure_backend()
    if not bridge.loaded:
        had_thread = bool(bridge.state["thread"])
        desktop = await activate(bridge, bridge.state["thread"])
        if desktop:
            action = "已连接桌面会话"
        elif had_thread:
            action = "已恢复会话"
        else:
            action = "已新建会话"
        bridge.reply(info(bridge, action))


async def model_entries(bridge):
    return await bridge.backend.models()


def effort_choices(model):
    return [item["reasoningEffort"] for item in model.get("supportedReasoningEfforts", [])]


async def command_settings(bridge, command, argument, received=None):
    if argument:
        parts = argument.split()
        if len(parts) != 2 or parts[0] != "autostart" or parts[1] not in ("on", "off"):
            bridge.reply(SETTINGS_USAGE)
            return
        try:
            if (parts[1] == "on" and not autostart.IS_WINDOWS
                    and not await autostart.installed()):
                await bridge.enable_background_service()
                return
            enabled = await autostart.set_enabled(parts[1] == "on")
        except UserFacingError:
            raise
        except (ValueError, RuntimeError, OSError, asyncio.TimeoutError):
            raise UserFacingError("自启设置未确认，请在电脑检查本项目的服务安装。") from None
        bridge.reply(f"自启: {'开启' if enabled else '关闭'}")
        return
    try:
        autostart_text = "开启" if await autostart.status() else "关闭"
    except (ValueError, RuntimeError, OSError, asyncio.TimeoutError):
        autostart_text = "不可用"
    bridge.reply(settings_summary(
        notice=notice(bridge),
        completion_notice=completion_template(bridge.reload_config(), bridge.state),
        source_hint=bridge.source_hint,
        attachment_mib=bridge.media.limit // (1024 * 1024),
        slicing=bridge.slicing,
        notifications=bridge.notifications,
        autostart=autostart_text,
    ))
    return


async def command_backend_view(bridge, command, argument, received=None):
    current = backend_argument(bridge.backend_name)
    names = list(bridge.backend_configs)
    available = " | ".join(backend_argument(name) for name in names)
    bridge.reply(fill(BACKEND_SUMMARY, {
        "当前": current,
        "可用": available,
    }))
    return


async def command_notice(bridge, command, argument, received=None):
    label = "接收提示" if command == "/ack" else "结束提示"
    key = "processingNotice" if command == "/ack" else "completionNotice"
    usage = ACK_USAGE.replace("/ack", command)
    if argument == "fields":
        bridge.reply(panel(*(f"{{{name}}}" for name in bridge.prompt_fields())))
        return
    template = (notice(bridge) if command == "/ack" else
                completion_template(bridge.reload_config(), bridge.state))
    if not argument:
        bridge.reply(fill(ACK_VIEW.replace("/ack", command).replace("接收提示", label),
                          {"模板": template or "关闭"}))
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
        raise UserFacingError(fill(ACK_TOO_LONG.replace("接收提示", label), {"字节": MAX_NOTICE_BYTES}))
    bridge.state[key] = value
    bridge.save()
    bridge.reply(panel(label + ": " + (value or "关闭")))
    return


async def command_sessions(bridge, command, argument, received=None):
    if argument == "next":
        raise UserFacingError(f"用法: `{REGISTRY['/sessions'].usage}`")
    page = int(argument) if argument.isdecimal() else 1
    search = bridge.state.get("sessionSearch") if argument.isdecimal() else argument or None
    if page < 1:
        raise UserFacingError("页码必须大于 0")
    cache = bridge.session_catalog
    key = (bridge.backend.connection_id, search)
    if (not argument.isdecimal() or cache is None or cache[0] != key
            or time.monotonic() - cache[1] > 60):
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
                raise UserFacingError("会话列表读取未完成，请重试。")
            cursors.add(cursor)
        bridge.session_catalog = (key, time.monotonic(), catalog)
    else:
        catalog = cache[2]
    total = max(1, (len(catalog) + 9) // 10)
    if page > total:
        raise UserFacingError(f"页码超出范围: 1–{total}")
    threads = catalog[(page - 1) * 10:page * 10]
    bridge.state["sessionChoices"] = [thread["id"] for thread in threads]
    bridge.state.pop("sessionCursor", None)
    bridge.state["sessionSearch"] = search
    bridge.save()
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
        lines.append("\n没有匹配的会话。")
    hints = [f"`{REGISTRY['/switch'].usage}`",
             f"`{REGISTRY['/sessions'].usage}` 当前{page}/{total}页"]
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
        lines.append("暂无可用模型")
    bridge.reply(panel(*lines) + f"\n`{REGISTRY['/model'].usage}`")
    return


async def command_cwd_view(bridge, command, argument, received=None):
    bridge.reply(fill(WORKING_DIRECTORY, {
        "目录": display_path(bridge.backend.info.get("cwd") or bridge.backend.cwd)}))
    return


async def command_session(bridge, command, argument, received=None):
    if command == "/compact" and argument:
        raise UserFacingError("用法: `/compact`")
    if command == "/new" and argument:
        raise UserFacingError(NEW_USAGE)
    if command == "/switch" and not argument:
        raise UserFacingError(SESSION_USAGE)
    # All state-changing session controls use one lock; incoming work waits until done.
    if (bridge.restarting or bridge.running or bridge.steering or bridge.active_turn()
            or bridge.state["inbox"] or bridge.session_lock.locked()):
        raise UserFacingError(BUSY_CHANGE)
    async with bridge.session_lock:
        if command == "/backend":
            await switch(bridge, argument)
            bridge.reply(info(bridge, "已切换后端", include_backend=True))
            return
        if command == "/new":
            await activate(bridge)
            bridge.reply(info(bridge, "已新建会话"))
            return
        if command == "/switch":
            target = argument
            if argument.isdecimal():
                choices = bridge.state.get("sessionChoices", [])
                index = int(argument) - 1
                if not 0 <= index < len(choices):
                    raise UserFacingError(SESSION_INDEX_INVALID)
                target = choices[index]
            desktop = await activate(bridge, target)
            bridge.reply(info(bridge, "已连接桌面会话" if desktop else "已切换会话"))
            return
        await ensure_session(bridge)
        if command == "/compact":
            if bridge.backend.blocked:
                raise UserFacingError("暂不能压缩: 当前会话等待处理。")
            await bridge.backend.compact(bridge.state["thread"])
            bridge.reply("已请求压缩上下文。")
            return
        overrides = {}
        if command in ("/model", "/effort"):
            models = (bridge.model_choices if argument.isdecimal() and bridge.model_choices
                      and command != "/effort" else await model_entries(bridge))
            if command == "/effort":
                current = next((m for m in models
                                if m["model"] == bridge.backend.info.get("model")), None)
                if current is None:
                    raise UserFacingError(MODEL_NOT_IN_CATALOG)
                choices = effort_choices(current)
                if not argument:
                    current_effort = bridge.backend.info.get("effort")
                    bridge.reply(fill(EFFORT_SUMMARY, {
                        "当前": effort_label(current_effort),
                        "可选": " | ".join(effort_label(value) for value in choices) or "无",
                    }))
                    return
                selected_effort = next(
                    (value for value in choices
                     if value.casefold() == argument.casefold()), None)
                if selected_effort is None:
                    raise UserFacingError(fill(EFFORT_INVALID, {
                        "可选": " | ".join(effort_label(value) for value in choices) or "无"}))
                overrides["effort"] = selected_effort
            else:
                selected = None
                if argument.isdecimal():
                    index = int(argument) - 1
                    if 0 <= index < len(models):
                        selected = models[index]
                else:
                    selected = next((m for m in models if m["model"] == argument), None)
                if selected is None:
                    raise UserFacingError(MODEL_INVALID)
                overrides["model"] = selected["model"]
                # Model-specific defaults prevent carrying an unsupported effort to another model.
                overrides["effort"] = selected.get("defaultReasoningEffort")
        elif command == "/cwd":
            base = Path(bridge.backend.info.get("cwd") or bridge.backend.cwd)
            path = Path(argument).expanduser()
            if not path.is_absolute():
                path = base / path
            try:
                path = path.resolve(strict=True)
            except OSError:
                raise UserFacingError(DIRECTORY_INVALID) from None
            if not path.is_dir():
                raise UserFacingError(DIRECTORY_NOT_FOLDER)
            overrides["cwd"] = str(path)
        await activate(bridge, bridge.state["thread"], overrides)
        details = bridge.backend.info
        bridge.reply(setting_summary(model=details.get("model"), effort=details.get("effort"),
                                     cwd=details.get("cwd") or bridge.backend.cwd,
                                     field=command.removeprefix("/")))


async def command_backend(bridge, command, argument, received=None):
    if not argument:
        return await command_backend_view(bridge, command, argument, received)
    return await command_session(bridge, command, argument, received)


async def command_model(bridge, command, argument, received=None):
    if not argument:
        return await command_model_view(bridge, command, argument, received)
    return await command_session(bridge, command, argument, received)


async def command_cwd(bridge, command, argument, received=None):
    if not argument:
        return await command_cwd_view(bridge, command, argument, received)
    return await command_session(bridge, command, argument, received)


async def command_help(bridge, command, argument, received=None):
    bridge.reply(HELP)


async def command_ping(bridge, command, argument, received=None):
    if argument:
        bridge.reply(PING_USAGE)
    else:
        ingress = received.get("ingress_ms") if received else None
        channel = ping_channel(bridge.latency.values(ingress))
        backend = bridge.backend
        connection = backend.connection_id
        provider = await provider_latency(backend)
        if backend is bridge.backend and connection == backend.connection_id:
            bridge.latency.provider_sample = (connection, provider)
        bridge.reply(channel + "─ Bridge ─" + provider + "─ Provider")


async def command_send(bridge, command, argument, received=None):
    try:
        if not argument:
            bridge.reply(MEDIA_USAGE)
            return
        if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in ("'", '"'):
            argument = argument[1:-1]
        await bridge.queue_file(argument, {"/sendfile": "file", "/sendimage": "image"}.get(command, "auto"),
                              origin=(received or {}).get("media"))
        bridge.reply(ATTACHMENT_QUEUED)
    except (MediaError, ValueError, OSError) as error:
        bridge.reply(f"附件未发送: {attachment_reason(error)}\n请检查路径后重试。")


async def command_restart(bridge, command, argument, received=None):
    if argument:
        bridge.reply(RESTART_USAGE)
        return
    try:
        await bridge.restart_service()
    except UserFacingError as error:
        bridge.reply("拒绝重启: " + str(error))
    except (RuntimeError, OSError, asyncio.TimeoutError) as error:
        bridge.reply(fill(RESTART_FAILED, {"原因": backend_reason(error)}))


async def command_status(bridge, command, argument, received=None):
    bridge.reply(status_from_fields(bridge.prompt_fields()))


async def command_stop(bridge, command, argument, received=None):
    bridge.stopping = True
    cleared = len(bridge.state["inbox"])
    cleared_note = f"\n已清空待办: {cleared}" if cleared else ""
    for job in bridge.state["inbox"]:
        if job.get("phase") in (InputPhase.DISPATCHING, InputPhase.ACCEPTED):
            bridge.record_input(job, InputPhase.UNCERTAIN if job["phase"] == InputPhase.DISPATCHING
                              else InputPhase.ACCEPTED)
    bridge.state["inbox"].clear()
    bridge.save()
    try:
        stopped = await bridge.backend.stop()
        if stopped:
            bridge.reply(STOP_REQUESTED + cleared_note)
        elif bridge.running or bridge.active_turn():
            bridge.reply(STOP_DEFERRED + cleared_note)
        else:
            bridge.reply(STOP_IDLE + cleared_note)
    except (RuntimeError, OSError, asyncio.TimeoutError):
        bridge.reply(STOP_UNCONFIRMED + cleared_note)


async def command_approval(bridge, command, argument, received=None):
    try:
        accept = command == "/approve"
        ok = await bridge.backend.answer_approval(
            argument, "accept" if accept else "decline")
        bridge.reply(APPROVAL_ACCEPTED if ok and accept else
                   APPROVAL_DECLINED if ok else APPROVAL_EXPIRED)
    except (RuntimeError, OSError, asyncio.TimeoutError):
        bridge.reply(APPROVAL_FAILED)


async def command_answer(bridge, command, argument, received=None):
    parts = argument.split(maxsplit=1)
    if len(parts) != 2:
        raise UserFacingError(f"用法: `{REGISTRY[command].usage}`")
    try:
        ok = await bridge.backend.answer_question(*parts)
        bridge.reply("已回答。" if ok else "问题已失效或编号无效。")
    except ValueError as error:
        bridge.reply(str(error))
    except (RuntimeError, OSError, asyncio.TimeoutError):
        bridge.reply("回答投递结果未确认，请查看桌面；未自动重发。")


HANDLERS = {spec.name: globals()["command_" + (spec.handler or spec.name.removeprefix("/"))]
            for spec in COMMANDS if spec.route != "input"}


def parse_control(text):
    """Trim only command boundaries; preserve the argument's internal whitespace."""
    parts = str(text or "").strip().split(maxsplit=1)
    return (parts[0], parts[1].strip() if len(parts) > 1 else "") if parts else ("", "")


def validate(spec, argument):
    if (spec.usage == spec.name and argument) or (spec.required and not argument):
        raise UserFacingError(f"用法: `{spec.usage}`")
    if spec.name == "/sessions":
        if argument == "next":
            raise UserFacingError(f"用法: `{spec.usage}`")
        if argument.isdecimal() and int(argument) < 1:
            raise UserFacingError("页码必须大于 0")


async def handle(bridge, command, argument, received=None):
    """Invoke a registered handler; also usable with injected backends in offline tests."""
    spec = REGISTRY.get(command)
    if spec is None or spec.route == "input":
        bridge.reply(UNKNOWN_COMMAND)
        return
    validate(spec, argument)
    await HANDLERS[spec.name](bridge, spec.name, argument, received)


async def dispatch(bridge, text, *, received=None):
    command, argument = parse_control(text)
    spec = REGISTRY.get(command)
    try:
        if spec is None or spec.route == "input":
            bridge.reply(UNKNOWN_COMMAND)
            return
        validate(spec, argument)  # Reject syntax before opening a backend connection.
        if spec.needs_backend and not (spec.query_local and not argument):
            await bridge.ensure_backend()
        await handle(bridge, spec.name, argument, received)
    except UserFacingError as error:
        bridge.reply(str(error))
    except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as error:
        label = COMMAND_FAILURE_LABELS.get(spec.name if spec else command, "操作失败")
        # CodexRpcError already sanitizes server messages and carries the method
        # and code. Preserve those details instead of reducing them to a class name.
        reason = str(error) if isinstance(error, CodexRpcError) else backend_reason(error)
        bridge.reply(fill(COMMAND_FAILURE, {"动作": label, "原因": reason}))
        detail = str(error) if isinstance(error, CodexRpcError) else type(error).__name__
        print(f"{command}失败：{detail}", file=sys.stderr, flush=True)
