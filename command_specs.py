"""One catalog for command routing, scheduling, aliases and help."""
from dataclasses import dataclass
from prompts import HELP_DESCRIPTIONS, prompt_text


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
    Command("/status", "/status", HELP_DESCRIPTIONS['/status'], lane="local", aliases=("/s",)),
    Command("/settings", "/settings [autostart on|off]", HELP_DESCRIPTIONS['/settings'], "managed", "local", section="设置"),
    Command("/sessions", "/sessions [页码|搜索词]", HELP_DESCRIPTIONS['/sessions'], "managed", needs_backend=True, section="会话"),
    Command("/new", "/new", HELP_DESCRIPTIONS['/new'], "managed", needs_backend=True, section="会话"),
    Command("/compact", "/compact", HELP_DESCRIPTIONS['/compact'], "managed", needs_backend=True, section="会话"),
    Command("/queue", "/queue {提示词}", HELP_DESCRIPTIONS['/queue'], route="input", aliases=("/q",)),
    Command("/unqueue", "/unqueue", HELP_DESCRIPTIONS['/unqueue'], lane="urgent"),
    Command("/stop", "/stop", HELP_DESCRIPTIONS['/stop'], lane="urgent"),
    Command("/ping", "/ping", HELP_DESCRIPTIONS['/ping'], lane="probe", aliases=("/p",)),
    Command("/switch", "/switch {编号、ID或搜索词}", HELP_DESCRIPTIONS['/switch'], "managed", needs_backend=True, section="会话", required=True),
    Command("/backend", "/backend {名称}", HELP_DESCRIPTIONS['/backend'], "managed", section="会话", query_local=True),
    Command("/model", "/model {编号或名称}", HELP_DESCRIPTIONS['/model'], "managed",
            needs_backend=True, aliases=("/m",), section="设置"),
    Command("/effort", "/effort {档位}", HELP_DESCRIPTIONS['/effort'], "managed", needs_backend=True, section="设置", aliases=("/e",)),
    Command("/cwd", "/cwd {路径}", HELP_DESCRIPTIONS['/cwd'], "managed", needs_backend=True, section="设置", query_local=True),
    Command("/ack", "/ack [text {模板}|fields]", HELP_DESCRIPTIONS['/ack'], "managed", "local", section="设置", handler="notice"),
    Command("/finish", "/finish [text {模板}|fields]", HELP_DESCRIPTIONS['/finish'], "managed", "local", section="设置", handler="notice"),
    Command("/approve", "/approve {编号}", HELP_DESCRIPTIONS['/approve'], lane="urgent", section="审批", handler="approval", required=True),
    Command("/deny", "/deny {编号}", HELP_DESCRIPTIONS['/deny'], lane="urgent", section="审批", handler="approval", required=True),
    Command("/answer", "/answer {问题编号} {选项或文字}", HELP_DESCRIPTIONS['/answer'], lane="urgent", required=True),
    Command("/send", "/send {路径}", HELP_DESCRIPTIONS['/send'], lane="media", section="附件", handler="send"),
    Command("/sendfile", "/sendfile {路径}", HELP_DESCRIPTIONS['/sendfile'], lane="media", section="附件", handler="send"),
    Command("/sendimage", "/sendimage {路径}", HELP_DESCRIPTIONS['/sendimage'], lane="media", section="附件", handler="send"),
    Command("/sendvideo", "/sendvideo {路径}", HELP_DESCRIPTIONS['/sendvideo'], lane="media", section="附件", handler="send"),
    Command("/restart", "/restart", HELP_DESCRIPTIONS['/restart'], lane="local"),
    Command("/help", "/help", HELP_DESCRIPTIONS['/help'], lane="local", aliases=("/h",)),
)
CONTROL_LANES = tuple(dict.fromkeys(spec.lane for spec in COMMANDS if spec.route != "input"))
REGISTRY = {}
for spec in COMMANDS:
    for name in (spec.name, *spec.aliases):
        if name in REGISTRY:
            raise ValueError(f"重复的命令名称: {name}")
        REGISTRY[name] = spec


def help_text(*, text=prompt_text):
    return "\n".join(f"`{spec.usage}` {text('HELP_' + spec.name[1:].upper())}" +
                     (" " + " ".join(f"`{alias}`" for alias in spec.aliases) if spec.aliases else "")
                     for spec in COMMANDS)


HELP = help_text()


def command_lane(command, argument):
    spec = REGISTRY.get(command)
    if spec and spec.query_local and not argument:
        return "local"
    return spec.lane if spec else "local"
