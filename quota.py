"""Threshold crossing detection on native account metadata, without I/O."""
import json
import math
import time

from prompts import limit_duration, prompt_text


def quota_warnings(records, payload, scope, thresholds, *, now=None, text=prompt_text):
    if not thresholds or not isinstance(payload, dict):
        return []
    now = time.time() if now is None else now
    buckets = payload.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        snapshot = payload.get("rateLimits")
        buckets = {snapshot.get("limitId") or "default": snapshot} if isinstance(snapshot, dict) else {}
    buckets = dict(buckets)
    legacy = payload.get("rateLimits")
    if isinstance(legacy, dict):
        identity = legacy.get("limitId")
        if identity in buckets:
            buckets[identity] = legacy
        elif len(buckets) == 1 and not identity:
            buckets[next(iter(buckets))] = legacy
    messages = []
    for bucket, snapshot in buckets.items():
        if not isinstance(snapshot, dict):
            continue
        for window in ("primary", "secondary"):
            value = snapshot.get(window)
            if not isinstance(value, dict):
                continue
            used = value.get("usedPercent")
            if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
                continue
            remaining = 100 - used
            key = json.dumps([scope, payload.get("accountId"), bucket, window,
                              value.get("windowDurationMins")], ensure_ascii=False)
            old = records.get(key)
            reset = value.get("resetsAt")
            reset = reset if type(reset) in (int, float) and math.isfinite(reset) else None
            warned = set(old.get("warned", [])) if old else set()
            if old:
                old_reset = old.get("reset")
                if ((old_reset is not None and reset is not None and now >= old_reset and reset > old_reset)
                        or remaining > max(thresholds)):
                    warned.clear()
                crossed = [t for t in thresholds if old["remaining"] > t >= remaining and t not in warned]
                if crossed:
                    warned.update(crossed)
                    duration = limit_duration(value.get("windowDurationMins")) or window
                    label = f"{snapshot.get('limitName') or bucket} " if len(buckets) > 1 else ""
                    messages.append(text("QUOTA_WARNING_BODY", {"标签": label, "窗口": duration,
                                    "剩余": f"{remaining:g}", "阈值": min(crossed)}))
            # First available sample is a baseline, not a replay of past warnings.
            records[key] = {"remaining": remaining, "reset": reset, "warned": sorted(warned)}
    while len(records) > 64:
        records.pop(next(iter(records)))
    return messages
