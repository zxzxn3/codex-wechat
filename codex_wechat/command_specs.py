"""One catalog for command routing, scheduling, aliases and help."""
from dataclasses import dataclass
from .prompts import HELP_DESCRIPTIONS, prompt_text


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
    default_help: bool = True
    help_usage: str | None = None


COMMANDS = (
    Command("/status", "/status", HELP_DESCRIPTIONS['/status'], lane="local", aliases=("/s",)),
    Command("/settings", "/settings [autostart|shared] true|false | ack|finish [text {模板}|fields]",
            HELP_DESCRIPTIONS['/settings'], "managed", "local", section="设置",
            help_usage="/settings"),
    Command("/sessions", "/sessions [页码|搜索词]", HELP_DESCRIPTIONS['/sessions'], "managed", needs_backend=True, section="会话"),
    Command("/new", "/new", HELP_DESCRIPTIONS['/new'], "managed", needs_backend=True, section="会话", aliases=("/n",)),
    Command("/compact", "/compact", HELP_DESCRIPTIONS['/compact'], "managed", needs_backend=True, section="会话", aliases=("/c",)),
    Command("/queue", "/queue {提示词}", HELP_DESCRIPTIONS['/queue'], route="input", aliases=("/q",)),
    Command("/unqueue", "/unqueue", HELP_DESCRIPTIONS['/unqueue'], lane="urgent", aliases=("/u",)),
    Command("/stop", "/stop", HELP_DESCRIPTIONS['/stop'], lane="urgent"),
    Command("/discard", "/discard", HELP_DESCRIPTIONS['/discard'], lane="local", aliases=("/d",)),
    Command("/ping", "/ping", HELP_DESCRIPTIONS['/ping'], lane="probe", aliases=("/p",), default_help=False),
    Command("/switch", "/switch {编号、ID前缀或搜索词}", HELP_DESCRIPTIONS['/switch'], "managed", needs_backend=True, section="会话", required=True, default_help=False),
    Command("/backend", "/backend {名称}", HELP_DESCRIPTIONS['/backend'], "managed", section="会话", query_local=True, aliases=("/b",)),
    Command("/model", "/model {编号或名称}", HELP_DESCRIPTIONS['/model'], "managed",
            needs_backend=True, aliases=("/m",), section="设置"),
    Command("/effort", "/effort {档位}", HELP_DESCRIPTIONS['/effort'], "managed", needs_backend=True, section="设置", aliases=("/e",)),
    Command("/cwd", "/cwd {路径}", HELP_DESCRIPTIONS['/cwd'], "managed", needs_backend=True, section="设置", query_local=True, default_help=False),
    Command("/approve", "/approve {编号}", HELP_DESCRIPTIONS['/approve'], lane="urgent", section="审批", handler="approval", required=True, default_help=False),
    Command("/deny", "/deny {编号}", HELP_DESCRIPTIONS['/deny'], lane="urgent", section="审批", handler="approval", required=True, default_help=False),
    Command("/answer", "/answer {问题编号} {选项或文字}", HELP_DESCRIPTIONS['/answer'], lane="urgent",
            required=True, aliases=("/a",), default_help=False),
    Command("/get", "/get {路径}", HELP_DESCRIPTIONS['/get'], lane="media", section="附件", handler="get", default_help=False, aliases=("/g",)),
    Command("/getfile", "/getfile {路径}", HELP_DESCRIPTIONS['/getfile'], lane="media", section="附件", handler="get", default_help=False),
    Command("/restart", "/restart", HELP_DESCRIPTIONS['/restart'], lane="local", aliases=("/r",)),
    Command("/continue", "/continue", HELP_DESCRIPTIONS['/continue'], lane="local",
            aliases=("/",), default_help=False),
    Command("/help", "/help [all|settings]", HELP_DESCRIPTIONS['/help'], lane="local",
            aliases=("/h",), help_usage="/help"),
)
CONTROL_LANES = tuple(dict.fromkeys(spec.lane for spec in COMMANDS if spec.route != "input"))
PENDING_CONTROL_LIMIT = 100
REGISTRY = {}
for spec in COMMANDS:
    for name in (spec.name, *spec.aliases):
        if name in REGISTRY:
            raise ValueError(f"重复的命令名称: {name}")
        REGISTRY[name] = spec


def help_text(show_all=False, *, text=prompt_text):
    """Short aliases lead the line so the terse form is always the first one seen."""
    def line(spec):
        head = command_usage(spec.name, help_form=True)
        return f"{head} {text('HELP_' + spec.name[1:].upper())}"
    return "\n".join(line(spec) for spec in COMMANDS if show_all or spec.default_help)


def _command_reference(spec, value):
    references = [f"`{alias}`" for alias in spec.aliases]
    references.append(f"`{value}`")
    return " ".join(references)


def command_name(name):
    """Canonical command reference with aliases first, without arguments."""
    spec = REGISTRY.get(name)
    return _command_reference(spec, spec.name) if spec else f"`{name}`"


def command_usage(name, *, help_form=False):
    """Usage reference with aliases first; help_form uses the short help entry."""
    spec = REGISTRY.get(name)
    if spec is None:
        return f"`{name}`"
    value = (spec.help_usage or spec.usage) if help_form else spec.usage
    return _command_reference(spec, value)


HELP = help_text()
HELP_ALL = help_text(show_all=True)


def command_lane(command, argument):
    spec = REGISTRY.get(command)
    if spec and spec.query_local and not argument:
        return "local"
    return spec.lane if spec else "local"
