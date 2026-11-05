# 命令面

用户命令只有一个公开入口：Linux 的 `codex-wechat`，Windows 的
`%USERPROFILE%\.local\bin\codex-wechat.cmd`。Windows 入口是薄启动器，最终也转到
`codex_wechat.py`；原生计划任务操作只存在于内部 helper
`windows/task-service.ps1`，不是用户命令。

## 入口

| | Linux | Windows |
|---|---|---|
| 主入口 | `~/.local/bin/codex-wechat` | `%USERPROFILE%\.local\bin\codex-wechat.cmd` |
| 安装入口 | 源码目录的 `install.sh` | 源码目录的 `install.ps1` |
| CLI/共享后端 | `codex-wechat cli -- ...` | `codex-desktop.ps1 cli -- ...` |

Windows 的 `.cmd` 只转交稳定的 `codex-wechat.ps1`；后者只负责定位当前 release 并调用
Python，不再实现服务状态机。`codex-desktop.ps1` 保留给需要显式 `websocketUrl` 的 CLI
连接；Windows 暂不增加 `codex-wechat cli` 薄转发，这是有意的功能不对称。
安装器不会修改 PATH；只有 `~/.local/bin` 已存在且在 PATH 中时才会生成 `.cmd`。

## 命令

| 命令 | 说明 |
|---|---|
| 无参数 | 默认执行 `status` |
| `start` | 前台启动。服务正在运行时拒绝，避免争抢状态目录 |
| `service-start` | 启动已注册的后台服务；未绑定时在当前终端扫码。前台桥接正在运行时拒绝 |
| `stop` | 停止后台服务；前台桥接用 `Ctrl+C` 停止 |
| `service-restart` | 重启已注册的后台服务；两个平台都等待服务稳定运行，超时才报错 |
| `status` | 输出桥接状态 JSON；`service` 段包含 `installed`、`running`，可用的原生状态还会带 `state` 和 `last_result` |
| `logs [--follow] [--service]` | 查看结构化日志；Windows 参数写作 `-Follow` / `-Service`。Linux 的 `--service` 读取 systemd journal，Windows 读取 `service.log` |
| `login` | 交互式扫码登录；后台服务必须已停止 |
| `rebind [--stop-active]` | 重新绑定微信；Windows 写作 `-StopActive` |
| `autostart` | 查询登录自启，输出 `true` / `false`；服务未安装时也是 `false` |
| `autostart true\|false` | 开启或关闭登录自启；只改登录触发器，不启动、停止或接管当前桥接 |
| `uninstall [-PurgeData]` | 停止并移除后台服务与命令入口；默认保留用户数据。Linux 用 `--purge-data`；稳定入口不依赖 `current` |
| `update` / `install` / `menu` / `components` | 转交同一个 `installer.py` |
| `cli -- ...` | Linux 专用；连接桥接使用的共享后端，参数原样转发 |

不再存在：

- `service-stop` / `service-status`：用 `stop` / `status`。
- `codex-wechat desktop`：直接运行保留的 `codex-desktop` / `codex-desktop.ps1`。
- `autostart on|off`：只接受 `true|false`。

## 平台行为

### 互斥

`start` 在后台服务运行时直接拒绝；`service-start` 在前台桥接持有状态锁时直接拒绝。
判断在 `codex_wechat.py`，不依赖 PowerShell 或 shell 的重复实现。

### 权限

Linux 以实际 `euid == 0` 判断 root，Windows 以管理员令牌判断提权。除只读 `status`、
`logs` 和显式 `uninstall` 外，安装、更新、启动、登录、重绑、自启和服务控制都要求普通用户。
容器/一次性 CI 可显式设置 `CODEX_WECHAT_ALLOW_ROOT=1`。

### 卸载

Windows 的公开入口把 `uninstall` 直接交给稳定的 `uninstall.ps1`，因此不依赖活动 release；
原生计划任务和共享后台清理仍由该 helper 负责。
Linux 停止并移除用户 unit、命令软链；默认保留 `config.json`、`.state`、`workspace`、
`logs` 和 `shared`。`--purge-data` 才删除这些用户数据；不会递归删除自定义数据根目录，
只清理上述已知项。卸载前会检查前台桥接的状态锁；仍在运行时要求先 `Ctrl+C` 退出。
若无法读取后台服务状态，卸载会 fail-closed 而不是继续删除；没有 systemd 客户端时视为
没有可移除的服务。
Windows 自定义数据目录由 `CODEX_WECHAT_DATA_ROOT`（或原生 helper 的
`-DataRoot`）传递，Python 不会忽略它。

Linux 的 stable `codex-wechat` 和 Windows 的 `codex-wechat.ps1` 在读取 `current` 之前
直接分派 `uninstall` 到稳定卸载组件。这样 `current` 缺失、损坏或活动 release 不完整时，
仍能移除服务和命令入口，而不是先因为版本指针错误而无法卸载。

服务未安装时，`autostart` 查询统一返回 `false` 并退出 0；只有同名服务/任务属于另一个
安装目录时才报错。

### 版本指针

Linux stable 入口、Windows `codex-wechat.ps1`、Windows `codex-desktop.ps1` 和
`bootstrap_supervisor.py` 都只接受 16 位小写十六进制 release ID。`..`、斜杠和其它
非规范值直接拒绝；PowerShell 入口使用大小写敏感匹配，避免大写 ID 被发现后再变成另一路径。

## Windows 待实机验收

- 新薄入口与 `.cmd`：`start`、`stop`、`status`、`service-start`、`service-restart`、
  `autostart true|false`、`uninstall`、`uninstall -PurgeData` 的完整闭环。
- 内部 `windows/task-service.ps1` 的 `Running` 稳定性校验，以及前台锁/后台服务的双向互斥。
- 管理员终端拒绝，以及 `status`、`logs`、`uninstall` 例外；`codex-desktop.ps1` 同样拒绝管理员。
- `autostart true|false` 对已有计划任务、缺失任务、foreign task 的结果。
- 卸载默认保留数据、`-PurgeData` 清理数据，以及 `.cmd` 安全删除。
