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

停止已安装的后台服务：

```bash
codex-wechat stop
```

Windows 使用 `.ps1 stop`。命令会先尽力向最近一次微信会话发送“微信桥正在停止。”，通知失败不阻止停服；直接使用 `systemctl stop` 不经过这条通知逻辑。前台 `start` 仍在终端按 Ctrl+C 退出。

## CLI 与 App 共用会话

本地共享连接优先复用原生 daemon，仍可显式配置独立连接。桥接退出只关闭自己的连接/代理进程，不结束共享 Codex 后台。

Linux 与 Windows App 正常启动后，可直接在微信 `/switch` 到它持有的会话，无需专用 App 启动入口。
遇到原生后台的 active writer 冲突时，桥接通过 App IPC 跟随会话，不关闭 App、
不抢占写入锁。已完成历史不回发，新消息、中途回复和最终回答仍走现有交付流程。
App 退出或 IPC 断开时不自动重发输入；重新打开 App 后用 `/switch` 或 `/backend` 重连。
自定义启动器只有显式配置后端 `env.CODEX_HOME` 才查找 IPC，不猜测它内部使用的目录。
Linux 使用同一 CODEX_HOME 下的私有 socket；Windows 使用本机命名管道，连接前核对服务进程
属于当前用户和已注册的 Codex App。Windows 仅跟随主 CODEX_HOME，不猜测独立后端目录。
Linux 已显式配置 WebSocket 时，需要后端 `env.CODEX_HOME` 才启用对应 App 的跟随，不猜测其目录。
内部协议随 App 版本变化；更新 App 后若身份检查失败，请完全退出重开。

**启动顺序限制**：IPC 只支持“先在 App 打开会话，再从微信连接”。若微信桥先通过原生
后台连接会话，随后用独立后台的 App 打开同一会话，App 可能显示被锁定。反向接入需要
桥接充当 IPC 持有者、向 App 同步状态并承接其操作，实现与维护成本较高，因此暂未支持。
App 使用系统原入口或仅提供代理的启动器；项目不再提供 App 启动命令或桌面入口覆盖。
若旧版本曾通过 `--shortcut` 创建 Linux 用户桌面覆盖，请先核对
`~/.local/share/applications/chatgpt.desktop` 中的 `X-Codex-WeChat=true`，仅移除项目创建且未自行修改的覆盖，恢复系统原入口。

CLI 仍可连接微信使用的原生后台：

```bash
codex-wechat cli -- resume
```

Windows 使用 `codex-desktop.ps1 cli -- resume`。`desktop setup` / `ensure` 仅配置和准备 CLI/微信的后台，
不启动 App、不改代理或系统桌面入口。IPC 为版本相关内部协议，依据及未验收组合见 [SOURCES](docs/SOURCES.md)。

## 日常行为

- 普通消息默认 steer；`/queue {prompt}` 在空闲后独立执行，且不挡后续 steer。`/unqueue` 撤回最后一条仍在等待的 queue（包括 steer 回退），不影响已开始的任务；剩余数量与 `/queue` 一样只计待办。
- `/sessions` 可翻页；`/sessions {搜索词}` 按标题检索。`/switch {搜索词}` 在唯一匹配时
  直接切换，多个匹配会列出候选，可再用 `/switch {编号}` 选择；也支持编号和会话 ID。
  后端、模型、思考强度与目录同样可切换；控制指令由程序处理。
- 中途完整回复直接发往微信，不等整轮结束，不发送逐字增量、推理或工具流水。
- 图片、视频与文件用手机原生附件发送。附件 skill 可从安装菜单单独安装；其它路径可显式 `/send`。
- 引用文字和局部引用会作为上下文交给 Agent；带转写文本的微信语音可直接作为输入。
- 原生提问与审批使用专门指令回应；桌面请求只处理实际送到此连接的部分，不冒充已接管全部请求。桌面 async 提问只从结构化 `agentMessage.questions` 识别，不把正文中的类似标记当成题目。
- `/stop` 中断当前共享轮次并清待办，不能撤销已发生的操作；`/restart` 保留确定未提交输入。
- 重启后不自动重放已接受或提交结果不确定的输入；当前不补发断线期间的所有历史回答。

