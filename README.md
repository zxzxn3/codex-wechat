# codex-wechat

通过微信使用本机 Codex，会话、模型、工具和认证沿用 Codex。Linux 与 Windows 共用一套程序，平台服务分别使用 systemd 用户服务和 Windows 计划任务。普通 Codex、App 内置 CLI 和自定义启动入口使用相同的 App Server 接口；codex-deepseek 可作为独立后端配置，不是安装前提。

## 安装与更新

公开仓库地址尚未确定，下面的 `OWNER/REPO` 需要替换。同一入口首次安装，已有安装则更新；也可先下载脚本审阅。当前使用源码与独立 Python 环境。

Linux：

```bash
bash -o pipefail -c 'curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/bootstrap.sh | bash -s -- --repo https://github.com/OWNER/REPO --ref main'
```

Windows PowerShell：

```powershell
& ([scriptblock]::Create((Invoke-RestMethod -UseBasicParsing https://raw.githubusercontent.com/OWNER/REPO/main/bootstrap.ps1))) -Repo https://github.com/OWNER/REPO -Ref main
```

本地源码运行 `python3 installer.py`；Windows 可用 `py -3 installer.py`。菜单提供安装/更新、附加组件和退出。`--dry-run` 展示流程，不写文件、不联网，环境检查仍提示但不阻断演练；`--non-interactive` 采用保守默认。重复运行原安装命令即可更新，也可从已安装入口执行 `update`。

需要 Python 3.11+。系统工具与缺失策略集中于 installer.py，Python 包在 requirements 文件中声明；缺少 Codex 不妨碍安装，但模型任务在补齐前不可用。可使用独立 CLI 或 App 内置原生 CLI。二维码优先用 qrencode，缺少时安装轻量 Python 回退。

安装不扫码、不收集模型密钥、不自动启动程序、不为新用户开启自启。更新保留用户配置和自启状态，不重启正在使用的桥接。新版本先独立准备和检查，危险操作集中在最后激活；不提供安装回滚命令。

## 第一次使用

Linux：

```bash
~/.local/share/codex-wechat/codex-wechat start
```

Windows：

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" start
```

未绑定时仅交互式 start 显示二维码；扫码后继续启动。已绑定则直接运行，后台或重定向环境不会等待不可见的扫码。在微信发送 `/help` 查看当前指令。需要自启时使用 `/settings autostart on`；关闭自启不终止当前进程。

## CLI 与 App 共用会话

本地共享连接优先复用原生 daemon，仍可显式配置独立连接。桥接退出只关闭自己的连接/代理进程，不结束共享 Codex 后台。

App 共享接入需要一次配置，在空闲且退出原 App 后执行：

```bash
codex-wechat desktop setup
codex-wechat app
codex-wechat cli -- resume
```

若入口不在 PATH，使用前述完整路径。Windows 对应：

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-desktop.ps1" setup
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" app
& "$env:LOCALAPPDATA\codex-wechat\app\codex-desktop.ps1" cli -- resume
```

setup 保留其它配置，为选定后端设置本机共享端点，之后需重启微信桥生效。`--backend {名称}` 选择后端，`--executable {绝对路径}` 指定 CLI/App 内置 CLI。端口由系统分配后保存，也可显式指定 `--url ws://127.0.0.1:{端口}`。不能以同一个 CODEX_HOME 代替连接同一个后台，不能通过删除写入锁解决冲突。

**代理启动器只负责代理并转交命令。** 项目接入入口不读取系统代理、不维护代理配置；后台管理在运行层，App / CLI / 微信共用连接配置。代理环境在实际后台启动时继承，改变客户端环境不会修改已经运行的后台。

App 的 WebSocket 接口属于版本相关实现，公开 App Server WebSocket 也仍为实验性。版本、依据及未验证组合见 [SOURCES](docs/SOURCES.md)。

## 日常行为

