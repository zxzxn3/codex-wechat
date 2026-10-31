"""Single source for user-visible copy and presentation formats."""
from datetime import datetime


DEFAULT_ACK_TEMPLATE = "{发送延迟}/{接收延迟}ms"
LEGACY_ACK_TEMPLATE = "{latency}"
MAX_NOTICE_BYTES = 1000

HELP = "\n".join((
    "`/status` 当前状态",
    "`/settings` 当前设置",
    "`/sessions` 会话",
    "`/new` 新建",
    "`/stop` 停止",
    "`/ping` 消息通道延迟",
    "`/switch {编号或ID}`",
    "`/backend {名称}`",
    "`/model {编号或名称}`",
    "`/effort {档位}`",
    "`/cwd {路径}`",
    "`/ack` 处理提示",
    "`/restart` 重启消息桥",
))

BACKEND_SUMMARY = (
    "当前后端：{当前}\n"
    "可用：{可用}\n"
    "切换：`/backend {目标}`"
)
MODEL_SUMMARY = "可用模型\n{列表}\n\n切换：`/model {目标}`"
EFFORT_SUMMARY = (
    "当前强度：{当前}\n"
    "可选：{可选}\n"
    "切换：`/effort {目标}`"
)
ACK_VIEW = (
    "处理提示：{模板}\n"
    "修改：`/ack text {{模板}}`\n"
    "关闭：`/ack text`"
)
ACK_USAGE = "用法：`/ack text {模板}`\n关闭：`/ack text`"
ACK_TOO_LONG = "未更新处理提示：模板最多 {字节} 字节"
NO_NEXT_PAGE = "没有下一页\n请先发送 `/sessions`。"
WORKING_DIRECTORY = "工作目录：{目录}"
MEDIA_USAGE = (
    "用法：\n\n"
    "`/send {路径}`\n"
    "`/sendfile {路径}`\n"
    "`/sendimage {路径}`"
)
SESSIONS_TABLE_HEADER = "| # | 会话 | 状态 |\n|:--:|:--|:--|"

SESSION_SUMMARY = (
    "{动作}\n"
    "会话：{会话}\n"
    "模型：{模型}\n"
    "目录：{目录}"
)
SETTING_SUMMARY = (
    "{动作}\n"
    "模型：{模型}\n"
    "目录：{目录}"
)
STATUS_SUMMARY = (
    "{状态}\n"
    "排队消息 {排队} | 待审批 {审批} | 发送失败 {发送}\n\n"
    "会话：{会话}\n"
    "模型：{模型}\n"
    "目录：{目录}\n"
    "连接：{连接}\n"
    "收信：{收信}"
)
SETTINGS_SUMMARY = (
    "会话：{会话}\n"
    "模型：{模型}\n"
    "目录：{目录}\n"
    "收信：{收信}\n"
    "处理提示：{处理提示}\n"
    "附件上限：{附件上限} MiB"
)

DESKTOP_NOTE = "补充消息会进入这个任务；`/stop` 也会停止它。"
BLOCKED_NOTE = "当前任务正在等待电脑端处理。\n请先在桌面继续。"

BUSY_CHANGE = "拒绝切换：有尚未完成的任务，使用 `/stop` 终止后重试。"
BUSY_RESTART = "有尚未完成的任务\n使用 `/stop` 终止后重试。"
RESTART_STARTED = "正在重启消息桥。"
RESTART_READY = "消息桥已重启。"
RESTART_CONFIRM_UNSENT = "确认消息未能及时发送。"
RESTART_FAILED = "重启未完成：{原因}\n请稍后重试。"
RESTART_NOT_SERVICE = "当前消息桥不是由用户服务启动。"

STOP_REQUESTED = "已请求停止，待办已清空。"
STOP_DEFERRED = "已请求停止\n当前准备步骤结束后生效，待办已清空。"
STOP_IDLE = "当前没有正在执行的任务，待办已清空。"
STOP_UNCONFIRMED = "停止未确认\n待办已清空，但当前任务可能仍在运行。\n请到电脑端确认。"

