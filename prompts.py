"""Single source for user-visible copy and presentation formats."""
from datetime import datetime
import os
import re
from string import Formatter
from types import MappingProxyType


MAX_NOTICE_BYTES = 1000
COMPACT_STARTED = "正在压缩上下文..."
COMPACT_FINISHED = "上下文压缩已结束。"
COMPACT_INTERRUPTED = "上下文压缩已中断。"
COMPACT_UNCONFIRMED = "上下文压缩结果未确认。"

BACKEND_SUMMARY = "```\n后端: {current}\n可用: {available}\n```\n`/backend {{名称}}`"
EFFORT_SUMMARY = "```\n强度: {current}\n可选: {choices}\n```\n`/effort {{档位}}`"
ACK_VIEW = ("```\nack: {template}\n```\n"
            "`/settings ack {{模板}}`\n`/settings ack \"\"` 关闭\n"
            "`/settings ack fields` 可用字段")
ACK_TOO_LONG = "未更新 ack: 最多 {max_bytes} 字节"
WORKING_DIRECTORY = "目录: {cwd}"
MEDIA_USAGE = "`/get {路径}`\n`/getfile {路径}`"

BLOCKED_NOTE = "等待桌面处理"

BUSY_CHANGE = "未操作: 任务未结束。等待完成或 `/stop`。"
BUSY_RESTART = "有尚未完成的任务\n使用 `/stop` 终止后重试。"
RESTART_STARTED = "正在重启微信桥..."
RESTART_READY = "微信桥已重启。"
SERVICE_STOPPING = "微信桥正在停止。"
RESTART_CONFIRM_UNSENT = "确认消息未能及时发送。"
RESTART_FAILED = "重启未完成: {reason}"
RESTART_NOT_SERVICE = "当前微信桥不是由用户服务启动。"

STOP_REQUESTED = "已发送停止请求，正在等待当前任务结束。"
STOP_DEFERRED = "已发送停止请求，正在等待当前任务结束。"
STOP_IDLE = "当前没有运行中的任务。"
STOP_UNCONFIRMED = "停止结果未确认，任务可能仍在运行；请查看 /status 或 Codex。"

BACKEND_CONNECT_FAILED = "后端连接失败: {reason}\n正在重连，未提交的待办已保留。"
TASK_FAILED = "消息未提交: 后端处理失败。查看 `/status`。"
TURN_STALLED = (
    "本轮已中止: 长时间没有收到任何进展。\n"
    "会话可能仍在桌面端运行；可用 `/status` 查看，再发消息会重新连接。"
)
TURN_FAILED = "任务未完成: Agent 执行失败。"
TURN_INTERRUPTED = "任务已中断。"
TURN_ENDED_UNEXPECTEDLY = "任务未正常完成。\n使用 `/status` 查看状态。"
TURN_COMPLETED_EMPTY = "任务已完成，但没有文本结果。"
UNCERTAIN_STEER = "补充消息提交未确认，未重发。请在桌面查看。"
QUEUED_STEER = "暂时无法追加，已转入排队。"
QUEUE_CONFIRMED = "已排队: {count} {unit}"
UNQUEUE_CANCELLED = "已撤回，剩余 {count} {unit}。"
UNQUEUE_EMPTY = "没有可撤回的排队消息。"
PING_REPLY = "微信 ─{ingress_ms}/{egress_ms}ms"
PING_PARTIAL = "微信 ─{latency}"
PING_ROUTE = "桥接 ─{provider_latency}─ 提供方"
PING_USAGE = "用法: `/ping`"
UNKNOWN_COMMAND = "未知指令，查看 `/help`"
TOO_MANY_ATTACHMENTS = "消息未接收: 附件超过 {count} 个。\n请分开发送。"
QUEUE_FULL = "拒绝接收: 排队消息已达 {count} 条。\n请等待完成或用 `/unqueue` 撤回最后一条。"
CONFIG_RELOAD_FAILED = "热配置未生效，保留原设置。请检查配置文件。"
UNSUPPORTED_MESSAGE = (
    "消息未接收: 仅支持文字、语音转写、图片、视频和文件。\n"
    "文字每条最多 {count} 字符。"
)
UNSUPPORTED_MESSAGE_TYPE = "暂时不支持这个类型，请发送文字、图片、视频或文件。"

