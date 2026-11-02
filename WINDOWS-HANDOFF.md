# Windows 交接与合并说明

2026-11-02。供回到 Linux 后继续开发的 agent 阅读。

**用户并非满意这里的全部代码和设计。授权尝试、运行成功或本文建议，都不等于认可设计；可以删减、替换或推翻，不得因“已实现”而要求保留。** 涉及用户可见行为和既有保证的取舍仍应说明。

## 文件与基线

- 完整实验源码：`D:\codex-wechat-app-support`，Linux 为 `/mnt/d/codex-wechat-app-support`。
- 原 CLI 导出：`D:\codex-wechat-install-df8d949`，也有少量通用修复和防误用保护，不要当成未修改基线。
- 外部本机助手：`C:\Users\zxzxo\CodexProxy`；相关入口快照在实验源码 `experiments/windows-shared/`。
- 实际安装与数据：`%LOCALAPPDATA%\codex-wechat`。账户、config、state、进程记录和日志不作为 Linux 合并内容。
- 独立迁移包：`D:\CodexMigration`，不属于 codex-wechat。

两个 D: 源码目录都不是 Git checkout，Windows 改动未提交。`.export.json` 记录基线 `df8d949` 和文件哈希，没有基线内容；须从 Linux 真实仓库核对分支及此后的修改，不能凭目录名判断当前分支。

完整差异见 `WINDOWS-CHANGES.json`：两份源码相对导出的新增、修改、删除和哈希。旧 `.refactor-files.json` 不再是完整清单。这里的清单不是验收报告或用户批准。

## 已知运行结果与证据边界

- 用户在 Windows 安装、扫码、微信独立使用成功；同一手机重新绑定会替换另一系统的旧绑定，用户接受。不迁移两侧待办或 Agent 进程。
- 最初后端启动只显示 RuntimeError；显式使用 App 内置原生 codex.exe 后成功，另改善了 RPC 错误详情。
- App 和微信各用独立写入后台时，切换会报 `already has an active writer`。改成同一个回环 WebSocket 后台后，用户确认切换、收发成功；没有删除写入锁。
- 用户确认代理有效且 TUN 关闭时 App 可用。某次微信网络失败与临时切换 TUN 的关系只是推测，未证明。
- 上次更新记录为成功版本 `470eacbc1012a034`，仅证明当次安装器报告成功。后续入口整合已通过安装器更新并切换配置，精确版本见本机 CodexProxy/desktop-integration-result.json；未结束当前后台、App 或微信桥，也未重新扫码。安装成功不是新功能联调通过。
- 新 CLI `--remote` 入口、重新绑定事务、取消清理和后续结构改动未做完整联调。只核对文档、本机 help、静态语法；本轮未跑 unittest、模型请求或端到端测试；整合时实际执行了安装器及它的依赖构建检查。专用测试会话未确认可用，未擅自换测试者。

## 源码变化与合并主题

| 主题 | 文件 | 变化及边界 |
| --- | --- | --- |
| 错误详情 | commands.py、codex_wechat.py、codex_transport.py | RPC 保留方法、错误码和经过过滤的信息；须审查脱敏，不只回异常类名 |
| 子进程清理 | subprocess_io.py、wechat.py、media.py、latency.py | 合并 communicate/取消/超时/回收；桥接避免重复 cancel。起因是 Ctrl+C 后 Proactor 析构报错，不代表所有退出路径已验收 |
| 共享连接 | codex_transport.py | 仅回环 websocketUrl、可选启动命令、共享标记；不要求本地 CLI 才能附加，WS 绕过代理，关闭只断客户端 |
| 后台准备 | backend_runtime.py | 从 RPC 层抽出原 Linux systemd/Windows daemon 和外部助手启动；不新增转接协议 |
| 测速观测 | backend.py | WS 时使用桥接自己的网络环境，不读取另一进程凭据，不宣称测得实际模型请求或后台网络路径；ping 样式不改 |
| 更新门禁 | installer.py、runtime-capabilities.json | 配置要求 WS 而候选不支持则拒绝正式更新，dry-run 提示；增加构建前本地模块导入检查。能力声明方案仍可简化 |
| 旧版本校验 | installer.py、test_release_manifest.py | experiments 不再打包后，旧版曾被新筛选规则误判。现在按版本自身清单校验哈希，仍拒绝越界、重复、链接、篡改和未声明公开文件；两个 D: 副本均修，用户重试更新成功 |
| 重新绑定 | binding.py、wechat.py、codex_wechat.py、codex-wechat.ps1 | 显式 --rebind/-Rebind；不同身份默认拒绝覆盖，同身份 token 刷新不清状态。rebind 清旧交付，仅保留列出的后端/会话/两种提示偏好；先写 bindingPending 隔离部分失败，不另存 token 备份 |
| 桌面入口整合 | codex-desktop.ps1、windows/、codex-wechat.ps1、installer.py | 见下一节；项目随版本管理 App、CLI 和共享后台，外部启动器只代理与转交 |

新 rebind 方案是替换早期手动搬走整个 `.state` 的做法；先前扫码成功不证明新事务通过。清哪些偏好、如何处理部分失败仍可评估。

原导出临时省略了安装器的 CLI 依赖发现，用于 App-only 尝试；这不是正式依赖策略，不要无意合入。原 CLI 导出还明确拒绝 WS 配置，避免不支持却静默换成独立后台。

新增用例有 binding、runtime_compatibility、subprocess_io、websocket_transport、release_manifest，以及本轮 desktop_entry；另调 installer 环境隔离和 latency mock。均需由专用测试者执行，不能套用历史 Linux 报告。README、docs 和 DESIGN-REVIEW 中的评审建议也不是用户逐条批准的规则。

