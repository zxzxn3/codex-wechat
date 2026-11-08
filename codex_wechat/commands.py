"""WeChat conversation controls; lists come from the current backend."""
from functools import wraps
from .lifecycle import can_change_session
from copy import deepcopy
from pathlib import Path
import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from . import autostart

from .profiles import backend_argument, backend_label, definitions, ensure_backend_config, switch
from .errors import UserFacingError, attachment_reason, backend_reason, writer_conflict
from .codex_transport import CodexRpcError
from .codex_connection import ConnectionSpec
from .backend import WRITER_BUSY, WRITER_NATIVE
from .media import MEDIA_MAX_MIB_MAX, MEDIA_MAX_MIB_MIN, MediaError
from .records import InputPhase
from .latency import provider_latency
from .diagnostics import error_fields, log_event, request_context

from .prompts import (
    MAX_NOTICE_BYTES, COMMAND_FAILURE_LABELS, panel, display_path, effort_label,
    session_age, session_name, session_summary, setting_summary, settings_summary,
    SettingsView,
    ping_channel, status_from_fields, prompt_text, VALUE_EMPTY,
)
from .command_specs import REGISTRY, COMMANDS, command_name, command_usage, help_text
from .configuration import (
    CONNECTION_IDLE_MAX, CONNECTION_IDLE_MIN, HotSettings,
    LOG_KEEP_MAX, LOG_KEEP_MIN,
    REPLY_BUDGET_MAX, REPLY_BUDGET_MIN,
    SOURCE_HINT_LIMIT_BYTES,
    SLICING_TARGET_MAX, SLICING_TARGET_MIN,
    completion_template, processing_template, write_config,
)


def notice(bridge):
    return processing_template(bridge.reload_config(), bridge.state)


