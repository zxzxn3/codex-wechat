"""App / CLI access to the same local Codex runtime; no proxy configuration."""
import argparse
import asyncio
import configparser
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from backend_runtime import BackendRuntime, run
from codex_connection import ConnectionSpec
from native_codex import APP_DESKTOP_ID, discover_app, discover_app_codex, discover_codex, supports_app_daemon
from platform_support import IS_WINDOWS, INSTALL_ROOT, data_root, resolve_command
from profiles import definitions


def backend_config(name, config):
    backends = definitions(config)
    if name not in backends:
        raise ValueError("后端不存在: " + name)
    return backends[name]


def startup_command(name):
    if IS_WINDOWS:
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(INSTALL_ROOT / "codex-desktop.ps1"),
                "ensure", "--backend", name]
    return [str(INSTALL_ROOT / "codex-desktop"), "ensure", "--backend", name]


def runtime_root(name):
    # Stable, safe names even for user-defined backend labels.
    import hashlib
    key = hashlib.sha256(name.encode()).hexdigest()[:16]
    return data_root() / "shared" / key


def desktop_argument(value):
    """Quote a literal Exec argument using desktop-entry rules, not shell rules."""
    value = str(value)
    if any(ord(char) < 32 for char in value):
        raise ValueError("桌面入口参数不能包含控制字符。")
    value = value.replace("%", "%%")
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'


def shortcut_plan(name):
    # Use the installed application's identity and metadata; don't replace its package files.
    target = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "applications" / APP_DESKTOP_ID
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if target.is_symlink():
        raise ValueError("拒绝覆盖符号链接桌面入口。")
    if target.exists():
        parser.read_string(target.read_text())
        if parser.get("Desktop Entry", "X-Codex-WeChat", fallback="") != "true":
            raise ValueError("已有自定义 App 桌面入口，请先自行保留或移除。")
        if parser.get("Desktop Entry", "Actions", fallback="") or any(
                section.startswith("Desktop Action ") for section in parser.sections()):
            raise ValueError("App 桌面入口含额外动作；请使用 codex-wechat app，暂不改写该入口。")
    source = next((Path(folder) / "applications" / APP_DESKTOP_ID
                   for folder in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
                   if folder and (Path(folder) / "applications" / APP_DESKTOP_ID).is_file()), None)
    if source is None:
        raise ValueError("找不到 App 系统桌面入口；仍可使用 codex-wechat app。")
    parser.clear()
    parser.read_string(source.read_text())
    entry = parser["Desktop Entry"]
    command = [INSTALL_ROOT / "codex-desktop", "app", "--backend", name, "--"]
    entry["Exec"] = " ".join(desktop_argument(arg) for arg in command) + " %U"
    entry["DBusActivatable"] = "false"
    entry["X-Codex-WeChat"] = "true"
    # Unsupported secondary actions must not silently bypass sharing or disappear.
    if entry.get("Actions") or any(section.startswith("Desktop Action ") for section in parser.sections()):
        raise ValueError("App 桌面入口含额外动作；请使用 codex-wechat app，暂不改写该入口。")
    output = io.StringIO()
    parser.write(output, space_around_delimiters=False)
    return target, output.getvalue().encode()


def configure(name, config, path, *, url=None, executable=None, native=False, shortcut=False):
    from installer import InstallLock, _atomic_write
    target = path.parent
    target.mkdir(parents=True, exist_ok=True)
    with InstallLock(target):
        # Reread under the lock; preserve unrelated config and hot settings.
        config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        backend = backend_config(name, config)
        if native and (IS_WINDOWS or url):
            raise ValueError("--native 仅用于 Linux，不能与 --url 同用。")
        if url is None and not native:
            url = backend.get("websocketUrl")
        use_native = not IS_WINDOWS and url is None
        if url is None and not use_native:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                url = "ws://127.0.0.1:" + str(listener.getsockname()[1])
        command = backend.get("command")
        if executable:
            command = [str(Path(executable).expanduser().resolve()), "app-server"]
        runtime = backend.get("runtime", "auto")
        if command is None and not use_native:
            bundled = discover_app_codex(windows=IS_WINDOWS)
            native = bundled or discover_codex(windows=IS_WINDOWS)
            if not native:
                raise ValueError("找不到原生 codex；请用 --executable 指定。")
            # Resolve App versions at runtime, not a stale absolute package path.
            runtime = "app" if bundled else "auto"
            command = ["codex", "app-server"]
        candidate = {**backend, "runtime": runtime, "sharedDaemon": True}
        if command is not None:
            candidate["command"] = command
        if use_native:
            candidate.pop("websocketUrl", None)
            candidate.pop("websocketStartupCommand", None)
        else:
            candidate.update(websocketUrl=url, websocketStartupCommand=startup_command(name))
        ConnectionSpec.from_config(candidate)
        if shortcut and IS_WINDOWS:
            raise ValueError("--shortcut 仅用于 Linux。")
        planned = shortcut_plan(name) if shortcut else None
        config.setdefault("backends", {})[name] = candidate
        _atomic_write(path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode(),
                      0o600, root=target)
        if planned:
            destination, content = planned
            _atomic_write(destination, content, 0o644, root=destination.parent)
    print("共享接入已配置。先退出已有 App，再通过 codex-wechat app 或 cli 进入。")
    if shortcut:
        print("App 桌面图标已接入共享启动；删除用户目录中的 " + str(planned[0]) + " 可恢复系统入口。")
    print("若更改了后端连接配置，请重新启动微信桥。")
    return 0