ATTACHMENT_INCOMING_FAILED = (
    "附件处理失败: {reason}\n"
    "这条消息未交给 Agent。"
)
ATTACHMENT_QUEUED = "附件已排队"
ATTACHMENT_SEND_FAILED = (
    "附件未发出: {name}\n"
    "重试 {attempts} 次仍失败，请稍后用 `/get` 重试。"
)
AUTO_ATTACHMENT_LIMIT = "附件未发送: 自动回传最多 {count} 个，超出的已跳过。"
AUTO_ATTACHMENT_FAILED = "附件未发送: {reason}{hint}"
AUTO_ATTACHMENT_PATH_HINT = "\n其他位置请使用 `/get {路径}`。"

APPROVAL_ACCEPTED = "已同意"
APPROVAL_DECLINED = "已拒绝"
APPROVAL_EXPIRED = "审批已失效，请在桌面确认。"
APPROVAL_FAILED = "审批提交未确认，请在桌面处理。"
APPROVAL_TOO_LONG = (
    "审批内容过长，手机端无法完整显示。\n"
    "本次请求未通过，请到电脑端处理。"
)
UNSUPPORTED_INTERACTION = "手机端暂不支持这个操作。\n本次请求未能继续，请到电脑端处理。"

COMMAND_FAILURE_LABELS = {
    "/settings": "设置操作失败",
    "/backend": "切换后端失败",
    "/sessions": "读取会话失败",
    "/switch": "未切换会话",
    "/new": "未新建会话",
    "/compact": "压缩请求未确认，请先查看 `/status`，勿重复提交",
    "/model": "未切换模型",
    "/effort": "未切换强度",
    "/cwd": "未切换目录",
}
COMMAND_FAILURE = "{action}: {reason}"

SETTINGS_USAGE = ("用法:\n`/settings [autostart|shared|compactionNotice|slicing.smart] true|false`\n"
                  "`/settings connectionIdleSeconds|media.maxMiB|slicing.targetChars <整数>`\n"
                  "`/settings sourceHint <文本>`\n"
                  "`/settings ack|finish <模板>|\"\"|fields`")
SETTINGS_HINT = ("以上设置可通过 `/settings [option] [value]` 更改。\n"
                 "使用 `/help settings` 查看设置说明。")