提示、切片、限额阈值、压缩通知及来源 hint 在同一 config.json 中热加载。接收/结束提示也可在微信即时修改；已提交输入和已入发件箱的文本不追溯改变。`/ack fields` 列出模板字段，使用缓存快照，不额外请求模型。
`connectionIdleSeconds` 控制微信 API 连接空闲保留秒数，默认 60，允许 0–3600。
0 表示不主动按空闲时间回收；服务退出、对端或代理断开、连接故障仍会关闭连接，不保证永久有效。
在 config.json 中设置如 `"connectionIdleSeconds": 2`，下次热配置读取时生效，无需重启；
`/settings` 显示当前值。已发出的请求不中断，空闲池按新值与已空闲时长重新计时；
删除此键恢复 60。附件传输也复用连接池并受此设置影响；`/ping` 保持独立建连。

`quotaWarningThresholds` 必须分别配置主窗口和次窗口，当前分别对应 5 小时与 7 天限额：

```json
{
  "quotaWarningThresholds": {
    "primary": [50, 25, 10, 5, 1],
    "secondary": [50, 25, 10, 5, 1]
  }
}
```

两个数组独立触发和去重；空数组表示关闭该窗口的提醒。该配置不保留单个共享数组。

热配置无效时沿用原设置，并提示一次；诊断日志记录失败类别和异常类型，不记录配置内容。
同一文件版本不重复提示或记录，修复后在下次读取时正常应用，无需重启。

运行期提示的默认值和可覆盖名称集中在 [prompts.py](prompts.py) 的 `DEFAULT_PROMPTS`。
在 config.json 的 `prompts` 中只填写要覆盖的项，删除覆盖即可恢复默认，例如：

```json
{
  "prompts": {
    "QUEUE_CONFIRMED": "Queue {count} {unit}",
    "RESTART_STARTED": "Restarting...",
    "LABEL_MODEL": "Model",
    "HELP_STATUS": "当前状态"
  }
}
```

动作、错误、压缩、限额及重绑通知在生成时读取新值；状态/设置标签、会话动作及帮助说明也支持。
这不是完整翻译层：状态值（如“运行中”）、连接类型、会话标记及少量内置回退文字仍固定；
可覆盖范围以 `DEFAULT_PROMPTS` 中的名称为准。
可使用 `/ack fields` 中的快照字段及该模板的局部字段，局部字段优先；替换只执行一次，
字段名统一为小写英文snake_case，例如 `{input_tokens}`、`{output_tokens}`、
`{total_tokens}`、`{context_remaining}`、`{model}`、`{ingress_ms}`、`{egress_ms}`、
`{elapsed}`（本回合从派发到结束的耗时，尚无样本时为 `-`）。
完整共享字段以 `/ack fields` 或 `/finish fields` 为准；旧中文字段不再识别，已有自定义模板需改为新名称。
显示标签及命令用法中的参数说明仍可使用中文，它们不是模板变量。
未知字段保留原样，字面花括号写成 `{{`、`}}`。每项最多3000 UTF-8字节，未知名称、
非文本值、损坏JSON或非法格式会拒绝整组热更新并保留旧设置。只支持 `{字段}`，不支持属性、
下标或格式表达式。修改的是配置覆盖；修改Python默认值仍需重启。

接收/结束提示继续使用原有 `processingNotice`、`completionNotice` 与 `/ack`、`/finish`，
不在 `prompts` 中重复设置。命令、分页结构、审批token、原生问题和Agent正文仍由程序或来源生成，
不作全文替换；已入发件箱的文本不重写。安装器、扫码/重绑终端流程及控制台日志不在此热修改范围。

AI 正文及中途回复按结构智能切片；控制回复仅在传输超限时拆分。`/ping` 分两条回复：先回微信链路耗时，再回报供应商 TCP/TLS 建连阶段，不调用模型，不代表模型响应速度或手机显示 RTT；连接阶段失败时标注路径（`直连,未配置代理` 或 `代理未响应`），没有代理环境变量时按本机系统代理测量，不改变桥接自身出站。

已知局限：实测“按住语音转文字、松开自动发送”时，手机虽显示已发送，桥接仍可能收不到；
诊断中未观察到该消息入站，原因尚未确定。可改用“点击语音转文字，文字进入输入框后手动发送”，
此方式已确认可用。此观察不代表所有微信版本都会出现相同行为。

## 微信绑定失效

确定认证失效后暂停微信收发和新输入提交，保留待办与发件箱，通过系统通知给出指引。正在运行的 Agent 不被擅自终止，结果保留待处理；普通断网只重连。

