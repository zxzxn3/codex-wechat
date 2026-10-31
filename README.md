# agent-wechat

微信文字消息 ↔ 本机 Codex App Server。支持普通 Codex 与 codex-deepseek，在微信内切换后端；无需 OpenClaw、模型 API 中转或额外 Python 包。

**验证状态：用户已实际完成扫码和基本对话。** 新增的后端切换和服务启动方式尚待用户实用验证；本文不把它们写成已验证通过。

## 运行

依赖 Linux、Python 3.11+、已登录且可正常使用的 Codex CLI。开发时本机 CLI 为 0.166.0；已核对该版本导出的 JSON Schema。绘制登录二维码用 `qrencode`，本机已安装。

```bash
cd ~/.local/share/agent-wechat
python3 agent_wechat.py login
python3 agent_wechat.py start
```

`login` 在终端生成二维码，用手机微信扫描并授权。配对码只在本机终端输入。绑定后，只有扫码者的私聊文字会进入 Agent；群聊和其他用户消息被丢弃。首次连通需在微信里主动发一条文字消息。

`start` 前台运行，Ctrl+C 停止。常驻和自启使用下面的安装脚本。电脑需要在线。微信 HTTP 请求使用标准库的系统环境代理，Codex 则由现有 `codex` 启动器管理网络。

默认工作目录为项目下的 `workspace/`。普通 Codex 配置、登录、模型、工具和权限沿用自身默认机制；不会复制配置，不会强制关闭审批或提升沙箱权限。微信入口默认创建独立会话，并在重启后恢复其 thread ID；也可通过 /sessions 和 /switch 选择普通 Codex 已有会话。仍须遵守原有 AGENTS.md 等规则。

状态及凭据保存在 `.state/`（目录 0700、文件 0600，已忽略）。本地运行 `python3 agent_wechat.py status` 查看不含凭据的绑定及队列摘要。它只表示最后保存的状态，不是服务健康检查。

## 微信命令

- 普通文字：提交任务；忙时排队，最多 20 条。
- `/status`：工作/空闲、待办数、审批数，以及当前后端、会话名称、ID、模型、思考强度和工作目录。
- `/backend`：显示当前和可用后端；`/backend codex`、`/backend deepseek` 切换。要求空闲且无待办；失败保留原后端。
- `/stop`：请求中断当前任务并清空未执行待办，不撤销已经发生的文件或外部修改。
- `/sessions [关键词]`：列出最近更新的 10 个非归档主会话（CLI、VS Code、App Server），可按名称搜索；不读取或显示对话正文。
- `/sessions next`：下一页。编号始终对应最近一次显示的列表。
- `/switch 编号或完整ID`：切换到已有会话，保持目标会话自己的设置。
- `/new`：立即创建新会话，采用配置的初始默认值；旧会话仍在 Codex 中。
- `/model` 或 `/models`：实时查询当前后端模型列表；`/model 编号或完整模型名` 切换。编号对应最近一次模型列表。切换模型时采用新模型返回的默认思考强度。
- `/effort`：显示当前模型支持的档位；`/effort 档位` 切换。不硬编码模型或强度清单。
- `/cwd`：查看目录；`/cwd 路径`：切换到已存在的目录，支持 `~`、绝对路径以及相对当前工作目录的路径，不自动创建目录。
- `/ack`：查看处理提示；`/ack on`、`/ack off` 开关；`/ack text 自定义文字` 修改文本。默认开启，默认文本 `::`，修改后跨重启保留。
- `/approve 编号`、`/deny 编号`：处理一次命令或文件审批。没有整会话授权快捷键。
- `/restart`：空闲且无待办时重启消息桥用户服务，加载已更新的本地代码和配置；不更新或下载代码。
- `/help`：命令说明。

新建、恢复或切换会话，以及修改模型、强度、目录后，会显示会话基本信息；模型、强度和目录取自 Codex 响应，未返回的具体强度明确显示为默认。没有已有会话时，首次普通消息才创建并显示；已有会话重启桥接时自动恢复并显示（需要已有微信 context token）。

模型/强度/目录的微信覆盖按会话保存在状态目录，重启或重新切换会话时恢复；不修改全局 Codex config.toml。新建/切换会话或修改这些设置要求空闲且没有待办；否则先 /stop 并等待结束。服务端报告目标会话 active 或 systemError 时会拒绝切入。不会因命令失败而自动创建替代会话。

回复只发送最终回答，不转发推理和命令输出。没有 phase 字段的旧版消息作为兼容回退。长回复按 UTF-8 字节分段。图片、文件、语音、群聊及复杂工具表单交互尚不支持。审批内容过长或不受支持的服务端交互会拒绝，不能在手机上批准看不全的请求。

不要在桌面端同时操作微信选中的会话；跨进程并发接管尚未实现。若不支持的交互需要在电脑处理，应先停止桥接，再用 `codex resume <thread ID>` 接续。

## 配置及扩展

无须配置即可使用普通 Codex；要修改时复制 `config.example.json` 为 `config.json`。配置是本地文件，不纳入版本管理。可选顶层 `cwd` 必须是希望 Agent 操作的工作目录；建议使用绝对路径。

可选 `processingNotice` 对象设置处理提示初值，例如 `{"enabled": true, "text": "::"}`；微信 /ack 的持久设置优先于此配置。参见 config.example.json。

`backend.command` 是程序和参数数组，通过 exec 启动，不经过 shell；`backend.env` 仅为后端进程覆盖环境。桥接会移除继承的 CODEX_HOME、CODEX_THREAD_ID、CODEX_SESSION_ID，再应用此覆盖，防止意外使用入口代理的 DeepSeek home。`backend.threadOptions` 直接传递给 thread/start 和 thread/resume；留空沿用 Codex 默认。不要在配置里存放 API 密钥。

