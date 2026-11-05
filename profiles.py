"""Backend definitions and per-backend session state, separate from WeChat state."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
from platform_support import IS_WINDOWS
from errors import UserFacingError, writer_conflict
from codex_transport import CodexRpcError

SESSION_KEYS = ("thread", "sessionChoices", "sessionCursor", "sessionSearch")


def default_backends():
    """Load the install-time backend definitions; runtime never synthesizes them."""
    path = Path(__file__).resolve().with_name("config.default.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    backends = data.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise ValueError("config.default.json 缺少 backends")
    platform = "windows" if IS_WINDOWS else "linux"
    result = {}
    for name, settings in backends.items():
        if not isinstance(settings, dict):
            raise ValueError("config.default.json 的 backends 值必须是对象")
        platforms = settings.get("platforms")
        if platforms is not None:
            if (not isinstance(platforms, list) or
                    any(not isinstance(value, str) or not value for value in platforms)):
                raise ValueError("config.default.json 的 platforms 必须是非空字符串数组")
            if platform not in platforms:
                continue
            settings = {key: value for key, value in settings.items() if key != "platforms"}
        result[name] = deepcopy(settings)
    if not result:
        raise ValueError(f"config.default.json 在 {platform} 没有可用 backend")
    return result


def _merge_known_defaults(backends, defaults):
    for name, settings in list(backends.items()):
        if name in defaults and isinstance(settings, dict):
            backends[name] = {**defaults[name], **settings}
    return backends


def definitions(config):
    backends = config.get("backends")
    if not backends:
        backends = default_backends()
    if not isinstance(backends, dict) or not backends:
        raise ValueError("backends 必须是非空对象")
    result = _merge_known_defaults(deepcopy(backends), default_backends())
    for name, settings in result.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(settings, dict):
            raise ValueError("backends 必须使用非空字符串键和对象值")
        if "command" not in settings and not settings.get("websocketUrl"):
            raise ValueError(f"backends.{name}.command 必须配置")
        label = settings.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ValueError(f"backends.{name}.label 必须是非空字符串")
    return result


def ensure_backend_config(config):
    """Seed a missing backend section once, preserving explicit settings."""
    if config.get("backends"):
        current = config["backends"]
        if not isinstance(current, dict):
            return False
        merged = _merge_known_defaults(deepcopy(current), default_backends())
        if merged != current:
            config["backends"] = merged
            definitions(config)
            return True
        definitions(config)
        return False
    config["backends"] = default_backends()
    definitions(config)
    return True


def snapshot(state):
    return {key: deepcopy(state[key]) for key in SESSION_KEYS if key in state}


def restore(state, saved):
    for key in SESSION_KEYS:
        state.pop(key, None)
    state.update({key: deepcopy(saved[key]) for key in SESSION_KEYS if key in saved})
    state.setdefault("thread", None)


def backend_label(name, backends=None):
    settings = (backends or {}).get(name, {})
    label = settings.get("label") if isinstance(settings, dict) else None
    return str(label).strip() if isinstance(label, str) and label.strip() else str(name)


def backend_argument(name):
    return str(name).lower()


async def resume_or_adopt(candidate, thread_id, adopt_busy, *, wait=5.0):
    """Take the writer for the saved thread, or adopt it without one.

    A daemon that was just unsubscribed can keep the writer for a moment, so a
    mode change retries briefly before falling back to a writer-free adoption;
    the next inbound message takes the writer once it is free.
    """
    deadline = time.monotonic() + wait if adopt_busy else 0
    while True:
        try:
            return await candidate.thread(thread_id)
        except CodexRpcError as error:
            if not (adopt_busy and thread_id and writer_conflict(error)):
                raise
            if time.monotonic() >= deadline:
                return await candidate.adopt_thread(thread_id)
            await asyncio.sleep(0.5)


async def switch(bridge, name, *, force=False, adopt_busy=False):
    resolved = next((key for key in bridge.backend_configs
                     if key.casefold() == str(name).casefold()), None)
    if resolved is None:
        available = " | ".join(backend_argument(key) for key in bridge.backend_configs)
        raise UserFacingError(bridge.prompt("BACKEND_INVALID", {"available": available}))
    name = resolved
    if (not force and name == bridge.backend_name and bridge.backend_started
            and bridge.loaded and bridge.backend.connected):
        return
    saved = (snapshot(bridge.state) if name == bridge.backend_name else
             deepcopy(bridge.state.get("backendSessions", {}).get(name, {})))
    candidate = bridge.make_backend(name)
    thread_id = saved.get("thread")
    try:
        await candidate.start()
        if thread_id:
            thread = await candidate.read_thread(thread_id)
            status = thread.get("status", {}).get("type")
            if status == "systemError" or (not adopt_busy and status == "active"
                                           and not candidate.shared):
                raise UserFacingError(bridge.prompt("BACKEND_SESSION_BUSY"))
        selected = await resume_or_adopt(candidate, thread_id, adopt_busy)
    except BaseException:
        await candidate.close()
        raise
    # Commit only after the target is ready. Failure above leaves the current backend intact.
    previous = bridge.backend
    bridge.state.setdefault("backendSessions", {})[bridge.backend_name] = snapshot(bridge.state)
    restore(bridge.state, saved)
    bridge.state["thread"] = selected
    bridge.backend_name = name
    bridge.state["backendName"] = name
    bridge.backend = candidate
    bridge.backend_started = True
    bridge.loaded = True
    bridge.input_paused = False
    bridge.model_choices = []
    bridge.save()
    try:
        await previous.close()
    except Exception:
        bridge.reply(bridge.prompt("BACKEND_CLEANUP_FAILED"))
    # The new backend only needs the writer while a turn runs.
    await candidate.release_thread(selected, follow=True)
