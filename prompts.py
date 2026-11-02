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
ACK_VIEW = "```\n接收提示: {template}\n```\n`/ack text {{模板}}`\n`/ack text` 关闭\n`/ack fields` 可用字段"
ACK_USAGE = "`/ack text {模板}`\n`/ack text` 关闭\n`/ack fields` 可用字段"
ACK_TOO_LONG = "未更新接收提示: 最多 {max_bytes} 字节"
WORKING_DIRECTORY = "目录: {cwd}"
MEDIA_USAGE = "`/send {路径}`\n`/sendfile {路径}`\n`/sendimage {路径}`\n`/sendvideo {路径}`"

BLOCKED_NOTE = "等待桌面处理"

BUSY_CHANGE = "未切换: 任务未结束。等待完成或 `/stop`。"
BUSY_RESTART = "有尚未完成的任务\n使用 `/stop` 终止后重试。"
RESTART_STARTED = "正在重启微信桥..."
RESTART_READY = "微信桥已重启。"
SERVICE_STOPPING = "微信桥正在停止。"
RESTART_CONFIRM_UNSENT = "确认消息未能及时发送。"
RESTART_FAILED = "重启未完成: {reason}"
RESTART_NOT_SERVICE = "当前微信桥不是由用户服务启动。"

STOP_REQUESTED = "已请求停止"
STOP_DEFERRED = "已请求停止，准备结束后生效"
STOP_IDLE = "当前无运行任务"
STOP_UNCONFIRMED = "停止未确认，任务可能仍在运行。请在桌面确认。"

BACKEND_CONNECT_FAILED = "后端连接失败: {reason}\n正在重连，未提交的待办已保留。"
TASK_FAILED = "消息未提交: 后端处理失败。查看 `/status`。"
TURN_FAILED = "任务未完成: Agent 执行失败。"
TURN_INTERRUPTED = "任务已中断。"
TURN_ENDED_UNEXPECTEDLY = "任务未正常完成。\n使用 `/status` 查看状态。"
TURN_COMPLETED_EMPTY = "任务已完成，但没有文本结果。"
UNCERTAIN_STEER = "补充消息提交未确认，未重发。请在桌面查看。"
QUEUED_STEER = "暂时无法追加，已转入排队。"
QUEUE_CONFIRMED = "Queue {count} {unit}"
UNQUEUE_CANCELLED = "已撤回。Queue {count} {unit}"
UNQUEUE_EMPTY = "没有可撤回的排队消息。"
PING_REPLY = "WeChat ─{ingress_ms}/{egress_ms}ms"
PING_PARTIAL = "WeChat ─{latency}"
PING_ROUTE = "{channel}─ Bridge ─{provider_latency}─ Provider"
PING_USAGE = "用法: `/ping`"
UNKNOWN_COMMAND = "未知指令，查看 `/help`"
TOO_MANY_ATTACHMENTS = "消息未接收: 附件超过 {count} 个。\n请分开发送。"
QUEUE_FULL = "拒绝接收: 排队消息已达 {count} 条。\n请等待完成或用 `/unqueue` 撤回最后一条。"
CONFIG_RELOAD_FAILED = "热配置未生效，保留原设置。请检查配置文件。"
UNSUPPORTED_MESSAGE = (
    "消息未接收: 仅支持文字、语音转写、图片、视频和文件。\n"
    "文字每条最多 {count} 字符。"
)

