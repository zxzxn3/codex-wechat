# Windows

Windows 使用 Python 3.11+、Codex 和 `curl`。独立 CLI 或已注册的 App 内置 `codex.exe` 均可
发现；显式配置的 `command` 始终优先，`.cmd` / `.bat` 不会被自动当成原生 exe。

安装、更新和卸载见 [INSTALL](INSTALL.md)，完整命令表见 [COMMANDS](COMMANDS.md)。

## 首次启动

已安装短命令入口时：

```powershell
codex-wechat service-start
```

否则使用稳定入口：

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-start
```

未绑定时会在当前终端显示二维码，扫码后由后台服务继续启动。没有可用服务注册时，使用
前台 `start` 并保持终端运行。

## 命令入口

公开入口是 `%USERPROFILE%\.local\bin\codex-wechat.cmd`。它只是启动器：

```text
codex-wechat <args>
  -> %USERPROFILE%\.local\bin\codex-wechat.cmd
  -> powershell -ExecutionPolicy Bypass -File <stable>\codex-wechat.ps1 <args>
  -> python -m codex_wechat
```

只有 `%USERPROFILE%\.local\bin` 已存在且在用户 PATH 中时，安装器才创建 `.cmd`；不会修改
PATH。未创建时使用完整 `codex-wechat.ps1` 路径，行为相同。`.ps1` 不在 `PATHEXT` 中且受
执行策略限制，因此不能作为 cmd.exe 的短入口。

## 权限和安全

不要在管理员终端运行安装、更新、启动、登录、重绑、自启或服务控制。只读 `status`、`logs`
和显式 `uninstall` 例外；容器/一次性 CI 可设置 `CODEX_WECHAT_ALLOW_ROOT=1`，但这不是推荐
运行方式。

计划任务以当前用户、`-LogonType Interactive -RunLevel Limited` 注册。提权安装可能使任务
归 `Administrators` 所有，或让入口 DACL 锁住普通用户的 `pythonw`；安装器会检查当前用户
是否实际可读入口和 release。不要依赖 POSIX `0700` 语义；Windows 使用 `make_private()` 和
`icacls` 管理 DACL。

## 服务和数据

- 安装根目录：`%LOCALAPPDATA%\codex-wechat\app`，保存 stable 入口和不可变 release。
- 数据根目录：默认 `%LOCALAPPDATA%\codex-wechat`，可用 `CODEX_WECHAT_DATA_ROOT` 覆盖；
  保存 `config.json`、`.state`、`workspace`、
  `logs`、`shared` 和 `active-release`；当前值记录在安装根的 `.data-root`，
  计划任务只传安装根，公开入口和服务 supervisor 都读取该标记。
- 计划任务负责原生启动、停止和查询；`codex_wechat/service_adapter.py` 负责 restart 编排、停止等待和
  稳定运行判断。`windows/task-service.ps1` 不实现 restart 状态机。
- 首次自启关闭；`autostart true|false` 只切换登录触发器，不启动、停止或接管当前实例。
- `logs --service` 读取 `logs/service.log` 及其轮转版本；结构化日志仍是
  `logs/bridge.jsonl`。

Windows 的 supervisor 使用 Mutex、Job Object 和 owner token；服务子进程由 supervisor
启动，并通过 `CODEX_WECHAT_SERVICE_CHILD=1` 区分于前台 `start`。退出码 `75` 表示用户请求
重启，supervisor 重新读取 `current`；配置错误使用 `78`。

## App IPC 和会话写者

App 正常启动并打开目标会话后，可以从微信 `/switch` 跟随。Windows 使用
`\\.\pipe\codex-ipc`，连接前核对管道服务进程属于当前用户且映像是已注册的 App。
只支持主 `CODEX_HOME`；独立后端不会猜测 App 的 home。

写者交接的常见现象：

- 桥接回合结束会交还 writer，但 app-server 可能延迟 65–100 秒才释放，App 仍可能短暂显示
  会话锁定；重新进入会话或稍等即可。
- App 运行但目标会话没有打开时，`thread-owner-discovery` 返回
  `CodexRpcError(-32600, "App IPC: no-client-found")`；桥接按“无法跟随”处理，会在后续
  轮询重试。
- 空闲时每 5 秒检查一次管道；attach 失败后的重试间隔是 60 秒。相同失败原因只记录一次
  `backend.app_attach_failed`，日志没有新事件不等于没有重试。
- 管道忙时 `Path.exists()` 可能抛 `OSError(winerror=231)`；`app_available()` 将
  `ERROR_PIPE_BUSY` 视为 App 仍存在，其它 `OSError` 才视为不可用。

如果 App 更新后身份校验失败，完全退出并重新打开 App；仍失败则查看
[VALIDATION](VALIDATION.md) 和日志。

### 第三方客户端抢占

任何自带 Codex 后端并打开会话的客户端都可能抢占 `\\.\pipe\codex-ipc` 的 router 身份或
持有会话 writer。典型来源包括 VS Code 的 `openai.chatgpt` 扩展、Codex App 本体和其它
resume 同一 `CODEX_HOME` 的启动器。确认后禁用该客户端，或让它在桥接之后启动，再重启
桥接。桥接不会为了跟随陌生宿主而放宽身份校验。

## 共享 daemon

Windows 默认使用独立 app-server（`sharedDaemon: false`），适合 App 为主的工作流。
如果主要通过普通 Codex CLI 与微信双向收发，直接运行 `codex` 或 `codex-deepseek`；
`codex-wechat` 不再提供 CLI 包装命令。然后在微信执行：

```text
/settings shared true
```

共享模式与 CLI 共用 daemon；桥接回合结束不会退订，否则会丢失另一客户端的回合。离开会话
或关闭共享模式时必须退订，否则 App 会一直看到锁定。模式切换会重建后端连接，任务进行中
会被拒绝。

共享 daemon 启动失败时，本次运行回退独立进程；原因保留在 `/status` 的
`shared_fallback` 和 `backend.shared_unavailable` 日志中，持久设置不被改写。

没有显式代理变量时，后端可按 `useSystemProxy` 读取 Windows 系统代理；显式
`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 优先。WMI 启动路径通过私有环境文件传递代理和
`CODEX_HOME`，不把凭据放进命令行；成功启动后还有兜底清扫。

