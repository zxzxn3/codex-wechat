"""WeChat conversation controls; lists come from the current backend."""
from pathlib import Path

from profiles import backend_argument, backend_label, switch
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
    HELP,
    MAX_NOTICE_BYTES,
    MODEL_INVALID,
    MODEL_NOT_IN_CATALOG,
    MODEL_SUMMARY,
    MODEL_TABLE_HEADER,
    NEW_USAGE,
    NO_NEXT_PAGE,
    SESSION_INDEX_INVALID,
    SESSION_TARGET_ACTIVE,
    SESSION_TARGET_BLOCKED,
    SESSION_USAGE,
    SESSIONS_TABLE_HEADER,
    SETTINGS_USAGE,
    UserFacingError,
    WORKING_DIRECTORY,
    effort_label,
    fill,
    processing_template,
    session_age,
    session_name,
    session_summary,
    setting_summary,
    settings_summary,
)

MANAGED = {"/settings", "/backend", "/sessions", "/switch", "/new", "/model", "/models",
           "/effort", "/cwd", "/ack"}


def notice(bridge):
    return processing_template(bridge.config, bridge.state)


def info(bridge, action="当前会话", *, include_session=True):
    details = bridge.backend.info
    values = {
        "action": action,
        "name": details.get("name"),
        "model": details.get("model"),
        "effort": details.get("effort"),
        "cwd": details.get("cwd") or bridge.backend.cwd,
    }
    if include_session:
        text = session_summary(values["action"], name=values["name"],
                               model=values["model"], effort=values["effort"],
                               cwd=values["cwd"])
    else:
        text = setting_summary(values["action"], model=values["model"],
                               effort=values["effort"], cwd=values["cwd"])
    if bridge.backend.blocked:
        return text + "\n\n" + BLOCKED_NOTE
    if bridge.active_turn() and not bridge.active_turn().get("owned"):
        return text + "\n\n" + DESKTOP_NOTE
    return text


async def activate(bridge, thread_id=None, overrides=None):
    # Persist overrides by thread, never let one session's settings leak to another.
    saved = dict(bridge.state.get("sessionOptions", {}).get(thread_id, {}))
    saved.update(overrides or {})
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
    selected = await bridge.backend.thread(thread_id, saved)
    bridge.state["thread"] = selected
    if saved:
        bridge.state.setdefault("sessionOptions", {})[selected] = saved
    elif thread_id:
        bridge.state.setdefault("sessionOptions", {}).pop(selected, None)
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
        bridge.resuming = True


async def model_entries(bridge):
    return await bridge.backend.models()


def effort_choices(model):
    return [item["reasoningEffort"] for item in model.get("supportedReasoningEfforts", [])]


async def handle(bridge, command, argument):
    if command == "/settings":
        if argument:
            bridge.reply(SETTINGS_USAGE)
            return
        bridge.reply(settings_summary(
            notice=notice(bridge),
            attachment_mib=bridge.media.limit // (1024 * 1024),
        ))
        return
    if command == "/backend" and not argument:
        current = backend_label(bridge.backend_name)
        names = list(bridge.backend_configs)
        available = " | ".join(backend_argument(name) for name in names)
        target = next((name for name in names if name != bridge.backend_name),
                      bridge.backend_name)
        bridge.reply(fill(BACKEND_SUMMARY, {
            "当前": current,
            "可用": available,
            "目标": backend_argument(target),
        }))
        return
    if command == "/ack":
        template = notice(bridge)
        if not argument:
            bridge.reply(fill(ACK_VIEW, {"模板": template or "关闭"}))
            return
        if argument == "text":
            value = ""
        elif argument.startswith("text "):
            value = argument[5:].strip()
            if value in ('""', "''"):
                value = ""
        else:
            raise UserFacingError(ACK_USAGE)
        if len(value.encode()) > MAX_NOTICE_BYTES:
            raise UserFacingError(fill(ACK_TOO_LONG, {"字节": MAX_NOTICE_BYTES}))
        bridge.state["processingNotice"] = value
        bridge.save()
        bridge.reply("处理提示：" + (value or "关闭"))
        return
    if command == "/sessions":
        cursor, search = None, argument or None
        if argument == "next":
            cursor = bridge.state.get("sessionCursor")
            search = bridge.state.get("sessionSearch")
            if not cursor:
                bridge.reply(NO_NEXT_PAGE)
                return
        result = await bridge.backend.list_threads(cursor, search)
        threads = result["data"]
        bridge.state["sessionChoices"] = [thread["id"] for thread in threads]
        bridge.state["sessionCursor"] = result.get("nextCursor")
        bridge.state["sessionSearch"] = search
        bridge.save()
        lines = ["会话", "", SESSIONS_TABLE_HEADER]
        for index, thread in enumerate(threads, 1):
            title = session_name(thread.get("name"))
            current = thread["id"] == bridge.state["thread"]
            status = thread.get("status", {}).get("type")
            labels = []
            if current:
                labels.append("当前")
            elif status == "active":
                labels.append("工作中")
            elif status == "systemError":
                labels.append("需要处理")
            if current or not labels:
                age = session_age(thread.get("updatedAt"))
                if age:
                    labels.append(age)
            title = title.replace("|", "\\|")
            state = "  ".join(labels) or "无"
            lines.append(f"| {index} | {title} | {state} |")
        if not threads:
            lines.append("\n没有匹配的会话。")
        lines.extend(["", "切换：`/switch {编号或ID}`", "搜索：`/sessions {关键词}`"])
        if result.get("nextCursor"):
            lines.append("下一页：`/sessions next`")
        bridge.reply("\n".join(lines))
        return
    if command in ("/model", "/models") and not argument:
        models = await model_entries(bridge)
        bridge.model_choices = models
        lines = [MODEL_TABLE_HEADER]
        for index, model in enumerate(models, 1):
            efforts = "  ".join(effort_label(value) for value in effort_choices(model))
            model_name = model["model"].replace("|", "\\|")
            lines.append(f"| {index} | {model_name} | {efforts or '无可选强度'} |")
        bridge.reply(fill(MODEL_SUMMARY, {
            "列表": "\n".join(lines) if models else "当前后端没有返回模型。",
        }))
        return
    if command == "/cwd" and not argument:
        bridge.reply(fill(WORKING_DIRECTORY, {
            "目录": bridge.backend.info.get("cwd") or bridge.backend.cwd}))
        return
    if command == "/new" and argument:
        raise UserFacingError(NEW_USAGE)
    if command == "/switch" and not argument:
        raise UserFacingError(SESSION_USAGE)
    # All state-changing session controls use one lock; incoming work waits until done.
    if (bridge.running or bridge.steering or bridge.active_turn()
            or bridge.state["inbox"] or bridge.session_lock.locked()):
        raise UserFacingError(BUSY_CHANGE)
    async with bridge.session_lock:
        if command == "/backend":
            await switch(bridge, argument)
            bridge.reply(info(bridge, f"已切换后端到 {backend_label(bridge.backend_name)}"))
            return
        if command == "/new":
            await activate(bridge)
            bridge.resuming = True
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
        overrides = {}
        if command in ("/model", "/models", "/effort"):
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
                    target = next((value for value in choices
                                   if value != current_effort), current_effort)
                    bridge.reply(fill(EFFORT_SUMMARY, {
                        "当前": effort_label(current_effort),
                        "可选": " | ".join(effort_label(value) for value in choices) or "无",
                        "目标": effort_label(target),
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
        bridge.reply(info(bridge, f"设置已更新 {backend_label(bridge.backend_name)}",
                          include_session=False))
