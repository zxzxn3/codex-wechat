# 跨平台差异与决策

更新：2026-11-06。本文记录用户可观察或影响运行契约的平台差异，以及每项的理由。
它不是逐行枚举所有 `if IS_WINDOWS`，而是按职责合并同类实现。

安装、更新和卸载流程见 [INSTALL](INSTALL.md)；命令参数和稳定入口见
[COMMANDS](COMMANDS.md)。

## 已确定

| 领域 | Linux | Windows | 决策与理由 |
|---|---|---|---|
| 公开命令入口 | `~/.local/bin/codex-wechat` | `%USERPROFILE%\.local\bin\codex-wechat.cmd` 转交 stable `codex-wechat.ps1` | Windows 的 `.ps1` 不在 `PATHEXT` 中，且受 PowerShell 执行策略限制；`.cmd` 是兼容 cmd 和 PowerShell 的薄 shim。安装器不修改 PATH |
| 安装入口 | `install.sh` | `install.ps1` | 只做平台 shell 适配，最终都调用同一个 `installer.py` |
| 参数拼写 | `--follow`、`--service`、`--stop-active`、`--purge-data` | `-Follow`、`-Service`、`-StopActive`、`-PurgeData` | POSIX shell 与 PowerShell 的参数习惯不同，语义相同 |
| 命令入口安装 | 创建 `~/.local/bin/codex-wechat` 链接 | 仅当 `%USERPROFILE%\.local\bin` 已存在且在 PATH 中时创建 `.cmd` | 安装器不修改 PATH；Windows 不主动创建未受管理的 PATH 目录，否则提示使用完整入口路径 |
| 程序与数据目录 | 默认安装根目录同时保存程序、配置和状态 | 程序在 `%LOCALAPPDATA%\codex-wechat\app`，数据根默认 `%LOCALAPPDATA%\codex-wechat` | 遵循平台目录约定；Windows 将不可变程序与用户数据分开。两边都可用 `CODEX_WECHAT_DATA_ROOT` 覆盖数据根；Linux 后台服务由安装器把值写入 systemd drop-in，Windows 入口、Python 命令和计划任务共用该值 |
| 后台服务 | systemd 用户 unit | Windows Scheduled Task | 使用各平台原生服务生命周期；不强行抽象成统一的跨平台服务 API |
| 服务归属验证 | unit `FragmentPath` 与 systemd `MainPID` | 计划任务 action、venv 可执行文件、进程父子链、所有者 token | 防止误停其它安装或外部进程。Windows 没有与 systemd `MainPID` 等价的单一校验点 |
| 服务重启 | `systemctl restart`（阻塞）后由 Python 等待稳定 | Python 先执行原生 stop、等待停止，再 start 并等待稳定 | Windows 子进程直接重启计划任务会死锁或被忽略，因此由 supervisor 负责服务内重启；CLI 的重启编排和稳定等待统一在 `service_adapter.py`，PowerShell helper 只发原生命令 |
| 登录自启 | systemd `enable` / `disable`，关闭后重新 link 以保留按需启动 | Scheduled Task logon trigger | 使用各平台原生登录机制；对外命令统一为 `autostart true\|false` |
| 崩溃恢复 | unit `Restart=on-failure`、固定退避和启动次数限制 | supervisor 的退出码、重启退避和异常退出计数 | Linux 由 systemd 管理，Windows 由项目 supervisor 管理 |
| `logs --service` 来源 | `journalctl --user -u codex-wechat` | `logs/service.log` | 读取各平台服务控制台的原生来源；结构化 `bridge.jsonl` 两边共用。Windows `service.log` 按 5 MiB 轮转并共用 `logs.keep`；Linux 由 journald 管理 journal 保留 |
| `status.service` 字段值 | `state=active/inactive/...`，`last_result=ExecMainStatus` | `state=Running/Ready/...`，`last_result=LastTaskResult` | JSON 字段形状相同，但保留原生状态词，不伪造统一枚举 |
| 权限判断 | 实际 `euid == 0` | 管理员令牌 `IsInRole(Administrator)` | 实现不同、策略相同；只读 `status`/`logs` 和显式 `uninstall` 例外 |
| 文件权限 | POSIX `chmod 0700` | DACL / `icacls`，显式授予当前账户、SYSTEM、Administrators | Windows 的 0700 会变成“仅所有者”，提权安装时可能锁住实际服务账户 |
| App IPC 传输 | 同一 `CODEX_HOME` 下的 Unix socket | `\\.\pipe\codex-ipc` 命名管道 | 使用各平台原生 IPC；Windows 管道由最先启动的客户端托管，需要额外核对宿主身份 |
| App IPC 的 home 范围 | 显式配置 `env.CODEX_HOME` 时可跟随对应后端 | 只跟随主 `CODEX_HOME` | Windows 是每用户单例管道，路径不携带 home；跨 home 连接可能把同名 thread 送到错误 App。该项是真实能力缺口，当前以安全为先 |
| 原生 Codex 发现 | PATH、已知 Linux App 资源路径、显式 command | PATH、独立安装目录、已注册 Appx 包、显式 command | 各平台打包布局不同，不能按另一平台的路径猜测 |
| 共享 daemon 默认值 | 标准 `app-server` 后端默认 `sharedDaemon: true` | 标准 `app-server` 后端默认 `false` | 产品默认，不是硬限制：Linux 以 CLI 为主、Windows 以 App 为主；用户应按工作流覆盖 |
| 共享后台启动 | `systemd-run --user` | `daemon start`，必要时通过 WMI 在 Job 外启动；WebSocket 使用 PowerShell helper | Windows 计划任务、Job Object 和 breakaway 语义不同；WMI 环境继承还需要一次性私有环境文件 |
| 系统代理 | 环境变量；无代理变量时读取 GNOME manual 代理 | `getproxies()` / WinINET，读取 Windows 系统代理 | **两边都会注入代理**；差异只在发现来源。`respect_system_proxy` 是实验性上游开关，当前不作为替代；`useSystemProxy:false` 两边都关闭自动注入 |
| 桌面通知 | `notify-send` | PowerShell `NotifyIcon.ShowBalloonTip` | 平台原生 UI；Windows toast 需要额外注册 AUMID，当前不冒充 WinRT toast |

## 卸载入口

正常用户使用公开入口：Linux `codex-wechat uninstall [--purge-data]`，Windows
`codex-wechat uninstall [-PurgeData]`。稳定 `uninstall.py` / `uninstall.ps1` 是内部实现，
正常用户不应直接调用。完整流程、数据保留规则和损坏 `current` 的处理见
[INSTALL](INSTALL.md#卸载)。

### 提示语言

- root/管理员拒绝统一使用「请以普通用户身份运行，不要使用 root/sudo 或管理员终端。」
- `current` 缺失、无效、非普通文件、版本不完整及 release 损坏统一使用中文提示。
- `uninstall` 的成功和保留数据提示统一使用中文；平台专用参数仍保留
  `--purge-data` / `-PurgeData` 的拼写差异。

## 已对齐

以下行为没有平台语义差异：微信命令表和短别名、回复预算、发送间隔与静默丢弃处置、
`logs.keep`（Windows 同时约束 `service.log`；Linux 控制台由 journald 管理）、结构化
`bridge.jsonl` 字段、`autostart true|false`、`status` 的 JSON
字段形状、默认保留用户数据、`start`/`service-start` 双向互斥、没有顶层
`service-stop`/`service-status`，以及卸载默认保留数据。