ATTACHMENT_INCOMING_FAILED = (
    "附件处理失败: {reason}\n"
    "这条消息未交给 Agent。"
)
ATTACHMENT_QUEUED = "附件已排队"
ATTACHMENT_SEND_FAILED = (
    "附件未发出: {name}\n"
    "重试 {attempts} 次仍失败，请稍后用 `/send` 重试。"
)
AUTO_ATTACHMENT_LIMIT = "附件未发送: 自动回传最多 {count} 个，超出的已跳过。"
AUTO_ATTACHMENT_FAILED = "附件未发送: {reason}{hint}"
AUTO_ATTACHMENT_PATH_HINT = "\n其他位置请使用 `/send {路径}`。"

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
    "/settings": "读取设置失败",
    "/backend": "切换后端失败",
    "/sessions": "读取会话失败",
    "/switch": "未切换会话",
    "/new": "未新建会话",
    "/compact": "压缩请求未确认，请先查看 `/status`，勿重复提交",
    "/model": "未切换模型",
    "/effort": "未切换强度",
    "/cwd": "未切换目录",
    "/ack": "未更新接收提示",
    "/finish": "未更新结束提示",
}
COMMAND_FAILURE = "{action}: {reason}"

SETTINGS_USAGE = "用法: `/settings [autostart on|off]`"
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
SESSION_INDEX_INVALID = "未切换会话: 编号无效\n请先发送 `/sessions`。"
SESSION_USAGE = "用法: `/switch {编号或ID}`"
NEW_USAGE = "用法: `/new`"
MODEL_INVALID = "未切换模型: 名称无效\n请使用 `/model` 查看可用模型。"
MODEL_NOT_IN_CATALOG = "未切换强度: 当前模型不在模型列表中。"
EFFORT_INVALID = "未切换强度: 可选 {choices}"
DIRECTORY_INVALID = "未切换目录: 目录不存在或无法访问\n请检查路径后重试。"
DIRECTORY_NOT_FOLDER = "未切换目录: 路径不是文件夹\n请选择文件夹。"
RESTART_USAGE = "用法: `/restart`"

# All uppercase string constants in this module are overrideable presentation text.
# Protocol identifiers, command routing and user/model content do not belong here.
LABEL_STATUS = "状态"
LABEL_BACKEND = "后端"
LABEL_SESSION = "会话"
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
LABEL_ACK = "接收提示"
LABEL_FINISH = "结束提示"
LABEL_AUTOSTART = "自启"
LABEL_ATTACHMENTS = "附件上限"
LABEL_SLICING = "切片"
LABEL_TARGET_LENGTH = "目标长度"
LABEL_SOURCE = "来源说明"
LABEL_COMPACTION = "压缩提示"
LABEL_QUOTA_WARNING = "限额警告"
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
SESSION_LIST_FAILED = "会话列表读取未完成，请重试。"
COMPACT_BLOCKED = "暂不能压缩: 当前会话等待处理。"
COMPACT_REQUESTED = "已请求压缩上下文。"
AUTOSTART_FAILED = "自启设置未确认，请在电脑检查本项目的服务安装。"
RESTART_REJECTED = "拒绝重启: {reason}"
STOP_CLEARED = "\n已清空待办: {count}"
ANSWER_ACCEPTED = "已回答。"
ANSWER_EXPIRED = "问题已失效或编号无效。"
ANSWER_FAILED = "回答投递结果未确认，请查看桌面；未自动重发。"
SEND_FILE_FAILED = "附件未发送: {reason}\n请检查路径后重试。"
REPLY_SEND_FAILED = "这条回复未能完整发送，已停止其余部分。请在桌面查看。"
INPUT_RECOVERED = "上次有任务已提交或提交结果未确认，未自动重发。未提交的待办已保留。"
INPUT_RESULT_INTERRUPTED = "任务已提交，但结果接收中断，未重新执行。"
CONTROL_QUEUE_FULL = "指令等待过多，请稍后重试。"
CONTROL_TIMEOUT = "控制请求未确认，请查看 `/status`；不会自动重发。"
CONTROL_FAILED = "指令未完成，请查看 `/status` 后重试。"
GENERIC_FAILURE = "操作失败"
COMMAND_USAGE = "用法: `{usage}`"
AUTOSTART_CUSTOM_PATH = "自定义状态或配置路径不能交给默认服务，请自行管理服务。"
AUTOSTART_BUSY = "首次启用自启需要移交后台服务，请等任务结束或用 /stop 停止后再试。"
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
VALUE_UNAVAILABLE = "不可用"
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
FINISH_VIEW = ACK_VIEW.replace("/ack", "/finish").replace("接收提示", "结束提示")
FINISH_USAGE = ACK_USAGE.replace("/ack", "/finish")
FINISH_TOO_LONG = ACK_TOO_LONG.replace("接收提示", "结束提示")

