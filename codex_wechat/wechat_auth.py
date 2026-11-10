"""Shared WeChat credential state and cross-chat transition policy."""
import time
import shlex
from .platform_support import IS_WINDOWS, INSTALL_ROOT

AUTH_UNKNOWN = "unknown"
AUTH_VALID = "valid"
AUTH_REQUIRED = "reauth_required"
IDENTITY_CHANGED = "identity_changed"


def _entry_command(action):
    if IS_WINDOWS:
        path = str(INSTALL_ROOT / "codex-wechat.ps1").replace("'", "''")
        return "& '" + path + "' " + action
    return shlex.join([str(INSTALL_ROOT / "codex-wechat"), action])


def start_command():
    """Start the interactive entry that matches the current platform."""
    return _entry_command("start")


def rebind_command(*, stop_active=False):
    """An actionable local command independent of PATH and shell aliases."""
    command = _entry_command("rebind")
    if not stop_active:
        return command
    return command + (" -StopActive" if IS_WINDOWS else " --stop-active")


def identity(account):
    return {
        "bot": str((account or {}).get("bot") or ""),
        "user": str((account or {}).get("user") or ""),
    }


def state(bridge_state):
    value = bridge_state.get("wechatAuth")
    if isinstance(value, dict) and value.get("state") in (AUTH_VALID, AUTH_REQUIRED):
        return value["state"]
    return AUTH_UNKNOWN


def require_rebind(bridge_state, *, pending, now=None):
    """Enter the durable paused state; repeated failures do not resave it."""
    if state(bridge_state) == AUTH_REQUIRED:
        return False
    previous = bridge_state.get("wechatAuth")
    value = {
        "state": AUTH_REQUIRED,
        "errorCode": -14,
        "detectedAt": int(time.time() if now is None else now),
        "unsent": pending,
    }
    if isinstance(previous, dict):
        for key in ("bot", "user"):
            if previous.get(key):
                value[key] = previous[key]
    bridge_state["wechatAuth"] = value
    return True


def mark_valid(bridge_state, account, *, now=None):
    """Record a successful authenticated call without rewriting every poll."""
    value = {"state": AUTH_VALID, **identity(account),
             "checkedAt": int(time.time() if now is None else now)}
    current = bridge_state.get("wechatAuth")
    if (isinstance(current, dict) and current.get("state") == AUTH_VALID
            and current.get("bot") == value["bot"]
            and current.get("user") == value["user"]):
        return False
    bridge_state["wechatAuth"] = value
    return True


def prepare_identity_change(bridge_state, account):
    """Quarantine delivery tied to the old chat before saving a new bot."""
    old_outbox = list(bridge_state.get("outbox", []))
    quarantined_output = []
    for entry in old_outbox:
        saved = dict(entry)
        saved["status"] = IDENTITY_CHANGED
        quarantined_output.append(saved)
    if quarantined_output:
        bridge_state["failedOutbox"] = (
            list(bridge_state.get("failedOutbox", [])) + quarantined_output
        )[-20:]
    bridge_state["outbox"] = []

    current = bridge_state.pop("currentInput", None)
    old_inputs = ([current] if current else []) + list(bridge_state.get("inbox", []))
    records = list(bridge_state.get("inputRecords", []))
    for job in old_inputs:
        saved = dict(job)
        saved["phase"] = IDENTITY_CHANGED
        records.append(saved)
    if records:
        bridge_state["inputRecords"] = records[-20:]
    bridge_state["inbox"] = []
    bridge_state["pendingControls"] = []

    bridge_state["cursor"] = ""
    bridge_state["seen"] = []
    bridge_state.pop("quoteCache", None)
    bridge_state.pop("context", None)
    bridge_state.pop("wechatContextPending", None)
    for key in ("restartRequested", "running", "phase"):
        bridge_state.pop(key, None)
    bridge_state["wechatIdentity"] = identity(account)
    mark_valid(bridge_state, account)
    return {
        "inputs": len(old_inputs),
        "outbox": len(quarantined_output),
    }


def prepare_rebind(bridge_state, previous, account):
    """Keep delivery for the same WeChat user, renew bot-scoped transport state."""
    if not previous.get("user") or identity(previous)["user"] != identity(account)["user"]:
        return prepare_identity_change(bridge_state, account)
    bridge_state["cursor"] = ""
    bridge_state.pop("context", None)
    bridge_state["wechatContextPending"] = True
    for entry in bridge_state.get("outbox", []):
        entry["context"] = ""
        # Uploaded references and retry budgets belong to the old connection.
        entry.pop("item", None)
        entry.pop("attempts", None)
    for control in bridge_state.get("pendingControls", []):
        received = control.get("received") if isinstance(control, dict) else None
        media = received.get("media") if isinstance(received, dict) else None
        if isinstance(media, dict):
            media["context"] = ""
    bridge_state["wechatIdentity"] = identity(account)
    return {"inputs": 0, "outbox": 0}


def accept_context(bridge_state, context):
    """Resume preserved output only after this binding receives a fresh token."""
    bridge_state["context"] = context
    if bridge_state.pop("wechatContextPending", False):
        for entry in bridge_state.get("outbox", []):
            entry["context"] = context
        for control in bridge_state.get("pendingControls", []):
            received = control.get("received") if isinstance(control, dict) else None
            media = received.get("media") if isinstance(received, dict) else None
            if isinstance(media, dict):
                media["context"] = context
