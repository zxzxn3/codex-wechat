# 平台差异与支持面

本文合并平台操作、平台差异、能力边界和 Windows 排障。运行所有权见
[ARCHITECTURE](ARCHITECTURE.md)，安装和路径见 [INSTALL](INSTALL.md)，实机证据见
[VALIDATION](VALIDATION.md)。

## 支持状态

| 范围 | Linux | Windows |
| --- | --- | --- |
| 安装、启动、停止、卸载 | 当前树有离线回归 | 已通过历史实机验证，当前提交待复测 |
| systemd / Scheduled Task | 支持 | 支持，任务时序需复测 |
| 登录自启 | 支持 | 支持，当前 action 已改为只传安装根 |
| `.cmd` 短入口 | 不适用 | 支持；非 ASCII 用户目录待复测 |
| App IPC follower | 实验性 | 实验性，三端并发待复测 |
| 共享 daemon | 支持 | 支持，App 并发需复测 |

“支持”表示已实现并纳入维护；“实验性”允许尝试但不保证；未列出的 Codex/App 版本按待验证
处理。只有明确不支持的配置应 fail closed，实验性能力必须给出边界，不能静默回退。

## 入口与目录

| 项目 | Linux | Windows |
| --- | --- | --- |
| 公开入口 | `~/.local/bin/codex-wechat` | `%USERPROFILE%\.local\bin\codex-wechat.cmd` |
| 源码安装入口 | `install.sh` | `install.ps1` |
| 程序 | `~/.local/share/codex-wechat` | `%LOCALAPPDATA%\codex-wechat\app` |
| 配置 | `~/.config/codex-wechat/config.json` | 数据根下 `config.json` |
| 状态、日志、共享后台 | `~/.local/state/codex-wechat` | 数据根下 `.state`、`logs`、`shared` |
| 工作区 | 数据根下 `workspace` | 数据根下 `workspace` |

Windows `.cmd` 是薄启动器，内部调用 stable `codex-wechat.ps1`，最终进入
`python -m codex_wechat`。`.ps1` 不在 `PATHEXT` 中且受执行策略限制，不能作为 `cmd.exe`
短入口；安装器不会修改 PATH。

数据根由安装根 `.data-root` 唯一解析。环境变量与标记冲突时公开入口、supervisor 和卸载器
fail closed。Linux 安装后修改数据根必须重新运行安装器；Windows 数据根可由
`CODEX_WECHAT_DATA_ROOT` 指定。

## Windows 操作

已安装短入口时：

```powershell
codex-wechat service-start
```

没有短入口时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-start
```

未绑定时，`service-start` 在当前终端显示二维码，扫码后由后台服务继续；服务注册不可用时，
使用前台 `start` 并保持终端运行。

不要以管理员身份运行安装、更新、启动、登录、重绑、自启或服务控制。只读 `status`、`logs`
和显式 `uninstall` 例外；容器或 CI 才使用 `CODEX_WECHAT_ALLOW_ROOT=1`。

计划任务以当前用户、交互登录和有限权限运行。Windows 使用 `make_private()` 和 `icacls`
管理 DACL，不套用 POSIX `0700` 语义。提权安装可能导致任务归属或入口 ACL 错误，安装器会
检查当前用户能否实际读取入口和 release。

Windows supervisor 使用 Mutex、Job Object 和 owner token。退出码 `75` 表示请求重启，
supervisor 重新读取 `current`；配置错误通常以 `78` 结束。

## App IPC

App 正常启动并打开目标会话后，微信 `/switch` 可以跟随。Windows 使用
`\\.\pipe\codex-ipc`，连接前核对管道服务进程属于当前用户且映像是已注册 App。

- 回合结束后 app-server 可能延迟释放 writer，App 仍可能短暂显示会话锁定；重新进入或稍等
  即可。
- App 运行但未打开目标会话时，桥接按“无法跟随”处理并稍后重试。相同失败原因不重复刷日志。
- App 更新后身份校验失败时，完全退出并重新打开 App，再重启桥接。
- VS Code 的 Codex 扩展、App 本体和其它 resume 同一 `CODEX_HOME` 的客户端可能抢占管道或
  writer。桥接不会为了跟随陌生宿主而放宽身份校验。

Windows 命名管道按用户单例，只跟随主 `CODEX_HOME`，不承诺跨 home 跟随；Linux 可以为显式
配置的后端使用对应 home 的私有 socket。

## 共享 daemon

标准 `app-server` 后端在 Linux 默认共享，在 Windows 默认独立，以分别适配 CLI 为主和 App
为主的工作流。可用 `/settings shared true|false` 覆盖；模式切换会重建连接，运行中的任务会
被拒绝。

共享启动失败时，本次运行回退独立进程，原因写入 `/status` 的 `shared_fallback` 和
`backend.shared_unavailable`，不修改持久设置。Windows 可通过 `useSystemProxy` 读取系统
代理；显式代理环境变量优先。

## 平台适配

| 领域 | Linux | Windows |
| --- | --- | --- |
| 后台 | systemd 用户 unit | Scheduled Task + supervisor |
| 服务日志 | `journalctl --user -u codex-wechat` | `service.log` 及轮转文件 |
| 权限 | 实际 `euid == 0` 拒绝 | 管理员令牌拒绝 |
| App IPC | 同用户私有 Unix socket | 命名管道 + 进程身份核对 |
| 文件权限 | POSIX mode | DACL / `icacls` |
| 系统代理 | 环境变量或 GNOME manual 代理 | 环境变量或 WinINET |
| 桌面通知 | `notify-send` | PowerShell `NotifyIcon.ShowBalloonTip` |

二维码优先使用 PATH 中的 `qrencode`；不可用时按需安装 Python `qrcode` 回退。两者都不可用
时必须保留原始 URL。通知是尽力而为：工具缺失、超时或异常返回失败，不伪造已通知。

## 命令与权限

命令和别名见 [生成参考](REFERENCE.generated.md)。平台参数只改变拼写，不改变语义：

| Linux | Windows |
| --- | --- |
| `--follow` | `-Follow` |
| `--service` | `-Service` |
| `--stop-active` | `-StopActive` |
| `--purge-data` | `-PurgeData` |

公开入口只有 `codex-wechat`；`service-stop`、`service-status`、`codex-wechat cli` 和
`codex-wechat desktop` 不存在。`windows/task-service.ps1`、`uninstall.py` 和
`uninstall.ps1` 是内部 helper，不是用户命令。

服务未安装时 `autostart` 查询返回 `false`。版本化入口只接受 16 位小写十六进制 release ID；
拒绝路径穿越、短 ID、大写和非法长度。损坏的 `current` 必须给出修复指引，不能猜测其它版本。

## 明确边界

- 不提供 macOS、WSL、群聊、企业微信、Telegram、飞书或 Discord 适配。
- 不承诺 App 主动打开或聚焦未打开会话；只跟随已经打开的 thread。
- 不把 Windows toast、命名管道多 home 或三端并发写成已验证能力。
- 不出站下载任意远程 URL 后转发；附件只从已核对 CDN 接收，或回传已存在的本机文件。
- CDN 上传重试幂等性未证实；上传不在原处盲目重试。
- App IPC 依赖非公开、版本相关的内部接口，必须按 [SOURCES](SOURCES.md) 的版本边界使用。
