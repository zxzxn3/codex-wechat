"""User-safe error classification; no protocol or UI state."""
class UserFacingError(ValueError):
    """An already complete WeChat-facing error message."""


class BackendStartupError(RuntimeError):
    """A bounded, safe startup reason derived from native service metadata."""
    def __init__(self, reason, *, retry_after=10):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


class WeChatAuthExpired(RuntimeError):
    """The saved bot token was rejected and requires an interactive rebind."""
    code = -14

    def __init__(self):
        from wechat_auth import rebind_command
        super().__init__("微信绑定已失效；运行 " + rebind_command())


class WeChatPollTimeout(RuntimeError):
    """Long poll reached its wait deadline after HTTPS preparation, without a response."""


class WeChatApiError(RuntimeError):
    """The API rejected the request itself: retrying cannot change the outcome."""
    def __init__(self, code, kind):
        self.code = code
        self.kind = kind
        super().__init__(f"微信 API 错误 {code}")


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
    (("不存在", "无法读取", "permission denied", "permissionerror"),
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