HELP_STATUS = '当前状态'
HELP_SETTINGS = '查看设置或修改登录自启'
HELP_SESSIONS = '会话'
HELP_NEW = '新建'
HELP_COMPACT = '压缩当前会话上下文'
HELP_QUEUE = '排队到当前任务结束后执行'
HELP_UNQUEUE = '撤回最后一条尚未开始的排队消息'
HELP_STOP = '停止并清空待办'
HELP_PING = '微信与供应商网络延迟'
HELP_SWITCH = '切换会话'
HELP_BACKEND = '查看或切换后端'
HELP_MODEL = '查看或切换模型'
HELP_EFFORT = '查看或切换强度'
HELP_CWD = '查看或切换目录'
HELP_ACK = '接收提示；空模板关闭'
HELP_FINISH = '结束提示；空模板关闭'
HELP_APPROVE = '同意审批'
HELP_DENY = '拒绝审批'
HELP_ANSWER = '回答原生提问'
HELP_SEND = '回传文件、图片或视频'
HELP_SENDFILE = '作为文件回传'
HELP_SENDIMAGE = '作为图片回传'
HELP_SENDVIDEO = '作为视频回传'
HELP_RESTART = '重启微信桥'
HELP_HELP = '命令说明'

DEFAULT_PROMPTS = MappingProxyType({name: value for name, value in tuple(globals().items())
                                  if name.isupper() and isinstance(value, str)} |
    {"FAILURE_" + command[1:].upper(): message for command, message in COMMAND_FAILURE_LABELS.items()})
HELP_DESCRIPTIONS = {"/" + key[5:].lower(): value for key, value in DEFAULT_PROMPTS.items()
                     if key.startswith("HELP_")}
_FORMATTER = Formatter()