内置后端在 `profiles.py` 中集中定义：普通 Codex 使用已有默认命令；DeepSeek 使用现有 `codex-deepseek app-server` 启动器，由它管理独立 home、模型和桌面密钥环。无需再次扫码，也不复制微信凭据。DeepSeek 尚未在本项目内运行联调；已有启动器本身的日常使用不能算本桥接的验证证据。

可选 `backends` 对象按名称覆盖各后端的 command/env/threadOptions；旧版顶层 `backend` 配置仍作为 codex 后端配置使用。不要把旧 codex 后端的 command 改成 DeepSeek 来代替 `/backend deepseek`，否则会混用保存的会话归属。安装入口会在系统默认 PATH 前加 `~/.local/bin`，保留你已有的两个启动器。

桥接在同一私有状态目录内按后端隔离会话 ID、列表编号、分页游标与会话设置；切回时恢复该后端上次会话。微信收发游标、待发回复和 /ack 设置由网关共用。重启恢复最后选中的后端。旧版单后端状态自动归入 codex，不修改正在运行的旧进程的磁盘状态。任一时刻只保留选中后端的桥接连接，首次选择后端时才启动它，切换成功后关闭旧连接；切换前不会打断工作中的任务。

更一般的后端只需实现 `backend.py` 中的接口，微信层不依赖模型供应商。

## 安装与图形登录自启

沿用 `ime-cursor` 的项目内服务单元和安装脚本惯例：

```bash
cd ~/.local/share/agent-wechat
./install.sh
```

脚本可重复运行，建立 `~/.local/bin/agent-wechat` 和用户服务单元的符号链接，拒绝覆盖无关文件，执行 daemon-reload 并 enable。默认在已绑定且没有前台桥接占用状态目录时请求 start；已有进程保持不动，不调用 restart。只安装启用、不启动可用 `./install.sh --no-start`，本次采用此方式。

服务随 `graphical-session.target` 启停，含义是**开机进入图形桌面后自动启动**，不启用 linger、不在登录前运行，因为 codex-deepseek 依赖桌面密钥环解锁。只有一个微信网关服务，无需同时开两个争用微信 token 的服务。

把现有前台运行交给服务：在原终端按 Ctrl+C，然后执行：

```bash
systemctl --user start agent-wechat.service
journalctl --user -u agent-wechat.service -f
```

以后改代码后可在微信发 `/restart`，也可在电脑执行 `systemctl --user restart agent-wechat.service`。微信指令在有任务、待办或会话切换时拒绝重启，请先 `/stop` 并等任务结束。它先检查当前进程确实是 `agent-wechat.service` 的 MainPID，再等待待发回复（包含重启提示）成功发送，最多 20 秒；发送未完成则取消重启。然后用 `systemctl --user --no-block restart` 提交重启请求，避免进程等待自己的重启任务。新进程启动后会发送提示，并照常尝试恢复最后的后端和会话；这不代表模型或微信通道已通过健康检查。

`/restart` 只重启网关及其后端连接，不重启电脑、不重新扫码，也不做 git pull。前台运行时该指令拒绝执行，请先按前面的步骤交给服务。首次加入本指令后，旧进程还不认识它，需要在电脑端手动启动或重启一次服务；以后更新即可用微信指令加载。电脑端直接 restart 会中断工作，先等空闲。停用自启用 `systemctl --user disable --now agent-wechat.service`。重新扫码前先停止服务，再 `agent-wechat login`，完成后启动服务。

服务以当前用户运行，使用原 `.state/` 登录、队列和配置，不重新绑定。服务条件检查默认账号文件是否存在，未绑定时跳过启动；进程异常退出 10 秒后重试，不把 systemctl 的启动成功当成模型或微信健康检查。网络与密钥环仍需在桌面会话内可用。

## 恢复与限制

入站消息与轮询游标在同一快照中保存，消息 ID 做最近 2000 条去重。开始执行前记录 running；结果与待发送回复共同落盘。崩溃发生在正在执行时，下次启动会提示人工检查，不重放可能产生副作用的任务，并清空当时的待办。未开始执行的持久待办可在正常重启后继续。

回复发送失败会重试，client_id 保持不变；不保证服务端支持去重，因此网络响应丢失时可能重复收到回复。游标和去重避免常见重复投递，但没有跨微信、Codex、本地文件的分布式事务，不承诺 exactly-once。控制命令先保存收件游标再执行，恰好在此时崩溃的控制命令可能需要重发。

后端任务故障会保留微信控制通道，报告失败并清空待办；不会自动重放任务，可通过 /backend 切到其他后端。网关进程崩溃时待发回复仍在 outbox，重启后继续发送。无法自动恢复过期的微信凭据和 context token；若出现 API -14，请停止桥接并重新 login。不同微信身份必须使用不同状态目录。状态目录包含消息、回复及登录 token，请按私密数据管理，不要上传或纳入仓库。

## 验证交接

用户选择以实际使用代替专用测试；本次新增功能未执行自动测试。保留的离线用例只覆盖初始桥接，并不覆盖新增管理命令。离线用例只使用临时目录、伪微信及伪 App Server，不调用真实模型或微信：

```bash
python3 -m unittest discover -s tests -v
```

当前按用户要求不派操作测试、不干扰正在使用的实例。安装后的使用由用户自行验收：/help、/backend deepseek、/backend codex，以及各后端的 /sessions 和 /status；/restart 的实用验证会重启服务，须由用户在空闲时自行进行；不要把文档或静态检查当成这些路径已运行通过。

文档依据及版本边界见 [docs/SOURCES.md](docs/SOURCES.md)。