SETTINGS_HELP = """设置说明
`/settings` 查看当前值；`/settings 名称 值` 修改。

ack: 接收提示模板；空字符串关闭；`/settings ack fields` 查看字段。
finish: 结束提示模板；空字符串关闭；`/settings finish fields` 查看字段。
autostart: true|false，登录时自动启动后台服务。
shared: true|false，与 Codex CLI 共用 app-server；默认 Windows 为 false、Linux 为 true；开启后 App 可能锁定当前会话。
compactionNotice: true|false，上下文压缩开始和结束时发送提示。
connectionIdleSeconds: 0–3600，微信 HTTP 连接池空闲秒数；0 表示不主动回收。
media.maxMiB: 1–100，单个微信附件的大小上限。
slicing.smart: true|false，true 使用智能语义切片，false 按长度切片。
slicing.targetChars: 80–20000，切片目标字符数。
sourceHint: 附加到每条微信用户消息的来源说明；空字符串关闭。

[无法直接修改]
quotaWarningThresholds.primary: 5 小时限额提醒阈值；修改 config.json 后热加载。
quotaWarningThresholds.secondary: 7 天限额提醒阈值；修改 config.json 后热加载。"""
SETTINGS_NUMBER = "{name} 必须是 {minimum}–{maximum} 的整数"
SETTINGS_WRITE_FAILED = "未保存设置: 配置文件不可写或格式无效"
SETTINGS_NOT_APPLIED = "设置已写入，但未生效；请检查配置文件。"
SOURCE_HINT_TOO_LONG = "未更新 sourceHint: 最多 {max_bytes} 字节"
SETTINGS_SHARED = "shared: {value}"
SETTINGS_AUTOSTART = "autostart: {value}"
SETTINGS_SHARED_FAILED = "未保存共享后台设置: 配置文件不可写"
SETTINGS_SHARED_FALLBACK = "共享后台不可用: {reason}；本次已退回独立进程，Codex CLI 的回合不会进入微信"
SETTINGS_SHARED_BUSY = "会话暂时仍被后台占用，下一条消息会自动接管"
SETTINGS_SHARED_UNSAVED = "已切换，但设置未能写入配置文件，重启后会恢复原模式"
BACKEND_INVALID = "未切换后端: 名称无效\n可用: {available}"
BACKEND_SESSION_BUSY = (
    "未切换后端: 目标后端的上次会话正在工作或等待处理。\n"
    "请先在电脑端处理。"
)
BACKEND_CLEANUP_FAILED = (
    "已切换后端，当前选择已保存。\n"
    "旧连接未清理，请在电脑端检查。"
)
SESSION_TARGET_BLOCKED = (
    "未切换会话: 目标会话需要先在电脑端处理。\n"
    "请完成后再重试。"
)
SESSION_TARGET_ACTIVE = (
    "未切换会话: 工作中的会话只能通过桌面共享连接。\n"
    "请切换后端或等待任务结束。"
)
SESSION_WRITER_BUSY = (
    "未提交: 会话正被另一个 Codex 客户端占用。\n"
    "请在 App 或 CLI 中关闭该会话，或稍后重试。"
)
SESSION_WRITER_QUEUED = (
    "已排队: 会话正被另一个 Codex 客户端占用，暂时无法提交。\n"
    "在 App 或 CLI 中关闭该会话后会自动发送；最多等待 10 分钟。"
)
SESSION_INDEX_INVALID = "未切换会话: 编号无效\n请先发送 `/sessions`。"
SESSION_USAGE = "用法: `/switch {{编号、ID前缀或搜索词}}`"
NEW_USAGE = "用法: `/new`"
MODEL_INVALID = "未切换模型: 名称无效\n请使用 `/model` 查看可用模型。"
MODEL_NOT_IN_CATALOG = "无法读取强度: 当前模型不在模型列表中，请重新选择模型。"
EFFORT_INVALID = "未切换强度: 可选 {choices}"
DIRECTORY_INVALID = "未切换目录: 目录不存在或无法访问\n请检查路径后重试。"
DIRECTORY_NOT_FOLDER = "未切换目录: 路径不是文件夹\n请选择文件夹。"
RESTART_USAGE = "用法: `/restart`"

