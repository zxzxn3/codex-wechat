"""App / CLI access to the same local Codex runtime; no proxy configuration."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from backend_runtime import BackendRuntime
from codex_connection import ConnectionSpec
from native_codex import discover_app, discover_app_codex, discover_codex
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


def configure(name, config, path, *, url=None, executable=None):
    from installer import InstallLock, _atomic_write
    target = path.parent
    target.mkdir(parents=True, exist_ok=True)
    with InstallLock(target):
        # Reread under the lock; preserve unrelated config and hot settings.
        config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        backend = backend_config(name, config)
        if url is None:
            url = backend.get("websocketUrl")
        if url is None:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                url = "ws://127.0.0.1:" + str(listener.getsockname()[1])
        command = backend.get("command")
        if executable:
            command = [str(Path(executable).expanduser().resolve()), "app-server"]
        runtime = backend.get("runtime", "auto")
        if command is None:
            bundled = discover_app_codex(windows=IS_WINDOWS)
            native = bundled or discover_codex(windows=IS_WINDOWS)
            if not native:
                raise ValueError("找不到原生 codex；请用 --executable 指定。")
            # Resolve App versions at runtime, not a stale absolute package path.
            runtime = "app" if bundled else "auto"
            command = ["codex", "app-server"]
        candidate = {**backend, "command": command, "runtime": runtime, "sharedDaemon": True,
                     "websocketUrl": url, "websocketStartupCommand": startup_command(name)}
        ConnectionSpec.from_config(candidate)
        config.setdefault("backends", {})[name] = candidate
        _atomic_write(path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode(),
                      0o600, root=target)
    print("共享接入已配置。重新启动微信桥后，通过 codex-wechat app 或 cli 进入。")
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
    # For CLI, arguments following '--' are forwarded exactly, not parsed here.
    argv = list(sys.argv[1:] if argv is None else argv)
    forwarded = []
    if "--" in argv:
        boundary = argv.index("--")
        forwarded, argv = argv[boundary + 1:], argv[:boundary]
    args, extra = parser.parse_known_args(argv)
    forwarded = extra + forwarded
    if args.action != "cli" and forwarded:
        parser.error("只有 cli 接受附加参数")
    if args.action != "setup" and (args.url or args.executable):
        parser.error("--url / --executable 仅用于 setup")
    path = data_root() / "config.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("配置必须是对象")
    if args.action == "setup":
        return configure(args.backend, config, path, url=args.url, executable=args.executable)
    backend = backend_config(args.backend, config)
    spec = ConnectionSpec.from_config(backend)
    app = backend.get("appCommand") or discover_app(windows=IS_WINDOWS)
    if args.action == "app" and (not app or not isinstance(app, list) or
                                 any(not isinstance(x, str) or not x for x in app)):
        raise ValueError("找不到 App；请安装 App 或配置 appCommand 字符串数组。")
    if args.action == "app" and not spec.endpoint:
        # App's default backend is platform/build dependent; never imply sharing
        # merely because it uses the same CODEX_HOME.
        raise ValueError("App 共享接入需要先运行 codex-wechat desktop setup。")
    if args.action == "cli" and any(arg.split("=", 1)[0] in
            {"--remote", "--remote-auth-token-env", "--no-daemon"} for arg in forwarded):
        raise ValueError("此入口固定使用选定后台；其它连接请直接运行原生 codex。")
    spec, env = await ensure(args.backend, backend)
    if args.action == "ensure":
        return 0
    if args.action == "app":
        env["CODEX_APP_SERVER_WS_URL"] = spec.endpoint
        env.pop("CODEX_APP_SERVER_FORCE_CLI", None)
        subprocess.Popen(app, env=env, stdin=subprocess.DEVNULL,
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