## 最新入口整合

- 安装目录稳定入口 `codex-desktop.ps1 app|cli|ensure` 读取 current，转交当前版本的 windows/ 脚本；installer.py 维护此入口。
- `windows/Start-SharedBackend.ps1` 管后台互斥、PID/创建时间、监听归属、拒绝未知端口及 readyz；不因 App 更新抢停已验证的旧后台。
- `windows/Start-App.ps1` 负责共享后台准备、进程级 CODEX_APP_SERVER_WS_URL、原生 App alias 启动和日志。
- `windows/Start-Cli.ps1` 复用同一后台，通过原生 --remote 运行 TUI，保持终端、当前目录和其余参数；拒绝再指定另一个 remote 或 no-daemon。
- 程序发现集中在 windows/Resolve-CodexExecutable.ps1；运行路径集中在 windows/SharedPaths.ps1。调用结束恢复调用者进程环境；客户端退出不杀共享后台。
- 共享配置与 PID 记录移至 `%LOCALAPPDATA%\codex-wechat\shared`。迁移沿用已有身份记录，不重新扫码、不结束当前 App；运行中后台的旧日志句柄仍可能指向 CodexProxy，后续新建后台使用新目录。
- 微信 startupCommand 改为安装目录的 `codex-desktop.ps1 ensure`，不再依赖外部 CodexProxy 执行后台管理。
- 外部 Start-Codex.ps1 只从现有 HTTPS_PROXY 或 Windows 系统代理取得代理，设置进程环境后调用安装入口 app；不再读共享端点、管理后台或设置 App 连接变量。保留同一个开始菜单快捷方式，不增加桌面图标。
- 外部 Start-CodexCli.ps1 / Start-SharedBackend.ps1 仅保留兼容转交。旧的外部 resolver 已无活动调用；旧配置/进程记录和后台旧日志仅保留作迁移历史，不再是活动配置源。Enable/Repair 是本机设置/更新兼容助手，读取新共享配置位置，不是通用发行安装接口。

安装后的可用入口：

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" app
& "$env:LOCALAPPDATA\codex-wechat\app\codex-desktop.ps1" cli resume
```

日常仍从原 Codex 快捷方式打开，自动经过代理层。直接用原生 App 或普通 codex 入口并不保证连接此 WS；没有设置永久用户级 CODEX_APP_SERVER_WS_URL。当前机器的端口 45231、代理 7897、源码 D: 路径属于运行配置，不应硬编码成发行默认值。

## 协议与待评估设计

使用一个 `codex app-server --listen ws://127.0.0.1:45231`，App、微信、CLI 连接同一后台。依据：

- 官方 CLI --remote：https://learn.chatgpt.com/docs/app-server#connect-the-cli-terminal-ui ，本机 help 已核对。
- 对应 CLI `0.166.0-alpha.5.1` 源码区分 stdio/WS，不能假设 App 默认 stdio 后台存在第二个监听：https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/app-server/src/lib.rs
- App `26.1027.3384.0` 打包实现支持 CODEX_APP_SERVER_WS_URL，但 Windows 不走相同的本地 daemon 分支，WS 路径未经过 stdio 的所有 getConfigOverrides。此变量是版本相关实现，不是稳定公共承诺。
- 官方将 WS 标为实验性，不承诺生产支持。这里只绑定回环、不开放 LAN、不启用 remote-control；未进行完整本地安全审计。

原生 Windows daemon 已有管理能力，但尚未证明其端点能替代这里的 App WS 路径。不要凭“存在 daemon”就宣布脚本全能删，也不要把自管后台定为必须长期保留。独立能力 JSON、rebind 白名单、模块数量、配置组织和入口形式都可重新设计。

未验证项还包括：CLI/App/微信三端并发、原生工具/配置等价、审批与提问路由、升级/重连/开机生命周期、重复取消、rebind 部分失败、Linux 回归及 codex-deepseek。普通 Codex 成功不能扩大成 DeepSeek 通过。

## Linux 合并步骤

1. 核对真实仓库、分支及 df8d949 之后的修改，以 WINDOWS-CHANGES.json 定位差异；不整目录覆盖，没有可直接 cherry-pick 的 Windows 提交。
2. 按主题审查并取舍：错误详情、取消清理、旧版校验可独立考虑；共享连接、生命周期、门禁、rebind 分开判断。同一个通用文件也包含实验改动，不按文件名盲选。
3. 不复制账户、状态、代理、Windows 路径、进程记录或一次性诊断脚本；CodexProxy 内 Refactor/Fix/Inspect/Check 脚本不是产品依赖，不要重跑或打包。
4. 保持用户已满意的紧凑提示和输入结果未知不重发、来源隔离、输出分组等既有契约。是否合并 App 实验分支须按当前用户意图决定，不能因已有代码静默扩支持范围。
5. 按当前 AGENTS 的测试路由委派、分主题提交，说明未验证项；不通过抢停当前模型或删写入锁“验证”。已有文档的“应保留”是评审建议，不等于用户批准。

## 其它 Windows 工作

迁移包 D:\CodexMigration 应独立审阅。可直接与 common.ps1.before-null-fix 对照确认：File.Replace 的空备份参数改为 `[System.Management.Automation.Language.NullString]::Value`，避免 PowerShell 5.1 把 null 转为空路径。其它迁移脚本完整历史与归属无法仅凭现存文件确认。Codex 会话/记忆、App 安装和登录不属于本项目源码合并，不从 Windows 覆盖 Linux 的 .codex。