```bash
codex-wechat rebind
```

Windows 使用 `.ps1 rebind`。等待扫码时服务继续运行，仅提交绑定时短暂停服并加锁；扫码失败或取消不会停服。同一微信号重绑沿用原聊天，保留待办、回复和去重记录；bot 变化时更新凭据、重置游标及协议上下文，在微信发一条消息后继续交付。换微信号以手机扫码授权为准，自动隔离旧交付，不在终端重复确认。Agent 会话选择和偏好始终保留。尚有已提交工作时需明确 `--stop-active` / `-StopActive`；排队消息本身不阻止重绑。

系统通知失败最多尝试 3 次，间隔 5 秒；成功后保存已通知状态，全部失败则下次启动再尝试。结果记录于诊断日志。通知被关闭或无桌面时仍可从本机 status 和日志查看重绑状态；接口成功不代表通知已被用户看到。

## 诊断日志

Linux 使用 `codex-wechat logs`，Windows 使用 `.\codex-wechat.ps1 logs`；追加 `--follow` /
`-Follow` 持续查看。默认只显示结构化日志；需要排查 supervisor/子进程控制台时再加
`--service` / `-Service`。结构化日志写入数据目录的 `logs/bridge.jsonl`，按 5 MiB 轮转并保留
3 份备份。Windows 的 `logs/service.log` 是未结构化控制台输出，可能包含路径或错误文本，
不应当作隐私安全日志外传；Linux 的 systemd 控制台输出仍在
`journalctl --user -u codex-wechat`。

`bridge.jsonl` 每行一个 JSON 事件，包含时间、级别、事件名、平台、版本、进程号、随机
请求号和必要元数据。由微信输入触发的任务会用 `request_id` 关联接收、准备、提交、输出入队、
发送重试和发送 API；中途回复也携带轮次关联的请求号。同轮多条输入通过 `output.related`
与输出组 `job_id` 关联，连接/恢复等生命周期事件不强行关联到单条消息。
控制指令只记录登记的规范名称（例如 `/s` 记为 `/status`），不记录参数。
`control.started` 记录收到指令到开始处理的等待时间，结束事件记录执行耗时；
`output.send_start` 可记录回复首次发送前的等待，重启恢复的旧回复不补造该样本。
字段采用白名单；
不记录聊天正文、prompt、回复、文件名、账号标识、token、context token、Authorization、
请求/响应体或绝对路径。错误仅保留异常类型和错误码；URL 只保留 endpoint，不带查询参数。
写入和轮转由原生进程锁保护；锁竞争或写入失败时丢弃本条诊断，不等待、不影响收发。
默认级别为 `info`，排障可用环境变量
`CODEX_WECHAT_LOG_LEVEL=debug|info|warning|error` 临时调整。

微信请求另记DNS、TCP、TLS/隧道、等待首响应及下载的计时，用于区分网络建连和长轮询等待；
不测模型生成时间，不改变现有 `/ping` 或提示格式。累计时间点、阶段差值及失败时的解释见
[ARCHITECTURE](docs/ARCHITECTURE.md#诊断与隐私)，本机发送耗时观察见
[PERFORMANCE](docs/PERFORMANCE.md)。

需要反馈问题时优先提供 `logs/bridge.jsonl`。`logs --service` 显示的内容是原始控制台输出，
不承诺脱敏。日志只保存在本机，不自动上传。

## 配置与数据

示例见 config.example.json；实际 config.json、账户和交付状态不入库。Linux 默认数据在安装目录；Windows 在 `%LOCALAPPDATA%\codex-wechat`，程序在其中 app 目录。可用 CODEX_WECHAT_DATA_ROOT / CODEX_WECHAT_INSTALL_ROOT 明确分离位置。

源码、配置、`.state`、共享后台记录和日志分开。传输临时文件在成功和失败后均清理；用户原文件和已保存的附件不自动删除。凭据不放命令行、不复制供应商密钥，不提高原生沙箱权限。自动回传只允许工作目录内非隐藏文件或私有媒体目录，不允许外发桥接凭据与状态。

服务与交付边界见 [ARCHITECTURE](docs/ARCHITECTURE.md)，能力迁移基线见 [CAPABILITIES](docs/CAPABILITIES.md)。测试报告区分离线覆盖与实机结果；Linux mock 通过不能代替 Windows、真实微信或多客户端联调。
