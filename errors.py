"""User-safe error classification; no protocol or UI state."""
class UserFacingError(ValueError):
    """An already complete WeChat-facing error message."""


class BackendStartupError(RuntimeError):
    """A bounded, safe startup reason derived from native service metadata."""
    def __init__(self, reason, *, retry_after=10):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


def backend_reason(error):
    if isinstance(error, BackendStartupError):
        return error.reason
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


