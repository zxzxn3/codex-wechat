"""Backend definitions and per-backend session state, separate from WeChat state."""
from copy import deepcopy
from prompts import (
    BACKEND_CLEANUP_FAILED,
    BACKEND_INVALID,
    BACKEND_SESSION_BUSY,
    UserFacingError,
    fill,
)

# An empty codex definition uses CodexBackend's existing default command.
DEFAULT_BACKENDS = {"codex": {}, "deepseek": {"command": ["codex-deepseek", "app-server"]}}
BACKEND_LABELS = {"codex": "Codex", "deepseek": "DeepSeek"}
SESSION_KEYS = ("thread", "sessionOptions", "sessionChoices", "sessionCursor", "sessionSearch")


def definitions(config):
    result = deepcopy(DEFAULT_BACKENDS)
    # Keep pre-profile config.json files working without copying their settings.
    result["codex"].update(config.get("backend", {}))
    for name, settings in config.get("backends", {}).items():
        result[name] = {**result.get(name, {}), **settings}
    return result


def snapshot(state):
    return {key: deepcopy(state[key]) for key in SESSION_KEYS if key in state}


def restore(state, saved):
    for key in SESSION_KEYS:
        state.pop(key, None)
    state.update(deepcopy(saved))
    state.setdefault("thread", None)


def backend_label(name):
    return BACKEND_LABELS.get(name, str(name))


async def switch(bridge, name):
    resolved = next((key for key in bridge.backend_configs
                     if key.casefold() == str(name).casefold()), None)
    if resolved is None:
        available = " | ".join(backend_label(key) for key in bridge.backend_configs)
        raise UserFacingError(fill(BACKEND_INVALID, {"可用": available}))
    name = resolved
    if name == bridge.backend_name and bridge.backend_started and bridge.loaded:
        return
    saved = (snapshot(bridge.state) if name == bridge.backend_name else
             deepcopy(bridge.state.get("backendSessions", {}).get(name, {})))
    candidate = bridge.make_backend(name)
    thread_id = saved.get("thread")
    active = False
    try:
        await candidate.start()
        if thread_id:
            thread = await candidate.read_thread(thread_id)
            status = thread.get("status", {}).get("type")
            if status == "systemError" or (status == "active" and not candidate.shared):
                raise UserFacingError(BACKEND_SESSION_BUSY)
            active = status == "active"
        options = {} if active else saved.get("sessionOptions", {}).get(thread_id, {})
        selected = await candidate.thread(thread_id, options)
    except BaseException:
        await candidate.close()
        raise
    # Commit only after the target is ready. Failure above leaves the current backend intact.
    previous = bridge.backend
    bridge.state.setdefault("backendSessions", {})[bridge.backend_name] = snapshot(bridge.state)
    restore(bridge.state, saved)
    if active:
        bridge.state.setdefault("sessionOptions", {}).pop(thread_id, None)
    bridge.state["thread"] = selected
    bridge.backend_name = name
    bridge.state["backendName"] = name
    bridge.backend = candidate
    bridge.backend_started = True
    bridge.loaded = True
    bridge.resuming = True
    bridge.model_choices = []
    bridge.save()
    try:
        await previous.close()
    except Exception:
        bridge.reply(BACKEND_CLEANUP_FAILED)