# All uppercase string constants in this module are overrideable presentation text.
# Protocol identifiers, command routing and user/model content do not belong here.
LABEL_STATUS = "状态"
LABEL_BACKEND = "后端"
LABEL_SESSION = "会话"
LABEL_SESSION_ID = "ID"
LABEL_MODEL = "模型"
LABEL_EFFORT = "强度"
LABEL_CWD = "目录"
LABEL_PROVIDER = "提供方"
LABEL_PERMISSIONS = "权限"
LABEL_COLLABORATION = "协作"
LABEL_SERVICE = "服务"
LABEL_TOKENS = "Token"
LABEL_CONTEXT = "上下文"
LABEL_LIMITS = "限额"
LABEL_QUEUED = "待办"
LABEL_APPROVALS = "审批"
LABEL_SEND_FAILURES = "发送失败"
LABEL_SHARED = "共享后台"
LABEL_ACK = "接收提示"
LABEL_FINISH = "结束提示"
LABEL_AUTOSTART = "自启"
LABEL_CONNECTION_IDLE = "连接空闲"
VALUE_CONNECTION_IDLE_UNLIMITED = "不按空闲回收"
LABEL_ATTACHMENTS = "附件上限"
LABEL_SLICING = "切片"
LABEL_TARGET_LENGTH = "目标长度"
LABEL_SOURCE = "来源说明"
LABEL_COMPACTION = "压缩提示"
LABEL_QUOTA_WARNING = "限额警告"
LABEL_QUOTA_PRIMARY = "5小时"
LABEL_QUOTA_SECONDARY = "7天"
CURRENT_SESSION = "当前会话"
SESSION_DESKTOP = "已连接桌面会话"
SESSION_RESTORED = "已恢复会话"
SESSION_CREATED = "已新建会话"
SESSION_SWITCHED = "已切换会话"
BACKEND_SWITCHED = "已切换后端"
NO_SESSIONS = "\n没有匹配的会话。"
NO_MODELS = "暂无可用模型"
SESSION_PAGE = "当前{page}/{total_pages}页"
PAGE_INVALID = "页码必须大于 0"
PAGE_RANGE = "页码超出范围: 1–{count}"
SESSION_SEARCH_EMPTY = "没有匹配“{query}”的会话。"
SESSION_SEARCH_MULTIPLE = "找到 {count} 个匹配会话；请用 `/switch {{编号}}` 选择。"
SESSION_SEARCH_MORE = "仅显示前 {count} 条；`/sessions {query}` 查看全部。"
SESSION_ID_PREFIX_MORE = "共匹配 {count} 个会话；仅显示前 {shown} 个，请继续输入更多 ID 字符缩小范围。"
SESSION_LIST_FAILED = "会话列表读取未完成，请重试。"
COMPACT_BLOCKED = "暂不能压缩: 当前会话等待处理。"
COMPACT_REQUESTED = "已请求压缩上下文。"
AUTOSTART_FAILED = "自启设置未确认，请在电脑检查本项目的服务安装。"
RESTART_REJECTED = "拒绝重启: {reason}"
STOP_CLEARED = "\n已清空待办: {count}"
ANSWER_ACCEPTED = "已回答。"
ANSWER_EXPIRED = "问题已失效或编号无效。"
ANSWER_FAILED = "回答投递结果未确认，请查看桌面；未自动重发。"
SEND_FILE_FAILED = "附件未发送: {reason}\n```\n{path}\n```\n请检查文件，或稍后重试。"
REPLY_SEND_FAILED = "这条回复未能完整发送，已停止其余部分。请在桌面查看。"
INPUT_RECOVERED = "上次有任务已提交或提交结果未确认，未自动重发。未提交的待办已保留。"
INPUT_RESULT_INTERRUPTED = "任务已提交，但结果接收中断，未重新执行。"
CONTROL_QUEUE_FULL = "指令等待过多，请稍后重试。"
CONTROL_TIMEOUT = "控制请求未确认，请查看 `/status`；不会自动重发。"
CONTROL_FAILED = "指令未完成，请查看 `/status` 后重试。"
GENERIC_FAILURE = "操作失败"
COMMAND_USAGE = "用法: `{usage}`"
HELP_USAGE = "用法: `/help`、`/help all` 或 `/help settings`"
INPUT_SUBMISSION_UNCERTAIN = "消息提交结果未确认，未自动重发。"
BACKEND_UNREADY = "后端尚未就绪，未提交的消息已保留；可用 `/stop` 清空。"
RESULT_PREPARE_FAILED = "结果准备失败，请在桌面查看该轮回答。"
PREPARATION_STOPPED = "已停止，未启动模型轮次。"
QUOTA_WARNING = "限额提醒: {backend}\n{content}"
QUOTA_WARNING_BODY = "{label}{window}剩余 {remaining}%"
WECHAT_AUTH_TITLE = "微信绑定已失效"
WECHAT_REBIND_HINT = "重新绑定\n{command}"
VALUE_ON = "开启"
VALUE_OFF = "关闭"
VALUE_UNAVAILABLE = "N/A"
VALUE_EMPTY = ""
APPROVAL_COMMAND_TITLE = "审批: 执行命令"
APPROVAL_FILE_TITLE = "审批: 修改文件"
LABEL_COMMAND = "命令"
COMMAND_NOT_PROVIDED = "未提供"
LABEL_REASON = "原因"
LABEL_SCOPE = "范围"
ACTION_APPROVE = "同意"
ACTION_DENY = "拒绝"
ANSWER_ARGUMENT = "选项或文字"
ANSWER_OPTION_RANGE = "选项编号应为 1–{count}；也可直接填写文字。"
FINISH_VIEW = ACK_VIEW.replace("/settings ack", "/settings finish").replace("ack:", "finish:", 1)
FINISH_TOO_LONG = ACK_TOO_LONG.replace("ack", "finish")