def info(bridge, action=None, *, include_backend=False):
    action = bridge.prompt("CURRENT_SESSION") if action is None else action
    details = bridge.backend.info
    status = bridge.prompt_fields()["status"]
    text = session_summary(action, name=details.get("name"), model=details.get("model"),
                           session_id=bridge.state.get("thread"),
                           effort=details.get("effort"), cwd=details.get("cwd") or bridge.backend.cwd,
                           backend=backend_label(
                               bridge.backend_name, bridge.backend_configs) if include_backend else None,
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
        if not writer_conflict(error):
            raise
        return await bridge.backend.adopt_thread(thread_id, thread)


async def _prepare_activation(bridge, thread_id=None, overrides=None):
    """Validate and prepare a target; never touch the current selection."""
    saved = dict(overrides or {})
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
            saved = {}
    prepared = await bridge.backend.prepare_thread(thread_id, saved, thread=thread)
    prepared["desktop"] = desktop
    return prepared


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
    bridge.loaded = True
    bridge.save()
    # Adoption itself needs no writer: return it so the App can open this thread
    # while the bridge is idle. The next turn resumes it or follows the App.
    if bridge.backend.writer_state(selected) in (None, WRITER_NATIVE):
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


def backend_section(config, name):
    """The per-backend definition `definitions()` applies last for this name."""
    backends = config.setdefault("backends", {})
    if not isinstance(backends, dict):
        raise ValueError("backends 必须是对象")
    section = backends.setdefault(name, {})
    if not isinstance(section, dict):
        raise ValueError("backends 的每一项必须是对象")
    return section


@dataclass(frozen=True)
class Setting:
    """One /settings name: where it lives in config.json, plus numeric bounds.

    A single table keeps the write path, the read-back and the limits from drifting
    apart; `numeric` replaces the old second table that only carried bounds.
    """

    path: tuple[str, ...]
    minimum: int | None = None
    maximum: int | None = None

    @property
    def numeric(self):
        return self.minimum is not None


SETTINGS = {
    "compactionNotice": Setting(("compactionNotice",)),
    "sourceHint": Setting(("sourceHint",)),
    "slicing.smart": Setting(("slicing", "smart")),
    "connectionIdleSeconds": Setting(("connectionIdleSeconds",),
                                      CONNECTION_IDLE_MIN, CONNECTION_IDLE_MAX),
    "media.maxMiB": Setting(("media", "maxMiB"), MEDIA_MAX_MIB_MIN, MEDIA_MAX_MIB_MAX),
    "slicing.targetChars": Setting(("slicing", "targetChars"),
                                   SLICING_TARGET_MIN, SLICING_TARGET_MAX),
    "logs.keep": Setting(("logs", "keep"), LOG_KEEP_MIN, LOG_KEEP_MAX),
    # Not advertised in the panel or in /help settings: an operator who knows the
    # name may raise it past the observed platform ceiling on purpose.
    "budgets": Setting(("sending", "replyBudget"), REPLY_BUDGET_MIN, REPLY_BUDGET_MAX),
    "sending.replyBudget": Setting(("sending", "replyBudget"),
                                   REPLY_BUDGET_MIN, REPLY_BUDGET_MAX),
}


def numeric_setting_value(bridge, name, value):
    setting = SETTINGS[name]
    minimum, maximum = setting.minimum, setting.maximum
    if not value.isascii() or not value.isdecimal():
        raise UserFacingError(bridge.prompt("SETTINGS_NUMBER", {
            "name": name, "minimum": minimum, "maximum": maximum}))
    number = int(value)
    if not minimum <= number <= maximum:
        raise UserFacingError(bridge.prompt("SETTINGS_NUMBER", {
            "name": name, "minimum": minimum, "maximum": maximum}))
    return number


async def write_hot_setting(bridge, name, value):
    path = bridge.settings.path
    try:
        if path is None:
            raise OSError("没有可写入的配置文件")
        config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        if not isinstance(config, dict):
            raise ValueError("配置必须是 JSON 对象")
        section = config
        for part in SETTINGS[name].path[:-1]:
            child = section.get(part)
            if child is None:
                child = {}
                section[part] = child
            if not isinstance(child, dict):
                raise ValueError(f"{part} 必须是对象")
            section = child
        section[SETTINGS[name].path[-1]] = value
        HotSettings.from_config(config)
        write_config(path, config)
    except (OSError, ValueError):
        raise UserFacingError(bridge.prompt("SETTINGS_WRITE_FAILED")) from None
    bridge.reload_config()


def text_setting_value(bridge, argument, max_bytes, too_long_key):
    value = "" if argument in ('""', "''") else argument
    if len(value.encode()) > max_bytes:
        raise UserFacingError(bridge.prompt(too_long_key, {"max_bytes": max_bytes}))
    return value


def current_numeric_setting(bridge, name):
    """Read back what the reload applied, derived from SETTINGS itself.

    Adding a hot numeric setting therefore needs no entry here: the camelCase
    config path is walked over the hot settings object. media.maxMiB is the one
    runtime value that is not a hot setting, because Media keeps its own limit.
    """
    if name == "media.maxMiB":
        return bridge.media.limit // (1024 * 1024)
    node = bridge.settings.hot
    for part in SETTINGS[name].path:
        attribute = re.sub(r"(?<!^)(?=[A-Z])", "_", part).lower()
        node = getattr(node, attribute)
    return node


async def set_numeric_setting(bridge, name, value):
    number = numeric_setting_value(bridge, name, value)
    await write_hot_setting(bridge, name, number)
    if name == "media.maxMiB":
        bridge.media.limit = number * 1024 * 1024
    if current_numeric_setting(bridge, name) != number:
        raise UserFacingError(bridge.prompt("SETTINGS_NOT_APPLIED"))
    bridge.reply(panel(f"{name}: {number}"))


async def set_compaction_notice(bridge, value):
    if value not in ("true", "false"):
        bridge.reply(bridge.prompt("SETTINGS_USAGE"))
        return
    enabled = value == "true"
    await write_hot_setting(bridge, "compactionNotice", enabled)
    if bridge.notifications.compaction != enabled:
        raise UserFacingError(bridge.prompt("SETTINGS_NOT_APPLIED"))
    bridge.reply(panel(f"compactionNotice: {value}"))


async def set_slicing_smart(bridge, value):
    if value not in ("true", "false"):
        bridge.reply(bridge.prompt("SETTINGS_USAGE"))
        return
    mode = "smart" if value == "true" else "length"
    await write_hot_setting(bridge, "slicing.smart", value == "true")
    if bridge.slicing.mode != mode:
        raise UserFacingError(bridge.prompt("SETTINGS_NOT_APPLIED"))
    bridge.reply(panel(f"slicing.smart: {value}"))


async def set_source_hint(bridge, argument):
    if not argument:
        bridge.reply(panel(f"sourceHint: {bridge.source_hint or VALUE_EMPTY}"))
        return
    value = text_setting_value(
        bridge, argument, SOURCE_HINT_LIMIT_BYTES, "SOURCE_HINT_TOO_LONG")
    await write_hot_setting(bridge, "sourceHint", value)
    if bridge.source_hint != value:
        raise UserFacingError(bridge.prompt("SETTINGS_NOT_APPLIED"))
    bridge.reply(panel(f"sourceHint: {value or VALUE_EMPTY}"))


async def set_shared_daemon(bridge, enabled):
    """Apply the shared-daemon choice, then persist it only once it works.

    Nothing is written until the reconnect succeeds, so a failed switch leaves
    the file and the running mode agreeing with each other. The value goes to
    `backends[name]`; `codex-desktop setup` writes there, so a change stored
    anywhere else would be silently overridden on the next start.
    """
    path = bridge.settings.path
    name = bridge.backend_name
    if not can_change_session(bridge):
        raise UserFacingError(bridge.prompt("BUSY_CHANGE"))
    # This one still needs a fully idle bridge: switching mode closes the
    # connection and restarts the backend, so a live turn cannot be left running.
    require_idle(bridge)
    try:
        if path is None:
            raise OSError("没有可写入的配置文件")
        config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        if not isinstance(config, dict):
            raise ValueError("配置必须是 JSON 对象")
        section = backend_section(config, name)
        running = backend_section(bridge.config, name)
    except (OSError, ValueError):
        raise UserFacingError(bridge.prompt("SETTINGS_SHARED_FAILED")) from None
    section["sharedDaemon"] = enabled
    previous_value = running.get("sharedDaemon")
    running["sharedDaemon"] = enabled
    bridge.backend_configs = definitions(bridge.config)
    previous = bridge.backend
    async with bridge.session_lock:
        if not enabled:
            # A daemon keeps every thread it loaded locked, so hand this one back.
            await previous.release_thread(bridge.state.get("thread"))
        await previous.close()
        bridge.backend_started = False
        try:
            await switch(bridge, name, force=True, adopt_busy=True)
        except BaseException:
            if previous_value is None:
                running.pop("sharedDaemon", None)
            else:
                running["sharedDaemon"] = previous_value
            bridge.backend_configs = definitions(bridge.config)
            raise
    values = {"value": "true" if bridge.backend.shared else "false"}
    text = bridge.prompt("SETTINGS_SHARED", values)
    try:
        write_config(path, config)
    except OSError:
        text += "\n" + bridge.prompt("SETTINGS_SHARED_UNSAVED")
    if bridge.backend.shared_fallback:
        text += "\n" + bridge.prompt("SETTINGS_SHARED_FALLBACK",
                                     {"reason": bridge.backend.shared_fallback})
    if bridge.backend.writer_state(bridge.state.get("thread")) == WRITER_BUSY:
        text += "\n" + bridge.prompt("SETTINGS_SHARED_BUSY")
    bridge.reply(text)


def settings_view(bridge, *, autostart_text=None):
    """Collect what the panel shows; adding a field touches only this function."""
    return SettingsView(
        notice=notice(bridge),
        completion_notice=completion_template(bridge.reload_config(), bridge.state),
        source_hint=bridge.source_hint,
        log_keep=bridge.settings.hot.logs.keep,
        attachment_mib=bridge.media.limit // (1024 * 1024),
        slicing=bridge.slicing,
        notifications=bridge.notifications,
        autostart=autostart_text,
        shared="true" if bridge.backend.shared else "false",
        connection_idle_seconds=bridge.settings.hot.connection_idle_seconds,
    )


async def command_settings(bridge, command, argument, received=None):
    if argument:
        parts = argument.split(maxsplit=1)
        name = parts[0]
        value = parts[1].strip() if len(parts) > 1 else ""
        if name in ("ack", "finish"):
            await command_notice(bridge, "/" + name, value)
            return
        if name in SETTINGS and SETTINGS[name].numeric:
            if not value:
                bridge.reply(bridge.prompt("SETTINGS_USAGE"))
                return
            await set_numeric_setting(bridge, name, value)
            return
        if name == "compactionNotice":
            await set_compaction_notice(bridge, value)
            return
        if name == "slicing.smart":
            await set_slicing_smart(bridge, value)
            return
        if name == "sourceHint":
            await set_source_hint(bridge, value)
            return
        if name not in ("autostart", "shared") or value not in ("true", "false"):
            bridge.reply(bridge.prompt("SETTINGS_USAGE"))
            return
        if name == "shared":
            await set_shared_daemon(bridge, value == "true")
            return
        try:
            enabled = await autostart.set_enabled(value == "true")
        except UserFacingError:
            raise
        except (TimeoutError, ValueError, RuntimeError, OSError):
            raise UserFacingError(bridge.prompt("AUTOSTART_FAILED")) from None
        bridge.reply(bridge.prompt("SETTINGS_AUTOSTART", {
            "value": "true" if enabled else "false"}))
        return
    try:
        autostart_text = "true" if await autostart.status() else "false"
    except (TimeoutError, ValueError, RuntimeError, OSError):
        autostart_text = "unknown"
    summary = settings_summary(settings_view(bridge, autostart_text=autostart_text),
                               text=bridge.prompt)
    bridge.reply(summary)
    return


async def command_backend_view(bridge, command, argument, received=None):
    current = backend_label(bridge.backend_name, bridge.backend_configs)
    names = list(bridge.backend_configs)
    available = " | ".join(backend_argument(name) for name in names)
    bridge.reply(bridge.prompt("BACKEND_SUMMARY", {
        "current": current,
        "available": available,
        "hint": command_usage("/backend"),
    }))
    return


def reload_backend_definitions(bridge):
    """Refresh backend definitions from the persisted config before switching."""
    path = bridge.settings.path
    if path is None:
        return
    try:
        fresh = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(fresh, dict):
            raise ValueError("配置必须是对象")
        candidate = dict(bridge.config)
        if "backends" in fresh:
            candidate["backends"] = fresh["backends"]
        else:
            candidate.pop("backends", None)
        ensure_backend_config(candidate)
        backends = definitions(candidate)
        for value in backends.values():
            ConnectionSpec.from_config(value)
    except (OSError, ValueError):
        raise UserFacingError(bridge.prompt("CONFIG_RELOAD_FAILED")) from None
    if "backends" in candidate:
        bridge.config["backends"] = candidate["backends"]
    else:
        bridge.config.pop("backends", None)
    bridge.backend_configs = backends


async def command_notice(bridge, command, argument, received=None):
    label = "ack" if command == "/ack" else "finish"
    key = "processingNotice" if command == "/ack" else "completionNotice"
    prefix = "ACK" if command == "/ack" else "FINISH"
    if argument == "fields":
        bridge.reply(panel(*(f"{{{name}}}" for name in bridge.prompt_fields())))
        return
    template = (notice(bridge) if command == "/ack" else
                completion_template(bridge.reload_config(), bridge.state))
    if not argument:
        bridge.reply(bridge.prompt(prefix + "_VIEW", {"template": template or VALUE_EMPTY}))
        return
    value = text_setting_value(
        bridge, argument, MAX_NOTICE_BYTES, prefix + "_TOO_LONG")
    bridge.state[key] = value
    bridge.save()
    bridge.reply(panel(label + ": " + (value or VALUE_EMPTY)))
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


def _session_choices(bridge, matches, query, *, id_prefix=False):
    shown = matches[:10]
    bridge.state["sessionChoices"] = [thread["id"] for thread in shown]
    bridge.state["sessionSearch"] = query
    bridge.save()
    text = panel(*_session_lines(bridge, shown)) + "\n" + bridge.prompt(
        "SESSION_SEARCH_MULTIPLE", {"count": len(matches)})
    if len(matches) > len(shown):
        text += "\n" + (bridge.prompt("SESSION_ID_PREFIX_MORE", {
            "count": len(matches), "shown": len(shown)})
            if id_prefix else bridge.prompt("SESSION_SEARCH_MORE", {
                "count": len(shown), "query": query}))
    bridge.reply(text)


def _id_prefix(value):
    text = str(value or "").strip().lower()
    compact = text.replace("-", "")
    if not 4 <= len(compact) <= 32:
        return None
    if not all(char in "0123456789abcdef" for char in compact):
        return None
    return text


async def _id_prefix_matches(bridge, prefix):
    # searchTerm filters titles only; enumerate the bounded thread list locally.
    catalog = await _session_catalog(bridge, None)
    compact = prefix.replace("-", "")
    matches = {}
    for thread in catalog:
        identity = str(thread.get("id") or "").lower()
        if identity.startswith(prefix) or identity.replace("-", "").startswith(compact):
            matches[identity] = thread
    return list(matches.values())


async def _search_session_target(bridge, argument):
    matches = await _session_catalog(bridge, argument)
    exact = next((thread for thread in matches
                  if thread["id"] == argument), None)
    if exact is not None:
        return exact["id"]
    if len(matches) == 1:
        return matches[0]["id"]
    if not matches:
        raise UserFacingError(bridge.prompt(
            "SESSION_SEARCH_EMPTY", {"query": argument}))
    _session_choices(bridge, matches, argument)
    return None


async def command_sessions(bridge, command, argument, received=None):
    if argument == "next":
        raise UserFacingError(bridge.prompt("COMMAND_USAGE", {
            "usage": command_usage("/sessions")}))
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
    hints = [command_usage("/switch"),
             command_usage("/sessions") + " " +
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
    bridge.reply(panel(*lines) + "\n" + command_usage("/model"))
    return


async def command_cwd_view(bridge, command, argument, received=None):
    bridge.reply(bridge.prompt("WORKING_DIRECTORY", {
        "cwd": display_path(bridge.backend.info.get("cwd") or bridge.backend.cwd)}))
    return


def session_control(handler):
    """Serialize session mutations; a live turn is discarded by each handler."""
    @wraps(handler)
    async def guarded(bridge, command, argument, received=None):
        if command == "/compact" and argument:
            raise UserFacingError(bridge.prompt("COMMAND_USAGE", {
                "usage": command_usage("/compact")}))
        if command == "/new" and argument:
            raise UserFacingError(bridge.prompt("NEW_USAGE", {
                "usage": command_usage("/new")}))
        if command == "/switch" and not argument:
            raise UserFacingError(bridge.prompt("SESSION_USAGE", {
                "usage": command_usage("/switch")}))
        if not can_change_session(bridge):
            raise UserFacingError(bridge.prompt("BUSY_CHANGE"))
        async with bridge.session_lock:
            return await handler(bridge, command, argument, received)
    return guarded


def _has_switchable_work(bridge):
    return bool(bridge.running or bridge.active_turn()
                or bridge.state.get("currentInput") or bridge.state["inbox"])


def require_idle(bridge):
    if _has_switchable_work(bridge):
        raise UserFacingError(bridge.prompt("BUSY_CHANGE"))


def _switch_suffix(bridge, discarded):
    return "\n" + bridge.prompt("SWITCH_DISCARDED") if discarded else ""


async def _replace_target(bridge, prepared):
    """Commit a prepared target, or leave the old state untouched on failure."""
    backend_name = bridge.backend_name
    thread_id = bridge.state.get("thread")
    backend_state = bridge.backend.selection_snapshot()
    reason = "switch"
    try:
        selected = await bridge.backend.commit_prepared(
            prepared, preserve_active=True, defer_cleanup=True)
        # The worker keeps the job objects that are live right now; the discard
        # below only marks their staged copies, so remember the originals.
        live_jobs = [bridge.state.get("currentInput"), *bridge.state.get("inbox", [])]
        staged = deepcopy(bridge.state)
        inputs = bridge.discard_pending_inputs(
            reason, state=staged, persist=False)
        outputs = bridge.discard_session_output(
            backend_name, thread_id, reason=reason, state=staged, persist=False)
        staged["thread"] = selected
        bridge.save(staged)
    except BaseException:
        bridge.backend.restore_selection(backend_state)
        await bridge.backend.abandon_prepared(prepared)
        raise
    active = bool(bridge.backend.active)
    bridge.state.clear()
    bridge.state.update(staged)
    bridge.loaded = True
    # Carry the discard into the objects the worker holds, or its requeue and
    # failure paths would treat a discarded job as live work for the new target.
    for job in live_jobs:
        if isinstance(job, dict):
            job["discarded"] = reason
    bridge.output.sync_drained()
    if bridge.backend.active:
        await bridge.backend.detach_active()
    await bridge.backend.finish_prepared(prepared)
    if thread_id:
        await bridge.backend.release_thread(thread_id)
    selected = bridge.state["thread"]
    if bridge.backend.writer_state(selected) in (None, WRITER_NATIVE):
        await bridge.backend.release_thread(selected, follow=True)
    bridge.running = False
    return bool(inputs or outputs or active)


@session_control
async def _change_backend(bridge, command, argument, received=None):
    require_idle(bridge)
    previous = bridge.backend_configs.get(bridge.backend_name)
    reload_backend_definitions(bridge)
    target = next((name for name in bridge.backend_configs
                   if name.casefold() == argument.casefold()), None)
    force = target == bridge.backend_name and bridge.backend_configs.get(target) != previous
    await switch(bridge, argument, force=force)
    bridge.reply(info(bridge, bridge.prompt("BACKEND_SWITCHED"), include_backend=True))
    return


@session_control
async def command_new(bridge, command, argument, received=None):
    prepared = await _prepare_activation(bridge)
    discarded = await _replace_target(bridge, prepared)
    bridge.reply(info(bridge, bridge.prompt("SESSION_CREATED"))
                 + _switch_suffix(bridge, discarded))
    return


@session_control
async def command_switch(bridge, command, argument, received=None):
    prefix = None if _is_thread_id(argument) else _id_prefix(argument)
    target = None
    if argument.isdecimal():
        choices = bridge.state.get("sessionChoices", [])
        index = int(argument) - 1
        if 0 <= index < len(choices):
            target = choices[index]
        elif prefix is None:
            raise UserFacingError(bridge.prompt("SESSION_INDEX_INVALID"))
        if target is None and prefix is not None:
            matches = await _id_prefix_matches(bridge, prefix)
            if len(matches) == 1:
                target = matches[0]["id"]
            elif matches:
                _session_choices(bridge, matches, argument, id_prefix=True)
                return
    elif _is_thread_id(argument):
        target = argument
    elif prefix is not None:
        matches = await _id_prefix_matches(bridge, prefix)
        if len(matches) == 1:
            target = matches[0]["id"]
        elif matches:
            _session_choices(bridge, matches, argument, id_prefix=True)
            return
    if target is None:
        if prefix is None and argument.isdecimal():
            raise UserFacingError(bridge.prompt("SESSION_INDEX_INVALID"))
        target = await _search_session_target(bridge, argument)
        if target is None:
            return
    if target == bridge.state.get("thread"):
        bridge.reply(info(bridge, bridge.prompt("CURRENT_SESSION")))
        return
    prepared = await _prepare_activation(bridge, target)
    discarded = await _replace_target(bridge, prepared)
    desktop = bool(prepared.get("desktop"))
    bridge.reply(info(bridge, bridge.prompt("SESSION_DESKTOP") if desktop
                      else bridge.prompt("SESSION_SWITCHED"))
                 + _switch_suffix(bridge, discarded))
    return


@session_control
async def command_compact(bridge, command, argument, received=None):
    require_idle(bridge)
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
        raise UserFacingError(bridge.prompt("MODEL_INVALID", {
            "command": command_name("/model")}))
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
        "hint": command_usage("/effort"),
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
    active = bridge.active_turn()
    if active and not active.get("owned"):
        raise UserFacingError(bridge.prompt("BUSY_CHANGE"))
    await bridge.backend.update_settings(bridge.state["thread"], **overrides)
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
        reload_backend_definitions(bridge)
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
    if argument not in ("", "all", "settings"):
        bridge.reply(bridge.prompt("HELP_USAGE", {
            "usage": command_usage("/help")}))
        return
    if argument == "settings":
        bridge.reply(bridge.prompt("SETTINGS_HELP"))
        return
    bridge.reply(help_text(show_all=argument == "all", text=bridge.prompt))


async def command_ping(bridge, command, argument, received=None):
    if argument:
        bridge.reply(bridge.prompt("PING_USAGE", {
            "usage": command_usage("/ping")}))
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


async def command_get(bridge, command, argument, received=None):
    try:
        if not argument:
            bridge.reply(bridge.prompt("MEDIA_USAGE", {
                "get": command_usage("/get"),
                "getfile": command_usage("/getfile"),
            }))
            return
        if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in ("'", '"'):
            argument = argument[1:-1]
        origin = (received or {}).get("media")
        # Snapshot first: a missing or oversized file must answer with the reason
        # instead of a receipt. Then acknowledge before the upload starts, so the
        # text does not queue behind a slow attachment.
        asset = await bridge.prepare_asset(
            argument, {"/getfile": "file"}.get(command, "auto"), origin=origin)
        bridge.reply(bridge.prompt("ATTACHMENT_QUEUED"))
        bridge.queue_asset(asset, origin=origin)
    except (MediaError, ValueError, OSError) as error:
        # The path goes into a code block so WeChat can copy it; a stray fence or
        # newline in the echoed text would end the block early.
        echoed = " ".join(argument[:300].split()).replace("```", "'''")
        bridge.reply(bridge.prompt("SEND_FILE_FAILED", {
            "reason": attachment_reason(error), "path": echoed}))


async def command_continue(bridge, command, argument, received=None):
    """Every inbound message refreshes the budget; this command only exists as the
    documented way to ask for more, so it answers nothing and sends nothing."""
    return None


async def command_restart(bridge, command, argument, received=None):
    if argument:
        bridge.reply(bridge.prompt("RESTART_USAGE", {
            "usage": command_usage("/restart")}))
        return
    try:
        bridge.mark_control_restarting((received or {}).get("controlId"))
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
                "count": count, "unit": "条"}))
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