_FIELD_LABELS = {
    "status": "LABEL_STATUS", "backend": "LABEL_BACKEND", "session": "LABEL_SESSION",
    "model": "LABEL_MODEL", "effort": "LABEL_EFFORT", "cwd": "LABEL_CWD",
    "provider": "LABEL_PROVIDER", "permissions": "LABEL_PERMISSIONS", "collaboration": "LABEL_COLLABORATION",
    "connection": "LABEL_SERVICE", "token_usage": "LABEL_TOKENS", "context_usage": "LABEL_CONTEXT",
    "rate_limits": "LABEL_LIMITS", "queue_depth": "LABEL_QUEUED", "pending_requests": "LABEL_APPROVALS",
    "send_failures": "LABEL_SEND_FAILURES",
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
        return "-" if value in (None, "暂不可用") else str(value)
    return re.sub(r"\{([^{}]+)\}", replace, template)


def effort_label(value):
    return str(value).replace("_", " ").capitalize() if value else "默认"


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


def compact_label(value, fallback="暂不可用", limit=100):
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
    permission = permission or "暂不可用"

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
        return "暂不可用"
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
    if used_text == "暂不可用":
        return used_text
    window_text = token_count(window)
    if window_text == "暂不可用":
        return f"{used_text} Token"
    try:
        percent = round(int(used) * 100 / int(window))
    except (TypeError, ValueError, ZeroDivisionError):
        return f"{used_text} / {window_text}"
    return f"剩余 {max(0, 100 - percent)}% ({used_text} / {window_text})"


def token_usage(value):
    total = value.get("total", {}) if isinstance(value, dict) else {}
    if not isinstance(total, dict) or total.get("totalTokens") is None:
        return "暂不可用"
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
        return "暂不可用"
    snapshot = value.get("rateLimits", value)
    if not isinstance(snapshot, dict):
        return "暂不可用"

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

    return "  ".join(parts) or "暂不可用"


def session_name(value):
    return str(value or "未命名").replace("\n", " ").strip()[:100] or "未命名"


def panel(*lines):
    text = "\n".join(line for line in lines if line is not None)
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return f"{fence}\n{text}\n{fence}"


def session_summary(action, *, name, model, effort, cwd, backend=None, status=None,
                    text=prompt_text):
    return action + "\n" + panel(f"{text('LABEL_BACKEND')}: {backend}" if backend else None,
                 f"{text('LABEL_SESSION')}: {session_name(name)}",
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
                   profile=None, usage=None, rate_limits=None, backend=None):
    total = (usage or {}).get("total") or {}
    context = context_usage(usage)
    remaining = re.match(r"剩余 (\d+%)", context)
    return {
        "status": status, "backend": backend, "session": session_name(name),
        "model": model or "默认模型", "effort": effort_label(effort),
        "cwd": display_path(cwd), "provider": provider_label(provider),
        "permissions": permission_label(sandbox=sandbox, approval=approval, reviewer=reviewer, profile=profile),
        "collaboration": collaboration_label(collaboration), "connection": connection,
        "queue_depth": queued, "pending_requests": pending, "send_failures": failed,
        "context_usage": context, "context_remaining": remaining[1] if remaining else "暂不可用",
        "total_tokens": token_count(total.get("totalTokens")),
        "input_tokens": token_count(total.get("inputTokens")),
        "output_tokens": token_count(total.get("outputTokens")),
        "token_usage": token_usage(usage), "rate_limits": rate_limit_label(rate_limits),
    }


def status_from_fields(values, *, text=prompt_text):
    label = lambda key: text(_FIELD_LABELS[key])
    counts = "  ".join(f"{label(key)}: {values[key]}" for key in
                       ("queue_depth", "pending_requests", "send_failures")
                       if values[key])
    lines = [f"{label('status')}: {values['status']}", counts or None]
    if values["backend"]:
        lines.append(f"{label('backend')}: {values['backend']}")
    lines.extend([f"{label('model')}: {values['model']} ({values['effort']})"])
    lines.extend(f"{label(key)}: {values[key]}" for key in
                 ("provider", "cwd", "permissions", "collaboration", "session", "connection"))
    lines.append("")
    lines.extend(f"{label(key)}: {values[key]}" for key in ("token_usage", "context_usage", "rate_limits"))
    return panel(*lines)


def status_summary(**kwargs):
    return status_from_fields(status_fields(**kwargs))


def settings_summary(*, notice, attachment_mib, slicing=None, notifications=None,
                     completion_notice="", source_hint=None, autostart=None, text=prompt_text):
    off = text('VALUE_OFF')
    lines = [f"{text('LABEL_ACK')}: {notice or off}",
             f"{text('LABEL_FINISH')}: {completion_notice or off}",
             f"{text('LABEL_ATTACHMENTS')}: {attachment_mib} MiB"]
    if autostart is not None:
        lines.append(f"{text('LABEL_AUTOSTART')}: {autostart}")
    if slicing is not None:
        lines.extend([f"{text('LABEL_SLICING')}: {slicing.mode}",
                      f"{text('LABEL_TARGET_LENGTH')}: {slicing.target_chars} 字符"])
    if source_hint is not None:
        lines.append(f"{text('LABEL_SOURCE')}: {source_hint or off}")
    if notifications is not None:
        thresholds = "/".join(f"{value}%" for value in notifications.quota_thresholds) or off
        lines.extend([f"{text('LABEL_COMPACTION')}: {text('VALUE_ON') if notifications.compaction else off}",
                      f"{text('LABEL_QUOTA_WARNING')}: {thresholds}"])
    return panel(*lines)


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