HELP_STATUS = '当前状态'
HELP_SETTINGS = '查看设置'
HELP_SESSIONS = '列出、搜索或翻页会话'
HELP_NEW = '新建'
HELP_COMPACT = '压缩当前会话上下文'
HELP_QUEUE = '排队到当前任务结束后执行'
HELP_UNQUEUE = '撤回最后一条尚未开始的排队消息'
HELP_STOP = '停止并清空待办'
HELP_PING = '微信与供应商网络延迟'
HELP_SWITCH = '按编号、ID前缀或搜索词切换会话'
HELP_BACKEND = '查看或切换后端'
HELP_MODEL = '查看或切换模型'
HELP_EFFORT = '查看或切换强度'
HELP_CWD = '查看或切换目录'
HELP_APPROVE = '同意审批'
HELP_DENY = '拒绝审批'
HELP_ANSWER = '回答原生提问'
HELP_GET = '从电脑取回文件、图片或视频'
HELP_GETFILE = '作为文件回传'
HELP_RESTART = '重启微信桥'
HELP_HELP = '查看常用命令；`/help all` 查看全部；`/help settings` 查看设置说明'

DEFAULT_PROMPTS = MappingProxyType({name: value for name, value in tuple(globals().items())
                                  if name.isupper() and isinstance(value, str)} |
    {"FAILURE_" + command[1:].upper(): message for command, message in COMMAND_FAILURE_LABELS.items()})
HELP_DESCRIPTIONS = {"/" + key[5:].lower(): value for key, value in DEFAULT_PROMPTS.items()
                     if key.startswith("HELP_")}
_FORMATTER = Formatter()

_FIELD_LABELS = {
    "status": "LABEL_STATUS", "backend": "LABEL_BACKEND", "session": "LABEL_SESSION",
    "session_id": "LABEL_SESSION_ID",
    "model": "LABEL_MODEL", "effort": "LABEL_EFFORT", "cwd": "LABEL_CWD",
    "provider": "LABEL_PROVIDER", "permissions": "LABEL_PERMISSIONS", "collaboration": "LABEL_COLLABORATION",
    "connection": "LABEL_SERVICE", "token_usage": "LABEL_TOKENS", "context_usage": "LABEL_CONTEXT",
    "rate_limits": "LABEL_LIMITS", "queue_depth": "LABEL_QUEUED", "pending_requests": "LABEL_APPROVALS",
    "send_failures": "LABEL_SEND_FAILURES",
    "shared_fallback": "LABEL_SHARED",
}


def validate_prompt_overrides(value, *, limit):
    if not isinstance(value, dict):
        raise ValueError("prompts 必须是对象")
    for name, template in value.items():
        if name not in DEFAULT_PROMPTS or not isinstance(template, str):
            raise ValueError("prompts 包含未知名称或非文本值")
        if len(template.encode("utf-8")) > limit:
            raise ValueError("prompts 模板过长")
        for _, field, spec, conversion in _FORMATTER.parse(template):
            if field is not None and (not field.isidentifier() or spec or conversion):
                raise ValueError("prompts 仅支持简单的 {字段} 替换")
    return dict(value)


def prompt_text(name, values=None, *, overrides=None):
    """Render once; preserve unknown fields and never traverse attributes or indices."""
    template = (overrides or {}).get(name, DEFAULT_PROMPTS[name])
    values = values or {}
    parts = []
    for literal, field, _, _ in _FORMATTER.parse(template):
        parts.append(literal)
        if field is not None:
            parts.append(str(values[field]) if field in values else "{" + field + "}")
    return "".join(parts)


def render_ack(template, values):
    """Render documented snake_case fields once; unknown fields remain literal."""
    # One pass prevents a field value containing another placeholder from
    # triggering a second expansion. Unknown fields remain literal text.
    def replace(match):
        value = values.get(match[1], match[0])
        return VALUE_UNAVAILABLE if value is None else str(value)
    return re.sub(r"\{([^{}]+)\}", replace, template)


def effort_label(value):
    return str(value).replace("_", " ").capitalize() if value else "默认"


def duration_label(seconds):
    """Format a turn duration for prompt templates; None means no sample yet."""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m{rest:02d}s"


def model_label(model, effort):
    model = model or "默认模型"
    return f"{model} ({effort_label(effort)})"


def display_path(value):
    path = str(value or "")
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def compact_label(value, fallback=VALUE_UNAVAILABLE, limit=100):
    text = " ".join(str(value or "").split())
    return text[:limit] or fallback


def provider_label(value):
    labels = {"openai": "OpenAI", "deepseek": "DeepSeek"}
    text = compact_label(value)
    return labels.get(text.casefold(), text)


