"""Single source for user-visible copy and presentation formats."""
from datetime import datetime
import os
import re


MAX_NOTICE_BYTES = 1000
COMPLETION_NOTICE = ""

BACKEND_SUMMARY = "```\n后端: {当前}\n可用: {可用}\n```\n`/backend {{名称}}`"
EFFORT_SUMMARY = "```\n强度: {当前}\n可选: {可选}\n```\n`/effort {{档位}}`"
ACK_VIEW = "```\n处理提示: {模板}\n```\n`/ack text {{模板}}`\n`/ack text` 关闭\n`/ack fields` 可用字段"
ACK_USAGE = "`/ack text {模板}`\n`/ack text` 关闭\n`/ack fields` 可用字段"
ACK_TOO_LONG = "未更新处理提示: 最多 {字节} 字节"
WORKING_DIRECTORY = "目录: {目录}"
MEDIA_USAGE = "`/send {路径}`\n`/sendfile {路径}`\n`/sendimage {路径}`"

DESKTOP_NOTE = "补充消息会进入这个任务；`/stop` 也会停止它。"
BLOCKED_NOTE = "等待桌面处理"

BUSY_CHANGE = "未切换: 任务未结束。等待完成或 `/stop`。"
BUSY_RESTART = "有尚未完成的任务\n使用 `/stop` 终止后重试。"
RESTART_STARTED = "正在重启消息桥..."
RESTART_READY = "消息桥已重启。"
RESTART_CONFIRM_UNSENT = "确认消息未能及时发送。"
RESTART_FAILED = "重启未完成: {原因}"
RESTART_NOT_SERVICE = "当前消息桥不是由用户服务启动。"

STOP_REQUESTED = "已请求停止"
STOP_DEFERRED = "已请求停止，准备结束后生效"
STOP_IDLE = "当前无运行任务"
STOP_UNCONFIRMED = "停止未确认，任务可能仍在运行。请在桌面确认。"

BACKEND_CONNECT_FAILED = "后端连接失败: {原因}\n正在重连，未提交的待办已保留。"
SESSION_RESTORE_FAILED = (
    "会话恢复失败: {原因}\n"
    "使用 `/backend` 切换后端，或稍后重试。"
)
TASK_FAILED = "消息未提交: 后端处理失败。查看 `/status`。"
TURN_FAILED = "任务未完成: Agent 执行失败。"
TURN_INTERRUPTED = "任务已中断。"
TURN_ENDED_UNEXPECTEDLY = "任务未正常完成。\n使用 `/status` 查看状态。"
TURN_COMPLETED_EMPTY = "任务已完成，但没有文本结果。"
INTERRUPTED_TASK = (
    "上次任务在等待结果时中断。\n"
    "没有自动重放，电脑端可能仍在执行。\n"
    "请使用 `/status` 确认。"
)
UNCERTAIN_STEER = "补充消息提交未确认，未重发。请在桌面查看。"
QUEUED_STEER = "已排队，当前任务结束后处理。"
PING_REPLY = "WeChat ─{发送延迟}/{接收延迟}ms"
PING_USAGE = "用法: `/ping`"
UNKNOWN_COMMAND = "未知指令，查看 `/help`"
TOO_MANY_ATTACHMENTS = "消息未接收: 附件超过 {数量} 个。\n请分开发送。"
QUEUE_FULL = "拒绝接收: 排队消息已达 {数量} 条。\n请等待完成或使用 `/stop`。"
UNSUPPORTED_MESSAGE = (
    "消息未接收: 仅支持文字、图片和文件。\n"
    "文字每条最多 {数量} 字符。"
)

ATTACHMENT_INCOMING_FAILED = (
    "附件处理失败: {原因}\n"
    "这条消息未交给 Agent。"
)
ATTACHMENT_QUEUED = "附件已排队"
ATTACHMENT_SEND_FAILED = (
    "附件未发出: {名称}\n"
    "重试 {次数} 次仍失败，请稍后用 `/send` 重试。"
)
AUTO_ATTACHMENT_LIMIT = "附件未发送: 自动回传最多 {数量} 个，超出的已跳过。"
AUTO_ATTACHMENT_FAILED = "附件未发送: {原因}{建议}"
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
    "/model": "未切换模型",
    "/models": "未切换模型",
    "/effort": "未切换强度",
    "/cwd": "未切换目录",
    "/ack": "未更新处理提示",
}
COMMAND_FAILURE = "{动作}: {原因}"

