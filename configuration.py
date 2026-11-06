"""Local configuration and atomic hot-setting reloads."""
from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import sys
from prompts import validate_prompt_overrides
from diagnostics import error_fields, log_event
from log_rotation import DEFAULT_KEEP, KEEP_MAX, KEEP_MIN, normalize_keep

DEFAULT_SOURCE_HINT = "[User from WeChat.]"
HOT_CONFIG_KEYS = ("processingNotice", "completionNotice", "sourceHint", "slicing",
                   "compactionNotice", "quotaWarningThresholds", "prompts", "connectionIdleSeconds",
                   "sending", "logs")
DEFAULT_CONNECTION_IDLE_SECONDS = 60
CONNECTION_IDLE_MIN = 0
CONNECTION_IDLE_MAX = 3600
REPLY_BUDGET_MIN = 1
# Ten replies per inbound message is the community-observed ceiling; the ceiling
# here is deliberately higher so an operator who knows this key can take the risk.
REPLY_BUDGET_MAX = 100
# Past this observed value the bridge warns once, so a shared log shows the cause.
REPLY_BUDGET_CEILING = 9
DEFAULT_REPLY_BUDGET = 9
DEFAULT_COMPLETION_TEMPLATE = (
    "`OUT > {visible_output_tokens}, ~{tokens_per_second} tok/s in {final_answer_duration_s}s`")
DEFAULT_ACK_TEMPLATE = "`IN < ~{turn_total_tokens} ({ingress_ms}ms)`"
SLICING_TARGET_MIN = 80
SLICING_TARGET_MAX = 20000
TEXT_LIMIT_BYTES = 3000
SOURCE_HINT_LIMIT_BYTES = 4000
LOG_KEEP_MIN = KEEP_MIN
LOG_KEEP_MAX = KEEP_MAX
DEFAULT_LOG_KEEP = DEFAULT_KEEP
QUOTA_WINDOWS = ("primary", "secondary")
DEFAULT_QUOTA_THRESHOLDS = {
    "primary": (50, 25, 10, 5, 1),
    "secondary": (50, 25, 10, 5, 1),
}


class ConfigurationError(ValueError):
    """A persistent startup configuration problem requiring user correction."""