def permission_label(*, sandbox, approval, reviewer, profile=None):
    sandbox_name = compact_label(
        (sandbox or {}).get("type") if isinstance(sandbox, dict) else sandbox,
        fallback="",
    )
    permission = {
        "dangerFullAccess": "完全访问",
        "workspaceWrite": "工作区可写",
        "readOnly": "只读",
        "externalSandbox": "外部沙箱",
    }.get(sandbox_name)
    if not permission and isinstance(profile, dict):
        permission = compact_label(profile.get("id"), fallback="")
    permission = permission or VALUE_UNAVAILABLE

    if isinstance(approval, dict):
        approval_name = "精细审批"
    else:
        approval_name = {
            "untrusted": "首次操作需审批",
            "on-request": "按需审批",
            "never": "无需审批",
        }.get(compact_label(approval, fallback=""), "")
    reviewer_name = {
        "auto_review": "自动审查",
        "guardian_subagent": "Guardian 审查",
    }.get(compact_label(reviewer, fallback=""), "")
    detail = "  ".join(value for value in (approval_name, reviewer_name) if value)
    return f"{permission}  {detail}" if detail else permission


def collaboration_label(value):
    mode = value.get("mode") if isinstance(value, dict) else value
    return {
        "default": "默认",
        "plan": "规划",
    }.get(compact_label(mode, fallback=""), compact_label(mode))


def token_count(value):
    try:
        number = max(0, int(value))
    except (TypeError, ValueError):
        return VALUE_UNAVAILABLE
    if number >= 1_000_000:
        text = f"{number / 1_000_000:.1f}M"
    elif number >= 1_000:
        text = f"{number / 1_000:.1f}K"
    else:
        return str(number)
    return text.replace(".0M", "M").replace(".0K", "K")


def context_usage(value):
    last = value.get("last", {}) if isinstance(value, dict) else {}
    used = last.get("totalTokens")
    window = value.get("modelContextWindow") if isinstance(value, dict) else None
    used_text = token_count(used)
    if used_text == VALUE_UNAVAILABLE:
        return used_text
    window_text = token_count(window)
    if window_text == VALUE_UNAVAILABLE:
        return f"{used_text} Token"
    try:
        percent = round(int(used) * 100 / int(window))
    except (TypeError, ValueError, ZeroDivisionError):
        return f"{used_text} / {window_text}"
    return f"剩余 {max(0, 100 - percent)}% ({used_text} / {window_text})"


def token_usage(value):
    total = value.get("total", {}) if isinstance(value, dict) else {}
    if not isinstance(total, dict) or total.get("totalTokens") is None:
        return VALUE_UNAVAILABLE
    total_count = token_count(total.get("totalTokens"))
    input_count = token_count(total.get("inputTokens"))
    output_count = token_count(total.get("outputTokens"))
    return f"{total_count} total\n       {input_count} input + {output_count} output"


def limit_duration(minutes):
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ""
    if minutes <= 0:
        return ""
    if minutes % 1440 == 0:
        return f"{minutes // 1440}天"
    if minutes % 60 == 0:
        return f"{minutes // 60}小时"
    return f"{minutes}分钟"


def limit_window(value, fallback):
    if not isinstance(value, dict):
        return ""
    try:
        used = min(100, max(0, int(value.get("usedPercent"))))
    except (TypeError, ValueError):
        return ""
    label = limit_duration(value.get("windowDurationMins")) or fallback
    text = f"{label}剩余 {100 - used}%"
    reset = value.get("resetsAt")
    if type(reset) in (int, float) and reset > 0:
        try:
            local_reset = datetime.fromtimestamp(reset).strftime("%m-%d %H:%M")
        except (ValueError, OverflowError, OSError):
            pass
        else:
            text += f" ({local_reset} 重置)"
    return text