SETTINGS_USAGE = "用法: `/settings`"
BACKEND_INVALID = "未切换后端: 名称无效\n可用: {可用}"
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
EFFORT_INVALID = "未切换强度: 可选 {可选}"
DIRECTORY_INVALID = "未切换目录: 目录不存在或无法访问\n请检查路径后重试。"
DIRECTORY_NOT_FOLDER = "未切换目录: 路径不是文件夹\n请选择文件夹。"
RESTART_USAGE = "用法: `/restart`"


def fill(template, values):
    """Fill named prompt fields without evaluating arbitrary expressions."""
    return template.format_map(values)


def render_ack(template, values):
    """Render only the documented channel-delay placeholders."""
    # One pass prevents a field value containing another placeholder from
    # triggering a second expansion. Unknown fields remain literal text.
    return re.sub(r"\{([^{}]+)\}",
                  lambda match: ("-" if values.get(match[1], match[0]) in (None, "暂不可用")
                                 else str(values.get(match[1], match[0]))), template)


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
    return "{累计} total\n       {输入} input + {输出} output".format(
        累计=token_count(total.get("totalTokens")),
        输入=token_count(total.get("inputTokens")),
        输出=token_count(total.get("outputTokens")),
    )


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
    return f"{label}剩余 {100 - used}%"


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


def session_summary(action, *, name, model, effort, cwd, backend=None):
    return action + "\n" + panel(f"后端: {backend}" if backend else None,
                 f"会话: {session_name(name)}", f"模型: {model_label(model, effort)}",
                 f"目录: {display_path(cwd)}")


def setting_summary(action=None, *, model, effort, cwd, field=None):
    # Display only effective values. Never compare with previous state.
    if field == "effort":
        return f"强度: {effort_label(effort)}"
    if field == "cwd":
        return f"目录: {display_path(cwd)}"
    if field == "model":
        return f"模型: {model_label(model, effort)}"
    return panel(f"模型: {model_label(model, effort)}", f"目录: {display_path(cwd)}")


def status_fields(*, status, queued, pending, failed, name, model, effort,
                   cwd, connection, provider=None, approval=None,
                   sandbox=None, reviewer=None, collaboration=None,
                   profile=None, usage=None, rate_limits=None, backend=None):
    total = (usage or {}).get("total") or {}
    context = context_usage(usage)
    remaining = re.match(r"剩余 (\d+%)", context)
    return {
        "状态": status, "后端": backend, "会话": session_name(name),
        "模型": model or "默认模型", "强度": effort_label(effort),
        "目录": display_path(cwd), "提供方": provider_label(provider),
        "权限": permission_label(sandbox=sandbox, approval=approval, reviewer=reviewer, profile=profile),
        "协作": collaboration_label(collaboration), "服务": connection,
        "待办数": queued, "审批数": pending, "发送失败数": failed,
        "上下文": context, "上下文剩余": remaining[1] if remaining else "暂不可用",
        "累计Token": token_count(total.get("totalTokens")),
        "输入Token": token_count(total.get("inputTokens")),
        "输出Token": token_count(total.get("outputTokens")),
        "Token": token_usage(usage), "限额": rate_limit_label(rate_limits),
    }


def status_from_fields(values):
    counts = "  ".join(f"{label}: {values[key]}" for label, key in
                       (("待办", "待办数"), ("审批", "审批数"), ("发送失败", "发送失败数"))
                       if values[key])
    lines = [f"状态: {values['状态']}", counts or None]
    if values["后端"]:
        lines.append(f"后端: {values['后端']}")
    lines.extend([f"模型: {values['模型']} ({values['强度']})"])
    lines.extend(f"{label}: {values[label]}" for label in
                 ("提供方", "目录", "权限", "协作", "会话", "服务"))
    lines.append("")
    lines.extend(f"{label}: {values[label]}" for label in ("Token", "上下文", "限额"))
    return panel(*lines)


def status_summary(**kwargs):
    return status_from_fields(status_fields(**kwargs))


def settings_summary(*, notice, attachment_mib, slicing=None):
    lines = [f"处理提示: {notice or '关闭'}", f"完成提示: {COMPLETION_NOTICE or '关闭'}",
             f"附件上限: {attachment_mib} MiB"]
    if slicing is not None:
        lines.extend([f"切片: {slicing.mode}", f"目标长度: {slicing.target_chars} 字符"])
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


def command_approval(*, command, cwd, reason, token):
    return "审批: 执行命令\n" + panel(f"命令: {command}", f"目录: {display_path(cwd)}",
                 f"原因: {reason}" if reason else None) + approval_actions(token)


def file_approval(*, scope, reason, token):
    return "审批: 修改文件\n" + panel(f"范围: {scope}",
                 f"原因: {reason}" if reason else None) + approval_actions(token)


def approval_actions(token):
    return f"\n`/approve {token}` 同意\n`/deny {token}` 拒绝"
