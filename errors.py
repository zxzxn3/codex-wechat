"""User-safe error classification; no protocol or UI state."""
from dataclasses import dataclass
from enum import Flag, auto


class ErrorAction(Flag):
    """Independent handling axes; an error may need more than one."""
    RETRYABLE = auto()
    USER_ACTION = auto()
    FALLBACK = auto()
    TERMINAL = auto()


# The first applicable action is the caller's immediate strategy.
ACTION_PRIORITY = (
    ErrorAction.FALLBACK,
    ErrorAction.RETRYABLE,
    ErrorAction.USER_ACTION,
    ErrorAction.TERMINAL,
)
WRITER_CONFLICT_MARKER = "already has an active writer"


@dataclass(frozen=True)
class ErrorDisposition:
    actions: ErrorAction
    retry_after: float | None = None

    @property
    def primary(self):
        for action in ACTION_PRIORITY:
            if action in self.actions:
                return action
        return ErrorAction.TERMINAL


class UserFacingError(ValueError):
    """An already complete WeChat-facing error message."""
    error_actions = ErrorAction.USER_ACTION


class BackendStartupError(RuntimeError):
    """A bounded, safe startup reason derived from native service metadata."""
    error_actions = ErrorAction.RETRYABLE

    def __init__(self, reason, *, retry_after=10):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


class WeChatAuthExpired(RuntimeError):
    """The saved bot token was rejected and requires an interactive rebind."""
    error_actions = ErrorAction.USER_ACTION
    code = -14

    def __init__(self):
        from wechat_auth import rebind_command
        super().__init__("微信绑定已失效；运行 " + rebind_command())


class WeChatPollTimeout(RuntimeError):
    """Long poll reached its wait deadline after HTTPS preparation, without a response."""
    error_actions = ErrorAction.RETRYABLE


class WeChatApiError(RuntimeError):
    """The API rejected the request itself: retrying cannot change the outcome."""
    error_actions = ErrorAction.TERMINAL

    def __init__(self, code, kind):
        self.code = code
        self.kind = kind
        super().__init__(f"微信 API 错误 {code}")


class WeChatNotAcknowledged(WeChatApiError):
    """The call succeeded but no message id came back, so nothing was delivered.

    The server answers 200 with `{"ret": 0}` while dropping the message (observed
    while a bot's outbound text channel was blocked: `sendtyping` still worked).
    Treating that as success removed the entry from the outbox, so the user saw
    nothing and only the log knew.
    """

    error_actions = ErrorAction.RETRYABLE

    def __init__(self, ret=None):
        super().__init__("no_id" if ret is None else ret, "not_acknowledged")

    def __str__(self):
        return f"微信未确认消息投递（ret={self.code}）"


def writer_conflict(error):
    """A resume rejected because another client still holds the thread's writer."""
    return (getattr(error, "code", None) == -32600
            and WRITER_CONFLICT_MARKER in str(getattr(error, "reason", error)))


def disposition(error):
    """Return independent handling axes and the bounded retry delay, if any."""
    actions = getattr(type(error), "error_actions", ErrorAction.TERMINAL)
    if writer_conflict(error):
        actions = (actions & ~ErrorAction.TERMINAL) | ErrorAction.RETRYABLE | ErrorAction.USER_ACTION
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        actions = (actions & ~ErrorAction.TERMINAL) | ErrorAction.RETRYABLE
    if ErrorAction.RETRYABLE in actions or ErrorAction.FALLBACK in actions:
        actions &= ~ErrorAction.TERMINAL
    retry_after = getattr(error, "retry_after", None)
    if not isinstance(retry_after, (int, float)) or retry_after < 0:
        retry_after = None
    return ErrorDisposition(actions, retry_after)


BACKEND_REASONS = (
    (("超时", "timeout"), "连接超时"),
    (("启动器", "no such file", "filenotfound"), "启动器未就绪"),
    (("systemd", "用户服务"), "用户服务不可用"),
    (("websocket", "共享连接", "连接断开", "连接失败", "后端已退出"), "后端连接中断"),
    (("rpc",), "后端拒绝了请求"),
    (("依赖", "websockets"), "后端组件不可用"),
    (("command", "配置"), "后端配置无效"),
)
BACKEND_REASON_DEFAULT = "服务异常"

ATTACHMENT_REASONS = (
    (("超过", "大小限制"), "文件超过大小限制"),
    (("图片仅支持",), "图片格式不支持"),
    (("凭据或运行状态",), "不允许发送微信桥私有状态"),
    (("自动回传仅限",), "只能自动发送当前会话目录内的文件"),
    (("不是普通文件",), "附件不是普通文件"),
    (("网络共享", "设备路径", "驱动器相对路径", "ntfs 附加数据流"),
     "路径格式不受支持，请只填文件路径本身"),
    (("不存在", "无法读取", "permission denied", "permissionerror",
      "找不到指定的文件", "找不到文件", "no such file", "filenotfounderror",
      "cannot find the file"),
     "文件不存在或不可读"),
    (("密钥", "密文", "md5", "校验失败"), "附件校验失败"),
    (("cdn", "curl", "http", "上传", "未返回附件"), "微信附件服务未响应"),
    (("cryptography", "依赖"), "附件组件不可用"),
    (("未知附件类型", "下载引用", "缺少"), "附件格式不支持"),
    (("最多",), "附件数量超过限制"),
)
ATTACHMENT_REASON_DEFAULT = "附件处理失败"


def _classify(text, rules, default):
    """Return the first reason whose tokens match; order is significant."""
    for tokens, reason in rules:
        if any(token in text for token in tokens):
            return reason
    return default


def backend_reason(error):
    if isinstance(error, BackendStartupError):
        return error.reason
    return _classify(str(error).casefold(), BACKEND_REASONS, BACKEND_REASON_DEFAULT)


def attachment_reason(error):
    return _classify(str(error).casefold(), ATTACHMENT_REASONS,
                     ATTACHMENT_REASON_DEFAULT)