def rate_limit_label(value):
    if not isinstance(value, dict):
        return VALUE_UNAVAILABLE
    snapshot = value.get("rateLimits", value)
    if not isinstance(snapshot, dict):
        return VALUE_UNAVAILABLE

    parts = []
    if value.get("ordinaryUsageAllowed") is False:
        parts.append("当前受限")
    if snapshot.get("rateLimitReachedType"):
        parts.append("已达上限")
    for key, fallback in (("primary", "主限额"), ("secondary", "次限额")):
        label = limit_window(snapshot.get(key), fallback)
        if label:
            parts.append(label)

    credits = snapshot.get("credits")
    if isinstance(credits, dict):
        if credits.get("unlimited"):
            parts.append("余额无限")
        elif credits.get("balance") is not None:
            parts.append(f"余额 {compact_label(credits.get('balance'))}")
        elif credits.get("hasCredits"):
            parts.append("余额可用")
        elif credits.get("hasCredits") is False:
            parts.append("余额 0")

    reset_credits = value.get("rateLimitResetCredits")
    if isinstance(reset_credits, dict):
        try:
            count = int(reset_credits.get("availableCount"))
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            parts.append(f"可重置 {count} 次")

    return "  ".join(parts) or VALUE_UNAVAILABLE


def session_name(value):
    return str(value or "未命名").replace("\n", " ").strip()[:100] or "未命名"


def short_session_id(value):
    return str(value or "")[:8] or "-"


def panel(*lines):
    text = "\n".join(line for line in lines if line is not None)
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return f"{fence}\n{text}\n{fence}"


def session_summary(action, *, name, model, effort, cwd, backend=None, status=None,
                    session_id=None, text=prompt_text):
    return action + "\n" + panel(f"{text('LABEL_BACKEND')}: {backend}" if backend else None,
                 f"{text('LABEL_SESSION')}: {session_name(name)}",
                 f"{text('LABEL_SESSION_ID')}: {short_session_id(session_id)}",
                 f"{text('LABEL_STATUS')}: {status}" if status else None,
                 f"{text('LABEL_MODEL')}: {model_label(model, effort)}",
                 f"{text('LABEL_CWD')}: {display_path(cwd)}")


def setting_summary(action=None, *, model, effort, cwd, field=None, text=prompt_text):
    # Display only effective values. Never compare with previous state.
    if field == "effort":
        return f"{text('LABEL_EFFORT')}: {effort_label(effort)}"
    if field == "cwd":
        return f"{text('LABEL_CWD')}: {display_path(cwd)}"
    if field == "model":
        return f"{text('LABEL_MODEL')}: {model_label(model, effort)}"
    return panel(f"{text('LABEL_MODEL')}: {model_label(model, effort)}",
                 f"{text('LABEL_CWD')}: {display_path(cwd)}")


def status_fields(*, status, queued, pending, failed, name, model, effort,
                   cwd, connection, provider=None, approval=None,
                   sandbox=None, reviewer=None, collaboration=None,
                   profile=None, usage=None, rate_limits=None, backend=None,
                   shared_fallback=None, session_id=None):
    total = (usage or {}).get("total") or {}
    last = (usage or {}).get("last") or {}
    context = context_usage(usage)
    remaining = re.match(r"剩余 (\d+%)", context)
    return {
        "status": status, "backend": backend, "session": session_name(name),
        "session_id": short_session_id(session_id),
        "model": model or "默认模型", "effort": effort_label(effort),
        "cwd": display_path(cwd), "provider": provider_label(provider),
        "permissions": permission_label(sandbox=sandbox, approval=approval, reviewer=reviewer, profile=profile),
        "collaboration": collaboration_label(collaboration), "connection": connection,
        "shared_fallback": shared_fallback,
        "queue_depth": queued, "pending_requests": pending, "send_failures": failed,
        "context_usage": context, "context_remaining": remaining[1] if remaining else VALUE_UNAVAILABLE,
        "total_tokens": token_count(total.get("totalTokens")),
        "input_tokens": token_count(total.get("inputTokens")),
        "output_tokens": token_count(total.get("outputTokens")),
        "turn_total_tokens": token_count(last.get("totalTokens")),
        "turn_input_tokens": token_count(last.get("inputTokens")),
        "turn_output_tokens": token_count(last.get("outputTokens")),
        "token_usage": token_usage(usage), "rate_limits": rate_limit_label(rate_limits),
    }