## 诊断

```powershell
codex-wechat status
codex-wechat logs
codex-wechat logs -Follow
codex-wechat logs -Service
```

结构化日志位于数据根目录的 `logs/bridge.jsonl`，按 5 MiB 轮转并默认保留 3 份；
可用 `/settings logs.keep 1-100` 调整。`service.log` 也按 5 MiB 轮转并共用
`logs.keep`；`logs -Service` 会读取它的完整轮转链。`service.log` 是未脱敏控制台输出，
不要原样外传。

排查网络时先看 `dns_ms`、`curl_total_ms`、`timeout_stage` 和 `proxy_used`；字段定义见
[ARCHITECTURE](ARCHITECTURE.md#诊断与隐私)。本机网络问题、历史验收和 WMI 组合缺口见
[VALIDATION](VALIDATION.md)。

## 常见失败

- `current` 缺失或损坏：从新的源码重新运行 `update`；卸载仍应走 stable 入口。
- 找不到 Python 环境：重新运行安装器，不要手工复制 `.venv`。
- 服务启动后立即退出：查看 `logs/service.log` 和 `bridge.jsonl` 的启动事件；配置错误通常
  以 `78` 结束。
- App 找不到会话：确认目标会话已在 App 中打开，并检查 `backend.app_attach_failed`。
- 命令入口不可用：确认 `%USERPROFILE%\.local\bin` 在 PATH 中，或使用完整 stable 入口。