- 普通消息默认 steer；`/queue {prompt}` 在空闲后独立执行，且不挡后续 steer。
- 可列出/搜索/翻页会话、切换后端、模型、思考强度与目录；控制指令由程序处理。
- 中途完整回复直接发往微信，不等整轮结束，不发送逐字增量、推理或工具流水。
- 图片与文件用手机原生附件发送。附件 skill 可从安装菜单单独安装；其它路径可显式 `/send`。
- 原生提问与审批使用专门指令回应；桌面请求只处理实际送到此连接的部分，不冒充已接管全部请求。
- `/stop` 中断当前共享轮次并清待办，不能撤销已发生的操作；`/restart` 保留确定未提交输入。
- 重启后不自动重放已接受或提交结果不确定的输入；当前不补发断线期间的所有历史回答。

提示、切片、限额阈值、压缩通知及来源 hint 在同一 config.json 中热加载。接收/结束提示也可在微信即时修改；已提交输入和已入发件箱的文本不追溯改变。`/ack fields` 列出模板字段，使用缓存快照，不额外请求模型。

AI 正文及中途回复按结构智能切片；控制回复仅在传输超限时拆分。`/ping` 测微信观测延迟和供应商 TCP/TLS 建连阶段，不调用模型，不代表模型响应速度或手机显示 RTT。

## 微信绑定失效

确定认证失效后暂停微信收发和新输入提交，保留待办与发件箱，通过系统通知给出指引。正在运行的 Agent 不被擅自终止，结果保留待处理；普通断网只重连。

```bash
codex-wechat rebind
```

Windows 使用 `.ps1 rebind`。等待扫码时服务继续运行，仅提交绑定时短暂停服并加锁；扫码失败或取消不会停服。同一微信号重绑沿用原聊天，保留待办、回复和去重记录；bot 变化时更新凭据、重置游标及协议上下文，在微信发一条消息后继续交付。换微信号以手机扫码授权为准，自动隔离旧交付，不在终端重复确认。Agent 会话选择和偏好始终保留。尚有已提交工作时需明确 `--stop-active` / `-StopActive`；排队消息本身不阻止重绑。

系统通知是尽力投递，通知被关闭或无桌面时仍可从本机 status 和日志查看重绑状态。

## 诊断日志

Linux 使用 `codex-wechat logs`，Windows 使用 `.\codex-wechat.ps1 logs`；追加 `--follow` /
`-Follow` 持续查看。结构化日志写入数据目录的 `logs/bridge.jsonl`，按 5 MiB 轮转并保留
3 份备份。Windows 的 `logs/service.log` 仍保存后台 supervisor 和子进程的控制台输出；
Linux 的 systemd 控制台输出仍在 `journalctl --user -u codex-wechat`。

`bridge.jsonl` 每行一个 JSON 事件，包含时间、级别、事件名、平台、版本、进程号、随机
请求号和必要元数据，用于关联收信、提交、API 请求、发送重试与后台恢复。字段采用白名单；
不记录聊天正文、prompt、回复、文件名、账号标识、token、context token、Authorization、
请求/响应体或绝对路径。错误仅保留异常类型和错误码；URL 只保留 endpoint，不带查询参数。
日志写失败不会影响收发。默认级别为 `info`，排障可用环境变量
`CODEX_WECHAT_LOG_LEVEL=debug|info|warning|error` 临时调整。

需要反馈问题时优先提供 `logs/bridge.jsonl`；`service.log` 是底层进程输出，可能比结构化
日志更杂。日志只保存在本机，不自动上传。

## 配置与数据

示例见 config.example.json；实际 config.json、账户和交付状态不入库。Linux 默认数据在安装目录；Windows 在 `%LOCALAPPDATA%\codex-wechat`，程序在其中 app 目录。可用 CODEX_WECHAT_DATA_ROOT / CODEX_WECHAT_INSTALL_ROOT 明确分离位置。

源码、配置、`.state`、共享后台记录和日志分开。传输临时文件在成功和失败后均清理；用户原文件和已保存的附件不自动删除。凭据不放命令行、不复制供应商密钥，不提高原生沙箱权限。自动回传只允许工作目录内非隐藏文件或私有媒体目录，不允许外发桥接凭据与状态。

服务与交付边界见 [ARCHITECTURE](docs/ARCHITECTURE.md)，能力迁移基线见 [CAPABILITIES](docs/CAPABILITIES.md)。测试报告区分离线覆盖与实机结果；Linux mock 通过不能代替 Windows、真实微信或多客户端联调。
