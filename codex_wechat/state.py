"""Versioned bridge state with strict loading and corrupt-file quarantine."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import time

from .diagnostics import log_event


STATE_VERSION = 1
STATE_RECOVERY_KEY = "stateRecovery"
_MISSING = object()
BRIDGE_STATE_DEFAULTS = {
    "version": STATE_VERSION,
    "cursor": "",
    "thread": None,
    "inbox": [],
    "outbox": [],
    "seen": [],
    "running": False,
    "inboundSeq": 0,
    "replyBudget": {},
    "deliveryUnknownTotal": 0,
    "pendingControls": [],
}


class BridgeStateError(ValueError):
    """A bridge state file that cannot be used safely."""

    def __init__(self, kind, detail, path):
        self.kind = kind
        self.detail = str(detail)
        self.path = Path(path)
        super().__init__(f"微信桥状态文件无效 ({kind}): {self.detail}")

    def diagnostic(self):
        return {
            "kind": self.kind,
            "detail": self.detail,
            "file": str(self.path),
        }


def _state_path(store):
    root = getattr(store, "root", None)
    return (Path(root) if _has_store_path(store) else Path(".")) / "bridge.json"


def _has_store_path(store):
    root = getattr(store, "root", None)
    return isinstance(root, (str, os.PathLike))


def _default_state():
    return deepcopy(BRIDGE_STATE_DEFAULTS)


def _is_int(value):
    return type(value) is int


def _expect(state, key, types, label, path, *, prefix=""):
    if key in state and not isinstance(state[key], types):
        field = f"{prefix}.{key}" if prefix else key
        raise BridgeStateError(
            "invalid_shape",
            f"{field} 必须是{label}，实际为 {type(state[key]).__name__}",
            path,
        )


def _expect_list_of(state, key, item_type, path, *, prefix=""):
    if key not in state:
        return
    value = state[key]
    if not isinstance(value, list) or any(not isinstance(item, item_type) for item in value):
        field = f"{prefix}.{key}" if prefix else key
        raise BridgeStateError(
            "invalid_shape",
            f"{field} 必须是 {item_type.__name__} 列表",
            path,
        )


def _expect_mapping_of_dicts(state, key, path):
    if key not in state:
        return
    value = state[key]
    if (not isinstance(value, dict)
            or any(not isinstance(name, str) or not isinstance(item, dict)
                   for name, item in value.items())):
        raise BridgeStateError(
            "invalid_shape",
            f"{key} 必须是字符串到对象的映射",
            path,
        )


def _entry_error(path, field, expected, value):
    raise BridgeStateError(
        "invalid_shape",
        f"{field} 必须是{expected}，实际为 {type(value).__name__}",
        path,
    )


def _expect_outbox_entries(state, path):
    if "outbox" not in state:
        return
    for index, entry in enumerate(state["outbox"]):
        prefix = f"outbox[{index}]"
        for field in ("id", "user"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                _entry_error(path, f"{prefix}.{field}", "非空字符串", entry.get(field))
        if "context" not in entry or not isinstance(entry["context"], (str, type(None))):
            _entry_error(
                path,
                f"{prefix}.context",
                "字符串或 null",
                entry.get("context"),
            )
        if "replySeq" not in entry:
            raise BridgeStateError(
                "invalid_shape",
                f"{prefix}.replySeq 缺失",
                path,
            )
        if not _is_int(entry["replySeq"]) or entry["replySeq"] < 0:
            _entry_error(path, f"{prefix}.replySeq", "非负整数", entry["replySeq"])

        payload = False
        if "text" in entry:
            if not isinstance(entry["text"], str):
                _entry_error(path, f"{prefix}.text", "字符串", entry["text"])
            payload = True
        if "item" in entry:
            if not isinstance(entry["item"], dict):
                _entry_error(path, f"{prefix}.item", "对象", entry["item"])
            payload = True
        if "asset" in entry:
            asset = entry["asset"]
            if not isinstance(asset, dict):
                _entry_error(path, f"{prefix}.asset", "对象", asset)
            for field in ("path", "name", "kind"):
                if not isinstance(asset.get(field), str) or not asset[field]:
                    _entry_error(
                        path,
                        f"{prefix}.asset.{field}",
                        "非空字符串",
                        asset.get(field),
                    )
            if asset["kind"] not in ("image", "video", "file"):
                _entry_error(
                    path,
                    f"{prefix}.asset.kind",
                    " image、video 或 file",
                    asset["kind"],
                )
            payload = True
        if not payload:
            raise BridgeStateError(
                "invalid_shape",
                f"{prefix} 缺少 text、item 或 asset",
                path,
            )


def _expect_binding_pending(state, path):
    if "bindingPending" not in state or state["bindingPending"] is None:
        return
    pending = state["bindingPending"]
    if not isinstance(pending, dict):
        _entry_error(path, "bindingPending", "对象或 null", pending)
    target = pending.get("target")
    if not isinstance(target, dict):
        _entry_error(path, "bindingPending.target", "对象", target)
    for field in ("bot", "user"):
        if not isinstance(target.get(field), str) or not target[field]:
            _entry_error(
                path,
                f"bindingPending.target.{field}",
                "非空字符串",
                target.get(field),
            )


def _expect_nonnegative_int(state, key, path):
    if key in state and (not _is_int(state[key]) or state[key] < 0):
        raise BridgeStateError(
            "invalid_shape",
            f"{key} 必须是非负整数",
            path,
        )


def _validate_state(state, path):
    _expect(state, "cursor", str, "字符串", path)
    _expect(state, "thread", (str, type(None)), "字符串或 null", path)
    _expect_list_of(state, "inbox", dict, path)
    _expect_list_of(state, "outbox", dict, path)
    _expect_outbox_entries(state, path)
    _expect_list_of(state, "seen", str, path)
    _expect(state, "running", bool, "布尔值", path)
    _expect_nonnegative_int(state, "inboundSeq", path)
    _expect(state, "replyBudget", dict, "对象", path)
    budget = state.get("replyBudget")
    if isinstance(budget, dict):
        for key in ("seq", "used"):
            if key in budget and (not _is_int(budget[key]) or budget[key] < 0):
                raise BridgeStateError(
                    "invalid_shape",
                    f"replyBudget.{key} 必须是非负整数",
                    path,
                )
    _expect_nonnegative_int(state, "deliveryUnknownTotal", path)
    _expect_list_of(state, "pendingControls", dict, path)

    _expect(state, "backendName", str, "字符串", path)
    _expect_mapping_of_dicts(state, "backendSessions", path)
    for name, session in state.get("backendSessions", {}).items():
        prefix = f"backendSessions.{name}"
        _expect(session, "thread", (str, type(None)), "字符串或 null", path, prefix=prefix)
        _expect_list_of(session, "sessionChoices", str, path, prefix=prefix)
        _expect(session, "sessionCursor", (str, type(None)),
                "字符串或 null", path, prefix=prefix)
        _expect(session, "sessionSearch", (str, type(None)),
                "字符串或 null", path, prefix=prefix)
    _expect(state, "context", (str, type(None)), "字符串或 null", path)
    _expect(state, "currentInput", (dict, type(None)), "对象或 null", path)
    _expect_list_of(state, "inputRecords", dict, path)
    _expect_list_of(state, "failedOutbox", dict, path)
    _expect_list_of(state, "sessionChoices", str, path)
    _expect(state, "sessionCursor", (str, type(None)), "字符串或 null", path)
    _expect(state, "sessionSearch", (str, type(None)), "字符串或 null", path)
    _expect(state, "quoteCache", dict, "对象", path)
    _expect(state, "wechatAuth", dict, "对象", path)
    _expect(state, "wechatIdentity", dict, "对象", path)
    _expect_binding_pending(state, path)
    _expect(state, "wechatContextPending", bool, "布尔值", path)
    if (not state.get("wechatContextPending")
            and any(not entry.get("context") for entry in state["outbox"])):
        raise BridgeStateError(
            "invalid_shape",
            "outbox.context 只有在 wechatContextPending 时才能为空",
            path,
        )
    _expect(state, "restartRequested", bool, "布尔值", path)
    _expect(state, "budgetPromptSent", bool, "布尔值", path)
    _expect_nonnegative_int(state, "consecutiveSendFailures", path)
    _expect(state, "processingNotice", (str, dict), "字符串或对象", path)
    _expect(state, "completionNotice", str, "字符串", path)
    _expect(state, STATE_RECOVERY_KEY, dict, "对象", path)
    if "lastSendMs" in state:
        value = state["lastSendMs"]
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < 0):
            raise BridgeStateError(
                "invalid_shape",
                "lastSendMs 必须是非负有限数值",
                path,
            )


def normalize_bridge_state(saved, path):
    """Validate a current-version state dict without migrating older formats."""
    if not isinstance(saved, dict):
        raise BridgeStateError(
            "invalid_shape",
            f"顶层必须是对象，实际为 {type(saved).__name__}",
            path,
        )
    if "version" not in saved:
        raise BridgeStateError("invalid_shape", "version 缺失", path)
    version = saved["version"]
    if not _is_int(version):
        raise BridgeStateError(
            "invalid_shape",
            f"version 必须是整数，实际为 {type(version).__name__}",
            path,
        )
    if version != STATE_VERSION:
        raise BridgeStateError(
            "unsupported_version",
            f"仅支持 {STATE_VERSION}，实际为 {version!r}",
            path,
        )
    state = {**_default_state(), **deepcopy(saved), "version": STATE_VERSION}
    _validate_state(state, path)
    return state


def _quarantine(store, error):
    if not _has_store_path(store):
        raise BridgeStateError(
            "quarantine_failed",
            "状态存储没有可写目录，不能隔离损坏文件",
            error.path,
        )
    path = error.path
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = path.with_name(f"bridge.corrupt-{stamp}-{os.getpid()}-{time.time_ns()}.json")
    try:
        os.replace(path, target)
    except OSError as move_error:
        raise BridgeStateError(
            "quarantine_failed",
            f"无法隔离损坏状态: {move_error}",
            path,
        ) from move_error
    state = _default_state()
    state[STATE_RECOVERY_KEY] = {
        "detectedAt": int(time.time()),
        "kind": error.kind,
        "detail": error.detail,
        "quarantine": str(target),
    }
    store.save("bridge", state)
    log_event(
        "state.quarantined",
        level="warning",
        component="state",
        reason=error.kind,
    )
    print(
        "微信桥状态文件无法安全读取，已原样隔离到 "
        f"{target}；当前将从空状态启动。运行 codex-wechat status 查看恢复信息。",
        file=sys.stderr,
        flush=True,
    )
    return state


def load_bridge_state(store, *, recover=True):
    """Load v1 state; when writable, quarantine unreadable state."""
    path = _state_path(store)
    has_store_path = _has_store_path(store)
    if has_store_path and (path.is_symlink() or (path.exists() and not path.is_file())):
        raise BridgeStateError("invalid_file", "状态路径不是普通文件", path)
    try:
        saved = store.load("bridge", _MISSING)
    except UnicodeDecodeError as error:
        state_error = BridgeStateError(
            "invalid_encoding",
            str(error),
            path,
        )
        if recover:
            return _quarantine(store, state_error)
        raise state_error from error
    except json.JSONDecodeError as error:
        state_error = BridgeStateError("invalid_json", str(error), path)
        if recover:
            return _quarantine(store, state_error)
        raise state_error from error
    if saved is _MISSING:
        return _default_state()
    try:
        return normalize_bridge_state(saved, path)
    except BridgeStateError as error:
        if recover and error.kind != "unsupported_version":
            return _quarantine(store, error)
        raise
