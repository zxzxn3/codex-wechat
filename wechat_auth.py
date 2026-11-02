"""Shared WeChat credential state and cross-chat transition policy."""
import time

AUTH_UNKNOWN = "unknown"
AUTH_VALID = "valid"
AUTH_REQUIRED = "reauth_required"
IDENTITY_CHANGED = "identity_changed"


def identity(account):
    return {
        "bot": str((account or {}).get("bot") or ""),
        "user": str((account or {}).get("user") or ""),
    }


def same_identity(left, right):
    return identity(left) == identity(right)


def state(bridge_state):
    value = bridge_state.get("wechatAuth")
    if isinstance(value, dict) and value.get("state") in (AUTH_VALID, AUTH_REQUIRED):
        return value["state"]
    return AUTH_UNKNOWN


def require_relink(bridge_state, *, pending, now=None):
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

    bridge_state["cursor"] = ""
    bridge_state["seen"] = []
    bridge_state.pop("context", None)
    for key in ("restartRequested", "running", "phase"):
        bridge_state.pop(key, None)
    bridge_state["wechatIdentity"] = identity(account)
    mark_valid(bridge_state, account)
    return {
        "inputs": len(old_inputs),
        "outbox": len(quarantined_output),
    }