async def ensure(name, backend):
    spec = ConnectionSpec.from_config(backend)
    env = spec.environment(backend)
    runtime = BackendRuntime(backend, str(data_root()))
    if spec.endpoint:
        # The bridge's hook calls this entry. Do not invoke that same hook again.
        await runtime.ensure_websocket(spec.resolve(env, client=True), env, spec.endpoint,
                                       runtime_root(name), name=name)
    elif spec.shared:
        command = spec.resolve(env, client=True) + ["app-server"]
        await runtime.ensure_native(command, env, is_windows=IS_WINDOWS)
    else:
        raise ValueError("此后端是独立连接；运行 desktop setup 启用共享接入。")
    return spec, env


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("app", "cli", "ensure", "setup"))
    parser.add_argument("--backend", default="codex")
    parser.add_argument("--url")
    parser.add_argument("--executable")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--shortcut", action="store_true")
    # For CLI, arguments following '--' are forwarded exactly, not parsed here.
    argv = list(sys.argv[1:] if argv is None else argv)
    forwarded = []
    if "--" in argv:
        boundary = argv.index("--")
        forwarded, argv = argv[boundary + 1:], argv[:boundary]
    args, extra = parser.parse_known_args(argv)
    forwarded = extra + forwarded
    if args.action not in ("cli", "app") and forwarded:
        parser.error("只有 cli / app 接受附加参数")
    if args.action != "setup" and (args.url or args.executable or args.native or args.shortcut):
        parser.error("--url / --executable / --native / --shortcut 仅用于 setup")
    path = data_root() / "config.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("配置必须是对象")
    if args.action == "setup":
        return configure(args.backend, config, path, url=args.url, executable=args.executable,
                         native=args.native, shortcut=args.shortcut)
    backend = backend_config(args.backend, config)
    spec = ConnectionSpec.from_config(backend)
    app = backend.get("appCommand") or discover_app(windows=IS_WINDOWS)
    if args.action == "app" and (not app or not isinstance(app, list) or
                                 any(not isinstance(x, str) or not x for x in app)):
        raise ValueError("找不到 App；请安装 App 或配置 appCommand 字符串数组。")
    native_app = args.action == "app" and not spec.endpoint
    if native_app:
        if IS_WINDOWS or not spec.shared:
            raise ValueError("此连接不能直接供 App 共享；请运行 codex-wechat desktop setup。")
        launch_env = spec.environment(backend)
        if launch_env.get("CODEX_CLI_PATH", "").strip() or launch_env.get("CODEX_APP_SERVER_FORCE_CLI") == "1":
            raise ValueError("请先移除 CODEX_CLI_PATH / CODEX_APP_SERVER_FORCE_CLI；它们会使 App 使用独立后台。")
        if Path(spec.client_command()[0]).name not in ("codex", "codex.exe") and not launch_env.get("CODEX_HOME"):
            raise ValueError("自定义启动器接入 App 时须显式配置后端 env.CODEX_HOME，或使用 WebSocket 共享。")
        bundled = discover_app_codex(launch_env, windows=False)
        if not bundled:
            raise ValueError("找不到 App 内置 Codex，无法确认原生后台兼容性。")
    if args.action == "cli" and any(arg.split("=", 1)[0] in
            {"--remote", "--remote-auth-token-env", "--no-daemon"} for arg in forwarded):
        raise ValueError("此入口固定使用选定后台；其它连接请直接运行原生 codex。")
    spec, env = await ensure(args.backend, backend)
    if args.action == "ensure":
        return 0
    if args.action == "app":
        env = dict(env)
        if native_app:
            version = json.loads(await run([bundled, "app-server", "daemon", "version"],
                                           env=env, cwd=str(data_root()), timeout=5))
            if not isinstance(version, dict) or not version.get("appServerVersion"):
                raise ValueError("App 未发现运行中的原生后台；请检查 CODEX_HOME 和后台版本。")
            if not supports_app_daemon(version["appServerVersion"]):
                raise ValueError("原生后台版本不满足 App 共享要求；请更新后台后重试。")
            env.pop("CODEX_APP_SERVER_WS_URL", None)
            env["CODEX_APP_SERVER_USE_LOCAL_DAEMON"] = "1"
        else:
            env["CODEX_APP_SERVER_WS_URL"] = spec.endpoint
            env.pop("CODEX_APP_SERVER_USE_LOCAL_DAEMON", None)
            env.pop("CODEX_APP_SERVER_FORCE_CLI", None)
        print("请先完全退出已有 App；再次启动不会改变已有窗口的后台。", flush=True)
        subprocess.Popen([*app, *forwarded], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=not IS_WINDOWS)
        return 0
    command = spec.resolve(env, client=True)
    command += ["--remote", spec.endpoint or "unix://", *forwarded]
    # The TUI inherits the terminal, working directory, and exit status.
    return subprocess.call(command, env=env)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
