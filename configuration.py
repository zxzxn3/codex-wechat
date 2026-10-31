"""Local settings and legacy migrations, independent of presentation."""
from dataclasses import dataclass

DEFAULT_COMPLETION_TEMPLATE = ""
DEFAULT_ACK_TEMPLATE = "{发送延迟}/{接收延迟}ms"
TEXT_LIMIT_BYTES = 3000


@dataclass(frozen=True)
class Notifications:
    compaction: bool = False
    quota_thresholds: tuple[int, ...] = (50, 25, 5)

    @classmethod
    def from_config(cls, config):
        compact = config.get("compactionNotice", cls.compaction)
        thresholds = config.get("quotaWarningThresholds", list(cls.quota_thresholds))
        if type(compact) is not bool:
            raise ValueError("compactionNotice 必须是布尔值")
        if (not isinstance(thresholds, list) or
                any(type(value) is not int or not 0 <= value <= 100 for value in thresholds)):
            raise ValueError("quotaWarningThresholds 必须是 0–100 整数列表")
        return cls(compact, tuple(sorted(set(thresholds), reverse=True)))


@dataclass(frozen=True)
class Slicing:
    mode: str = "smart"
    target_chars: int = 600

    @classmethod
    def from_config(cls, value=None):
        value = {} if value is None else value
        if not isinstance(value, dict) or set(value) - {"mode", "targetChars"}:
            raise ValueError("slicing 仅支持 mode 和 targetChars")
        mode = value.get("mode", cls.mode)
        target = value.get("targetChars", cls.target_chars)
        if mode not in ("smart", "length"):
            raise ValueError("slicing.mode 必须是 smart 或 length")
        if type(target) is not int or not 80 <= target <= 20000:
            raise ValueError("slicing.targetChars 必须是 80–20000 的整数")
        return cls(mode, target)


def processing_template(config, state):
    text = DEFAULT_ACK_TEMPLATE
    for source in (config.get("processingNotice"), state.get("processingNotice")):
        if isinstance(source, str):
            text = source
        elif isinstance(source, dict) and source.get("text") is not None:
            text = source["text"]
    if text in ("{latency}", "::{发送延迟}/{接收延迟}ms::"):
        return DEFAULT_ACK_TEMPLATE
    return str(text)


def completion_template(config, state):
    for source in (state.get("completionNotice"), config.get("completionNotice")):
        if isinstance(source, str):
            return source
    return DEFAULT_COMPLETION_TEMPLATE