BACKEND_CONNECT_FAILED = (
    "后端连接失败：{原因}\n"
    "使用 `/backend` 切换后端，或稍后重试。"
)
SESSION_RESTORE_FAILED = (
    "会话恢复失败：{原因}\n"
    "使用 `/backend` 切换后端，或稍后重试。"
)
TASK_FAILED = (
    "任务未完成：后端处理异常\n"
    "可用 `/status` 查看状态，或 `/backend` 切换后端重试。"
)
TURN_FAILED = "任务未完成：Agent 执行失败。"
TURN_INTERRUPTED = "任务已中断。"
TURN_ENDED_UNEXPECTEDLY = "任务未正常完成。\n使用 `/status` 查看状态。"
TURN_COMPLETED_EMPTY = "任务已完成，但没有文本结果。"
INTERRUPTED_TASK = (
    "上次任务在等待结果时中断。\n"
    "没有自动重放，电脑端可能仍在执行。\n"
    "请使用 `/status` 确认。"
)
UNCERTAIN_STEER = (
    "补充消息可能已送达，但结果未确认。\n"
    "没有自动重发，请使用 `/status` 查看进度。"
)
QUEUED_STEER = (
    "当前任务暂不接受补充消息\n"
    "消息已排队，完成后继续处理。"
)
PING_REPLY = "pong. {发送延迟}/{接收延迟}ms"
PING_USAGE = "用法：`/ping`\n仅测试微信通道，不调用模型。"
UNKNOWN_COMMAND = "命令无效\n发送 `/help` 查看命令。"
TOO_MANY_ATTACHMENTS = "消息未接收：附件超过 {数量} 个。\n请分开发送。"
QUEUE_FULL = "拒绝接收：排队消息已达 {数量} 条。\n请等待完成或使用 `/stop`。"
UNSUPPORTED_MESSAGE = (
    "消息未接收：仅支持文字、图片和文件。\n"
    "文字每条最多 {数量} 字符。"
)

ATTACHMENT_INCOMING_FAILED = (
    "附件处理失败：{原因}\n"
    "这条消息未交给 Agent。"
)
ATTACHMENT_QUEUED = "附件已加入发送队列\n失败时会在此提示。"
ATTACHMENT_SEND_FAILED = (
    "附件未发出：{名称}\n"
    "重试 {次数} 次仍失败，请稍后用 `/send` 重试。"
)
AUTO_ATTACHMENT_LIMIT = "附件未发送：自动回传最多 {数量} 个，超出的已跳过。"
AUTO_ATTACHMENT_FAILED = "附件未发送：{原因}{建议}"
AUTO_ATTACHMENT_PATH_HINT = "\n其他位置请使用 `/send {路径}`。"

COMMAND_APPROVAL = (
    "需要你的决定：执行命令\n"
    "命令：{命令}\n"
    "目录：{目录}\n"
    "原因：{原因}\n\n"
    "同意：`/approve {编号}`\n"
    "拒绝：`/deny {编号}`"
)
FILE_APPROVAL = (
    "需要你的决定：修改文件\n"
    "范围：{范围}\n"
    "原因：{原因}\n\n"
    "同意：`/approve {编号}`\n"
    "拒绝：`/deny {编号}`"
)
APPROVAL_ACCEPTED = "已同意，等待 Agent 继续。"
APPROVAL_DECLINED = "已拒绝。"
APPROVAL_EXPIRED = "审批已失效\n可能已处理或超时，请在电脑端确认。"
APPROVAL_FAILED = "审批结果未送达\n请在电脑端处理。"
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
COMMAND_FAILURE = "{动作}：{原因}\n请稍后重试。"

SETTINGS_USAGE = "用法：`/settings`"
BACKEND_INVALID = "未切换后端：名称无效\n可用：{可用}"
BACKEND_SESSION_BUSY = (
    "未切换后端：目标后端的上次会话正在工作或等待处理。\n"
    "请先在电脑端处理。"
)
BACKEND_CLEANUP_FAILED = (
    "已切换后端，当前选择已保存。\n"
    "旧连接未清理，请在电脑端检查。"
)
SESSION_TARGET_BLOCKED = (
    "未切换会话：目标会话需要先在电脑端处理。\n"
    "请完成后再重试。"
)
SESSION_TARGET_ACTIVE = (
    "未切换会话：工作中的会话只能通过桌面共享连接。\n"
    "请切换后端或等待任务结束。"
)
SESSION_INDEX_INVALID = "未切换会话：编号无效\n请先发送 `/sessions`。"
SESSION_USAGE = "用法：`/switch {编号或ID}`"
NEW_USAGE = "用法：`/new`"
MODEL_INVALID = "未切换模型：名称无效\n请使用 `/model` 查看可用模型。"
MODEL_NOT_IN_CATALOG = "未切换强度：当前模型不在模型列表中。"
EFFORT_INVALID = "未切换强度：可选 {可选}"
DIRECTORY_INVALID = "未切换目录：目录不存在或无法访问\n请检查路径后重试。"
DIRECTORY_NOT_FOLDER = "未切换目录：路径不是文件夹\n请选择文件夹。"
RESTART_USAGE = "用法：`/restart`"