async def command_discard(bridge, command, argument, received=None):
    count = bridge.discard_outbox()
    bridge.resume_delivery()
    bridge.reply(bridge.prompt("DISCARDED", {"count": count}))


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
        raise UserFacingError(bridge.prompt("COMMAND_USAGE", {
            "usage": command_usage(command)}))
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
    "discard": command_discard,
    "ping": command_ping,
    "switch": command_switch,
    "backend": command_backend,
    "model": command_model,
    "effort": command_effort,
    "cwd": command_cwd,
    "approval": command_approval,
    "answer": command_answer,
    "get": command_get,
    "restart": command_restart,
    "continue": command_continue,
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
        raise UserFacingError(text("COMMAND_USAGE", {
            "usage": command_usage(spec.name)}))
    if spec.name == "/sessions":
        if argument == "next":
            raise UserFacingError(text("COMMAND_USAGE", {
                "usage": command_usage(spec.name)}))
        if argument.isdecimal() and int(argument) < 1:
            raise UserFacingError(text("PAGE_INVALID"))


async def handle(bridge, command, argument, received=None):
    """Invoke a registered handler; also usable with injected backends in offline tests."""
    spec = REGISTRY.get(command)
    if spec is None or spec.route == "input":
        bridge.reply(bridge.prompt("UNKNOWN_COMMAND", {
            "help": command_name("/help")}))
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
                bridge.reply(bridge.prompt("UNKNOWN_COMMAND", {
                    "help": command_name("/help")}))
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
        finally:
            if spec and spec.defers_resume:
                bridge.resume_deferred_delivery()