def write_config(path, config):
    """Replace the file in one step so a crash never leaves a partial configuration."""
    temp = Path(path).with_name(Path(path).name + ".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)


@dataclass(frozen=True)
class Notifications:
    compaction: bool = True
    quota_thresholds: dict[str, tuple[int, ...]] = field(
        default_factory=lambda: dict(DEFAULT_QUOTA_THRESHOLDS))

    @classmethod
    def from_config(cls, config):
        compact = config.get("compactionNotice", cls.compaction)
        if type(compact) is not bool:
            raise ValueError("compactionNotice 必须是布尔值")
        value = config.get("quotaWarningThresholds")
        if "quotaWarningThresholds" not in config:
            thresholds = dict(DEFAULT_QUOTA_THRESHOLDS)
        elif not isinstance(value, dict) or set(value) != set(QUOTA_WINDOWS):
            raise ValueError("quotaWarningThresholds 必须分别配置 primary 和 secondary")
        else:
            thresholds = {}
            for window in QUOTA_WINDOWS:
                values = value[window]
                if (not isinstance(values, list) or
                        any(type(item) is not int or not 0 <= item <= 100 for item in values)):
                    raise ValueError(
                        f"quotaWarningThresholds.{window} 必须是 0–100 整数列表")
                thresholds[window] = tuple(sorted(set(values), reverse=True))
        return cls(compact, thresholds)


@dataclass(frozen=True)
class Slicing:
    mode: str = "smart"
    target_chars: int = 600

    @classmethod
    def from_config(cls, value=None):
        value = {} if value is None else value
        if not isinstance(value, dict) or set(value) - {"smart", "targetChars"}:
            raise ValueError("slicing 仅支持 smart 和 targetChars")
        smart = value.get("smart", cls.mode == "smart")
        if type(smart) is not bool:
            raise ValueError("slicing.smart 必须是布尔值")
        mode = "smart" if smart else "length"
        target = value.get("targetChars", cls.target_chars)
        if type(target) is not int or not SLICING_TARGET_MIN <= target <= SLICING_TARGET_MAX:
            raise ValueError(
                f"slicing.targetChars 必须是 {SLICING_TARGET_MIN}–{SLICING_TARGET_MAX} 的整数")
        return cls(mode, target)


@dataclass(frozen=True)
class Sending:
    reply_budget: int = DEFAULT_REPLY_BUDGET

    @classmethod
    def from_config(cls, value=None):
        value = {} if value is None else value
        if not isinstance(value, dict) or set(value) - {"replyBudget"}:
            raise ValueError("sending 仅支持 replyBudget")
        budget = value.get("replyBudget", DEFAULT_REPLY_BUDGET)
        if type(budget) is not int or not REPLY_BUDGET_MIN <= budget <= REPLY_BUDGET_MAX:
            raise ValueError(
                f"sending.replyBudget 必须是 {REPLY_BUDGET_MIN}–{REPLY_BUDGET_MAX} 的整数")
        return cls(budget)


@dataclass(frozen=True)
class Logs:
    keep: int = DEFAULT_LOG_KEEP

    @classmethod
    def from_config(cls, value=None):
        value = {} if value is None else value
        if not isinstance(value, dict) or set(value) - {"keep"}:
            raise ValueError("logs 仅支持 keep")
        return cls(normalize_keep(value.get("keep", DEFAULT_LOG_KEEP)))


@dataclass(frozen=True)
class HotSettings:
    slicing: Slicing
    notifications: Notifications
    source_hint: str
    prompts: dict[str, str]
    connection_idle_seconds: int
    sending: Sending
    logs: Logs

    @classmethod
    def from_config(cls, config):
        if ("processingNotice" in config and not isinstance(config["processingNotice"], (str, dict))
                or "completionNotice" in config and not isinstance(config["completionNotice"], str)):
            raise ValueError("提示模板类型无效")
        hint = config.get("sourceHint", DEFAULT_SOURCE_HINT)
        if not isinstance(hint, str) or len(hint.encode()) > SOURCE_HINT_LIMIT_BYTES:
            raise ValueError(
                f"sourceHint 必须是最多 {SOURCE_HINT_LIMIT_BYTES} 字节的文本")
        templates = validate_prompt_overrides(config.get("prompts", {}), limit=TEXT_LIMIT_BYTES)
        idle = config.get("connectionIdleSeconds", DEFAULT_CONNECTION_IDLE_SECONDS)
        if type(idle) is not int or not CONNECTION_IDLE_MIN <= idle <= CONNECTION_IDLE_MAX:
            raise ValueError(
                f"connectionIdleSeconds 必须是 {CONNECTION_IDLE_MIN}–{CONNECTION_IDLE_MAX} 的整数")
        return cls(slicing=Slicing.from_config(config.get("slicing")),
                   notifications=Notifications.from_config(config),
                   source_hint=hint,
                   prompts=templates,
                   connection_idle_seconds=idle,
                   sending=Sending.from_config(config.get("sending")),
                   logs=Logs.from_config(config.get("logs")))


def processing_template(config, state):
    text = DEFAULT_ACK_TEMPLATE
    for source in (config.get("processingNotice"), state.get("processingNotice")):
        if isinstance(source, str):
            text = source
        elif isinstance(source, dict) and source.get("text") is not None:
            text = source["text"]
    return str(text)


def completion_template(config, state):
    text = DEFAULT_COMPLETION_TEMPLATE
    for source in (state.get("completionNotice"), config.get("completionNotice")):
        if isinstance(source, str):
            text = source
            break
    return text


class ConfigLoader:
    """Own only hot values; launcher configuration remains the startup snapshot."""
    def __init__(self, config, path=None):
        self.config = config
        self.path = Path(path) if path else None
        self.hot = HotSettings.from_config(config)
        self.signature = self.file_signature()
        self.failure_pending = False

    def file_signature(self):
        try:
            stat = self.path.stat() if self.path else None
            return (stat.st_ino, stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            return None

    def reload(self, overrides):
        """Apply a validated batch and report whether persisted overrides changed."""
        signature = self.file_signature()
        if not self.path or signature == self.signature:
            return False
        self.signature = signature
        try:
            fresh = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(fresh, dict):
                raise ValueError("配置必须是对象")
            hot = HotSettings.from_config(fresh)
            # An editor may replace the file while reading; retry on the next use.
            if self.file_signature() != signature:
                self.signature = None
                return False
        except (OSError, ValueError) as error:
            self.failure_pending = True
            kind = ("json" if isinstance(error, json.JSONDecodeError) else
                    "read" if isinstance(error, OSError) else "values")
            log_event("config.reload_failed", level="warning", component="config",
                      kind=kind, **error_fields(error))
            print("热配置未加载，保留原设置，请检查 JSON 和配置值。", file=sys.stderr)
            return False
        changed = False
        for key in HOT_CONFIG_KEYS:
            if fresh.get(key) != self.config.get(key):
                if key in ("processingNotice", "completionNotice") and key in overrides:
                    overrides.pop(key)
                    changed = True
                if key in fresh:
                    self.config[key] = fresh[key]
                else:
                    self.config.pop(key, None)
        self.hot = hot
        self.failure_pending = False
        return changed