class UserFacingError(ValueError):
    """An already complete WeChat-facing error message."""


def fill(template, values):
    """Fill named prompt fields without evaluating arbitrary expressions."""
    return template.format_map(values)


def render_ack(template, values):
    """Render only the documented channel-delay placeholders."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def processing_template(config, state):
    """Resolve config/state notice text; an empty text disables the notice."""
    text = DEFAULT_ACK_TEMPLATE
    for source in (config.get("processingNotice"), state.get("processingNotice")):
        if isinstance(source, str):
            text = source
        elif isinstance(source, dict) and source.get("text") is not None:
            text = source["text"]
    if text == LEGACY_ACK_TEMPLATE:
        return DEFAULT_ACK_TEMPLATE
    return str(text)


def effort_label(value):
    return str(value).replace("_", " ").capitalize() if value else "默认"


def model_label(model, effort):
    model = model or "默认模型"
    return f"{model} {effort_label(effort)}"


def backend_reason(error):
    text = str(error).casefold()
    if "超时" in text or "timeout" in text:
        return "连接超时"
    if any(token in text for token in ("启动器", "no such file", "filenotfound")):
        return "启动器未就绪"
    if "systemd" in text or "用户服务" in text:
        return "用户服务不可用"
    if any(token in text for token in
           ("websocket", "共享连接", "连接断开", "连接失败", "后端已退出")):
        return "后端连接中断"
    if "rpc" in text:
        return "后端拒绝了请求"
    if "依赖" in text or "websockets" in text:
        return "后端组件不可用"
    if "command" in text or "配置" in text:
        return "后端配置无效"
    return "服务暂不可用"


def attachment_reason(error):
    text = str(error).casefold()
    if "超过" in text or "大小限制" in text:
        return "文件超过大小限制"
    if "图片仅支持" in text:
        return "图片格式不支持"
    if "凭据或运行状态" in text:
        return "不允许发送消息桥私有状态"
    if "自动回传仅限" in text:
        return "只能自动发送当前会话目录内的文件"
    if "不是普通文件" in text:
        return "附件不是普通文件"
    if any(token in text for token in
           ("不存在", "无法读取", "permission denied", "permissionerror")):
        return "文件不存在或不可读"
    if any(token in text for token in ("密钥", "密文", "md5", "校验失败")):
        return "附件校验失败"
    if any(token in text for token in
           ("cdn", "curl", "http", "上传", "未返回附件")):
        return "微信附件服务未响应"
    if "cryptography" in text or "依赖" in text:
        return "附件组件不可用"
    if any(token in text for token in ("未知附件类型", "下载引用", "缺少")):
        return "附件格式不支持"
    if "最多" in text:
        return "附件数量超过限制"
    return "附件处理失败"


def session_name(value):
    return str(value or "未命名").replace("\n", " ").strip()[:100] or "未命名"


def session_summary(action, *, name, model, effort, cwd):
    return fill(SESSION_SUMMARY, {
        "动作": action,
        "会话": session_name(name),
        "模型": model_label(model, effort),
        "目录": cwd,
    })


def setting_summary(action, *, model, effort, cwd):
    return fill(SETTING_SUMMARY, {
        "动作": action,
        "模型": model_label(model, effort),
        "目录": cwd,
    })


def status_summary(*, status, queued, pending, failed, name, model, effort,
                   cwd, connection, input_mode):
    return fill(STATUS_SUMMARY, {
        "状态": status,
        "排队": queued,
        "审批": pending,
        "发送": failed,
        "会话": session_name(name),
        "模型": model_label(model, effort),
        "目录": cwd,
        "连接": connection,
        "收信": input_mode,
    })


def settings_summary(*, name, model, effort, cwd, input_mode, notice, attachment_mib):
    return fill(SETTINGS_SUMMARY, {
        "会话": session_name(name),
        "模型": model_label(model, effort),
        "目录": cwd,
        "收信": input_mode,
        "处理提示": notice or "关闭",
        "附件上限": attachment_mib,
    })


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
    return fill(COMMAND_APPROVAL, {
        "命令": command,
        "目录": cwd,
        "原因": reason or "未说明",
        "编号": token,
    })


def file_approval(*, scope, reason, token):
    return fill(FILE_APPROVAL, {
        "范围": scope,
        "原因": reason or "未说明",
        "编号": token,
    })