def status_from_fields(values, *, text=prompt_text):
    def label(key):
        return text(_FIELD_LABELS[key])
    counts = "  ".join(f"{label(key)}: {values[key]}" for key in
                       ("queue_depth", "pending_requests", "send_failures")
                       if values[key])
    lines = [f"{label('status')}: {values['status']}", counts or None]
    if values["backend"]:
        lines.append(f"{label('backend')}: {values['backend']}")
    lines.extend([f"{label('model')}: {values['model']} ({values['effort']})"])
    lines.extend(f"{label(key)}: {values[key]}" for key in
                 ("provider", "cwd", "permissions", "collaboration", "session",
                  "session_id", "connection"))
    if values.get("shared_fallback"):
        lines.append(f"{label('shared_fallback')}: {values['shared_fallback']}")
    lines.append("")
    lines.extend(f"{label(key)}: {values[key]}" for key in ("token_usage", "context_usage", "rate_limits"))
    return panel(*lines)


def status_summary(**kwargs):
    return status_from_fields(status_fields(**kwargs))


def settings_summary(*, notice, attachment_mib, slicing=None, notifications=None,
                     completion_notice="", source_hint=None, autostart=None, shared=None,
                     connection_idle_seconds=None, text=prompt_text):
    def display_template(value):
        value = str(value or VALUE_EMPTY)
        if len(value) >= 2 and value[0] == value[-1] == "`":
            return value[1:-1]
        return value

    editable = [f"ack: {display_template(notice)}",
                f"finish: {display_template(completion_notice)}"]
    if autostart is not None:
        editable.append(f"autostart: {autostart}")
    if shared is not None:
        editable.append(f"shared: {shared}")
    if notifications is not None:
        editable.append(f"compactionNotice: {'true' if notifications.compaction else 'false'}")
    if connection_idle_seconds is not None:
        editable.append(f"connectionIdleSeconds: {connection_idle_seconds}")
    editable.append(f"media.maxMiB: {attachment_mib}")
    if slicing is not None:
        editable.append(f"slicing.smart: {'true' if slicing.mode == 'smart' else 'false'}")
        editable.append(f"slicing.targetChars: {slicing.target_chars}")
    if source_hint is not None:
        editable.append(f"sourceHint: {source_hint or VALUE_EMPTY}")
    readonly = []
    if notifications is not None:
        for window in ("primary", "secondary"):
            values = notifications.quota_thresholds.get(window, ())
            summary = "[" + ",".join(str(value) for value in values) + "]"
            readonly.append(f"quotaWarningThresholds.{window}: {summary}")
    if readonly:
        editable.extend(["[无法直接修改]", *readonly])
    return panel(*editable) + "\n\n" + text("SETTINGS_HINT")


def session_age(timestamp, *, now=None):
    try:
        then = datetime.fromtimestamp(timestamp).astimezone()
    except (OSError, OverflowError, TypeError, ValueError):
        return ""
    now = now or datetime.now().astimezone()
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if then.date() == now.date():
        return f"今天 {then:%H:%M}"
    if (now.date() - then.date()).days == 1:
        return "昨天"
    if then.year == now.year:
        return f"{then.month}月{then.day}日"
    return f"{then.year}年{then.month}月{then.day}日"


def command_approval(*, command, cwd, reason, token, text=prompt_text):
    return text('APPROVAL_COMMAND_TITLE') + "\n" + panel(
        f"{text('LABEL_COMMAND')}: {command}", f"{text('LABEL_CWD')}: {display_path(cwd)}",
        f"{text('LABEL_REASON')}: {reason}" if reason else None) + approval_actions(token, text=text)


def file_approval(*, scope, reason, token, text=prompt_text):
    return text('APPROVAL_FILE_TITLE') + "\n" + panel(f"{text('LABEL_SCOPE')}: {scope}",
        f"{text('LABEL_REASON')}: {reason}" if reason else None) + approval_actions(token, text=text)


def approval_actions(token, *, text=prompt_text):
    return f"\n`/approve {token}` {text('ACTION_APPROVE')}\n`/deny {token}` {text('ACTION_DENY')}"


def ping_channel(fields, *, text=prompt_text):
    """Keep the compact pair; missing samples never acquire a time unit."""
    inbound, outbound = fields["ingress_ms"], fields["egress_ms"]
    if inbound != "-" and outbound != "-":
        return text("PING_REPLY", fields)
    return text("PING_PARTIAL", {"latency": "/".join(value + "ms" if value != "-" else "-"
                                                   for value in (inbound, outbound))})
