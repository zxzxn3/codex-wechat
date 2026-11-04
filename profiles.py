"""Backend definitions and per-backend session state, separate from WeChat state."""
import asyncio
from copy import deepcopy
import time
from platform_support import IS_WINDOWS
from errors import UserFacingError
from codex_transport import CodexRpcError

# An empty codex definition uses CodexBackend's existing default command.
DEFAULT_BACKENDS = {"codex": {}, "deepseek": {"command": ["codex-deepseek", "app-server"]}}
BACKEND_LABELS = {"codex": "Codex", "deepseek": "DeepSeek"}
SESSION_KEYS = ("thread", "sessionChoices", "sessionCursor", "sessionSearch")


def definitions(config):
    result = deepcopy(DEFAULT_BACKENDS)
    if IS_WINDOWS:
        result.pop("deepseek")  # Optional launcher, never a Windows prerequisite.
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
    state.update({key: deepcopy(saved[key]) for key in SESSION_KEYS if key in saved})
    state.pop("sessionOptions", None)
    state.setdefault("thread", None)


def backend_label(name):
    return BACKEND_LABELS.get(name, str(name))


def backend_argument(name):
    return str(name).lower()


def writer_conflict(error):
    """A resume rejected because another client still holds the thread's writer."""
    return (isinstance(error, CodexRpcError) and error.code == -32600
            and "already has an active writer" in str(error.reason))


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
