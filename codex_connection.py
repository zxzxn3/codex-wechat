"""Validated Codex connection intent, shared by bridge and desktop entry points."""
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit

from diagnostics import log_event
from platform_support import IS_WINDOWS, PROXY_ENV_KEYS, apply_system_proxy, proxy_label

SUPPORTED_TRANSPORTS = ("stdio", "native-daemon", "loopback-websocket")


@dataclass(frozen=True)
class ConnectionSpec:
    command: tuple[str, ...]
    shared: bool
    endpoint: str | None = None
    startup: tuple[str, ...] = ()
    runtime: str = "auto"
    system_proxy: bool = True

    @classmethod
    def from_config(cls, config):
        runtime = config.get("runtime", "auto")
        if runtime not in ("auto", "app"):
            raise ValueError("runtime 仅支持 auto 或 app")
        command = config.get("command", ["codex", "app-server"])
        if (not isinstance(command, (list, tuple)) or not command or
                any(not isinstance(value, str) or not value for value in command)):
            raise ValueError("后端 command 必须是非空字符串数组")
        env = config.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                            for k, v in env.items()):
            raise ValueError("后端 env 必须是字符串对象")
        shared = config.get("sharedDaemon", not IS_WINDOWS and (
            list(command[-1:]) == ["app-server"] or
            list(command[-2:]) == ["app-server", "proxy"]))
        if type(shared) is not bool:
            raise ValueError("sharedDaemon 必须是布尔值")
        endpoint = config.get("websocketUrl")
        if endpoint is not None:
            # No credentials, paths, remote hosts or ambiguous authority parsing.
            if not isinstance(endpoint, str) or not re.fullmatch(
                    r"ws://(?:127\.0\.0\.1|localhost|\[::1\]):[0-9]{1,5}/?", endpoint):
                raise ValueError("websocketUrl 仅允许带端口的本机 ws:// 回环地址")
            try:
                if not 1 <= urlsplit(endpoint).port <= 65535:
                    raise ValueError()
            except ValueError:
                raise ValueError("websocketUrl 端口无效") from None
            shared = True
        startup = config.get("websocketStartupCommand", [])
        if startup and not endpoint:
            raise ValueError("websocketStartupCommand 需要同时配置 websocketUrl")
        if not isinstance(startup, (list, tuple)) or any(
                not isinstance(value, str) or not value for value in startup):
            raise ValueError("websocketStartupCommand 必须是字符串数组")
        if shared and not endpoint and list(command[-1:]) != ["app-server"] and list(command[-2:]) != ["app-server", "proxy"]:
            raise ValueError("sharedDaemon 模式的 command 须以 app-server 结尾")
        system_proxy = config.get("useSystemProxy", True)
        if type(system_proxy) is not bool:
            raise ValueError("useSystemProxy 必须是布尔值")
        return cls(tuple(command), shared, endpoint, tuple(startup), runtime, system_proxy)

    def environment(self, config):
        env = dict(os.environ)
        # A caller's current chat/home must never silently choose another backend.
        for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME"):
            env.pop(key, None)
        # Disabling system-proxy discovery means this backend should not inherit
        # an unrelated proxy from the long-lived bridge process. Explicit backend
        # env below still wins, so a deliberate proxy configuration is preserved.
        if not self.system_proxy:
            for key in PROXY_ENV_KEYS:
                env.pop(key, None)
        env.update(config.get("env", {}))
        # Codex only reads a proxy for sampling from the environment; without it
        # the first turn of a proxied machine burns every WebSocket retry.
        applied = apply_system_proxy(env, enabled=self.system_proxy)
        if applied:
            log_event("backend.proxy_inherited", component="backend", source="system",
                      proxy=proxy_label(applied["HTTPS_PROXY"]))
        return env

    def proxy_command(self):
        command = list(self.command)
        if self.shared and not self.endpoint and command[-1:] == ["app-server"]:
            command.append("proxy")
        return command

    def client_command(self):
        command = list(self.command)
        if command[-2:] == ["app-server", "proxy"]:
            return command[:-2]
        if command[-1:] == ["app-server"]:
            return command[:-1]
        raise ValueError("此后端没有可复用的 Codex CLI 入口")

    def resolve(self, env, *, client=False):
        from native_codex import discover_app_codex
        from platform_support import resolve_command
        command = self.client_command() if client else self.proxy_command()
        if self.runtime == "app" and command[0] in ("codex", "codex.exe"):
            executable = discover_app_codex(env, windows=IS_WINDOWS)
            if not executable:
                raise ValueError("找不到 App 内置 Codex；请安装 App 或显式配置 command。")
            command[0] = executable
        return resolve_command(command, env)
