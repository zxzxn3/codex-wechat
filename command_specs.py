"""One catalog for command routing, scheduling, aliases and help."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    description: str
    route: str = "control"
    lane: str = "backend"
    needs_backend: bool = False
    aliases: tuple[str, ...] = ()
    section: str = "控制"
    handler: str | None = None
    required: bool = False
    query_local: bool = False


COMMANDS = (
    Command("/status", "/status", "当前状态", lane="local"),
    Command("/settings", "/settings", "当前设置", "managed", "local", section="设置"),
    Command("/sessions", "/sessions {页码}", "会话", "managed", needs_backend=True, section="会话"),
    Command("/new", "/new", "新建", "managed", needs_backend=True, section="会话", handler="session"),
    Command("/compact", "/compact", "压缩当前会话上下文", "managed", needs_backend=True, section="会话", handler="session"),
    Command("/queue", "/queue {提示词}", "排队到当前任务结束后执行", route="input"),
    Command("/stop", "/stop", "停止并清空待办", lane="urgent"),
    Command("/ping", "/ping", "微信与供应商网络延迟", lane="probe"),
    Command("/switch", "/switch {编号或ID}", "切换会话", "managed", needs_backend=True, section="会话", handler="session", required=True),
    Command("/backend", "/backend {名称}", "查看或切换后端", "managed", section="会话", query_local=True),
    Command("/model", "/model {编号或名称}", "查看或切换模型", "managed",
            needs_backend=True, aliases=("/models",), section="设置"),
    Command("/effort", "/effort {档位}", "查看或切换强度", "managed", needs_backend=True, section="设置", handler="session"),
    Command("/cwd", "/cwd {路径}", "查看或切换目录", "managed", needs_backend=True, section="设置", query_local=True),
    Command("/ack", "/ack [text {模板}|fields]", "接收提示；空模板关闭", "managed", "local", section="设置", handler="notice"),
    Command("/finish", "/finish [text {模板}|fields]", "结束提示；空模板关闭", "managed", "local", section="设置", handler="notice"),
    Command("/approve", "/approve {编号}", "同意审批", lane="urgent", section="审批", handler="approval", required=True),
    Command("/deny", "/deny {编号}", "拒绝审批", lane="urgent", section="审批", handler="approval", required=True),
    Command("/send", "/send {路径}", "回传文件或图片", lane="media", section="附件", handler="send"),
    Command("/sendfile", "/sendfile {路径}", "作为文件回传", lane="media", section="附件", handler="send"),
    Command("/sendimage", "/sendimage {路径}", "作为图片回传", lane="media", section="附件", handler="send"),
    Command("/restart", "/restart", "重启消息桥", lane="local"),
    Command("/help", "/help", "命令说明", lane="local"),
)
CONTROL_LANES = tuple(dict.fromkeys(spec.lane for spec in COMMANDS if spec.route != "input"))
REGISTRY = {name: spec for spec in COMMANDS for name in (spec.name, *spec.aliases)}


def help_text():
    return "\n".join(f"`{spec.usage}` {spec.description}" for spec in COMMANDS)


HELP = help_text()


def command_lane(command, argument):
    spec = REGISTRY.get(command)
    if spec and spec.query_local and not argument:
        return "local"
    return spec.lane if spec else "local"
