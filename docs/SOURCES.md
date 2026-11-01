# 协议依据与兼容点

更新：2026-11-01。本轮重新查阅官方 App Server 页面，能够访问；在线页面没有列出 settings/update、thread/queue/* 或 clientUserMessageId 的持久幂等保证。依据不等于功能验证；运行结果见专用测试报告。

| 范围 | 依据与版本 |
| --- | --- |
| Codex | [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)；本机 CLI 0.166.0 导出 JSON Schema。在线文档不固定于该版本 |
| 微信 | 腾讯 openclaw-weixin 2.4.10 的 [README](https://github.com/Tencent/openclaw-weixin/blob/main/README.zh_CN.md)、[后端协议](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md) 与 src/api、src/cdn、src/messaging 对应实现 |
| WebSocket | websockets 17.4 本机实现与 [asyncio client 文档](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html)；项目支持 15–17 |
| 加密 | cryptography 50.0.2；[Cipher/AES128/ECB](https://cryptography.io/en/50.0.0/hazmat/primitives/symmetric-encryption/) 与 [PKCS7](https://cryptography.io/en/50.0.0/hazmat/primitives/padding/)；最低 38 |
| CDN 请求 | [curl 手册](https://curl.se/docs/manpage.html)，最低 8.4 以支持传输中大小限制；保留默认 Happy Eyeballs |
| 服务 | systemd 262 本机手册、[systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)、[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)、[systemd.special](https://www.freedesktop.org/software/systemd/man/latest/systemd.special.html) |
| Windows | [原生 CLI 部署](https://learn.chatgpt.com/docs/enterprise/windows-deployment)、[Python asyncio 平台差异](https://docs.python.org/3.14/library/asyncio-platforms.html)、[msvcrt 文件锁](https://docs.python.org/3.14/library/msvcrt.html)、[任务设置](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)；当前无 Windows 实机验证 |
| 管道 WebSocket / 二维码 | [websockets Sans-I/O Client](https://websockets.readthedocs.io/en/stable/reference/sansio/client.html)、[qrcode](https://pypi.org/project/qrcode/)；Windows 的 QR 使用纯 Python 控制台输出 |

## Codex

- App Server 提供 thread/start、resume、read、list、turn/start、steer、interrupt、最终消息和审批事件。steer 的 expectedTurnId 必须匹配活动轮次，不接受模型/目录等 turn-level 覆盖。
- 官方 Items / Events 将 agentMessage 与 reasoning、工具输出分开，phase 可为 commentary、partial_answer、final_answer；item/completed 为该条消息的完整权威文本。中途回复据此逐条转发，不使用 delta 拼接，也不等待 turn/completed。phase 字段可缺失时保留旧收口行为。
- 原生提问依据官方 `item/tool/requestUserInput`、`serverRequest/resolved` 和本机 0.166.0 的 ToolRequestUserInputParams/Response：回复为以 question ID 为键的 answers 字典，每题值含字符串数组。当前 schema 用 isBlocking，autoResolutionMs 已弃用；桥接不自动选择。其他客户端请求是否投递到桥接连接尚未在线确认，不根据字段存在宣称请求所有权已转移。
- 本机 schema 中 turn/start 和 turn/steer 提供 clientUserMessageId，但在线文档没有明确其持久去重保证；本项目仅用它追踪提交，未实现历史接受状态核对。
- start/resume 响应及 settings/name/tokenUsage 事件提供实际设置和用量。账户限额来自 account/rateLimits/read 和稀疏 updated 事件，缓存独立于会话。没有数据时不推测。
- 限额窗口的 resetsAt 按官方定义为下一次重置的 Unix 秒时间戳；显示使用运行电脑的本地时区，缺失或无效时省略，不根据窗口长度推算。
- skills/extraRoots/set 在连接初始化后注册项目 skills 绝对路径；不往两个 CODEX_HOME 安装链接。技能只说明用户明确请求的附件格式，不扩大自动外发路径权限。
- 本机 app-server proxy 帮助明确其转发原始 socket 字节，不能按 JSONL 处理；共享控制 socket 需 WebSocket HTTP Upgrade。socketpair + unix_connect 复用成熟分帧实现。独立 app-server 仍用 JSONL。
- daemon start 复用已有后台。桥接通过独立 systemd transient user service、ExitType=cgroup、UMask=0077 承载后台，环境复制只传变量名，不把密钥值放 argv；不启用 remote-control 或网络监听。
- thread/settings/update、thread/queue/*、thread/sample、thread/unsubscribe 的字段可在本机 schema 查到，但跨客户端影响、持久性或工具隔离未完全核实，因此未用它们替换既有流程。

图片输入沿用官方 Turns 的 `localImage`（本机 0.166.0 UserInput schema 同样支持）；正常图片不再附加重复路径说明。该 schema 未提供通用本地文件输入类型，普通文件及非视觉模型的图片保留精简 JSON 路径引用，不把文件冒充 skill/mention。

## 微信和附件

认证 token 仅发送到可信 HTTPS API 域；拒绝重定向。CDN 不携带 API 认证。应用声明 CodexWechat/0.1.0，不冒充腾讯插件版本。

按腾讯协议使用 AES-128-ECB/PKCS7 和 MD5，入站兼容原始 key/base64 与十六进制 key/base64；出站先取上传 URL、上传密文取得引用，再发媒体消息。完整协议字段见上游实现，不能仅凭静态类型宣称文件传输通过。

create_time_ms 是源消息时间戳，未证实等于手机点击发送时间。入站延迟使用 wall clock 差值，发送 API 耗时使用 monotonic perf_counter；两者都不是手机端显示回执。

## 服务与兼容

安装方式沿用本机 ime-cursor 项目的脚本/用户服务惯例，绑定 graphical-session.target。systemctl --no-block 只确认重启作业入队，不表示重启成功。DeepSeek launcher 自行选择 CODEX_HOME 并处理密钥环，桥接不读取密钥。

旧 processingNotice 对象和旧延迟模板迁移集中在 configuration.py。保留旧 backend 配置、sharedDaemon=false、已有 JSON 状态及旧发件箱条目；无法由旧 running 标记还原的当前输入不猜测重放。

网络测速依据：CLI 0.166.0 的 ConfigReadParams/GetAccountParams schema 与官方 config/read、account/read；curl --write-out 的 time_appconnect 是起始至 TLS 握手完成，time_connect 是起始至 TCP（可能为代理）连接完成。HEAD 不携带模型认证，不跟随重定向；只取上述阶段，不使用包含服务端处理的 time_starttransfer/time_total。

上下文压缩提示依据：官方 App Server Items 的 contextCompaction 与 item/started、item/completed 生命周期，以及本机 0.166.0 的 ItemStartedNotification/ItemCompletedNotification。旧 thread/compacted 已弃用，本桥接不同时监听旧事件，避免重复通知；item 仅提供 id/type，不能据此区分自动与手动压缩。

用量历史回退：本机当前会话 JSONL 的 event_msg/token_count.info 实际含 total_token_usage、last_token_usage、model_context_window。此为本机记录格式核对，不是稳定公共 API 保证；只从原生 Thread.path 读取当前 thread 文件的有界尾部，失败返回不可用，实时事件优先。

本轮原生能力取舍（2026-11-01）：
- 官方 “Start (or resume) a thread / Turns” 支持不带覆盖的 resume 和 turn/start；因此恢复只读取原生设置，普通输入不再把显示缓存作为 turn 覆盖发回，旧 sessionOptions 不再重放。显式 /model、/effort、/cwd 继续沿用已有 resume 覆盖流程，不引入未经核实的新请求。
- thread/settings/updated 的 threadSettings 字段映射依据本机 0.166.0 schema，仅用于观察当前选中会话，不触发写入；其实际到达与持久化行为待在线验证。
- 原生 queue 不迁移：当前本地队列还承载附件准备、停止和交付恢复，官方页面没有建立等价的取消/持久契约。保留稳定客户端 ID，但不声称服务端持久去重，也不自动重发 uncertain 输入。
- 不增加 loaded/unsubscribe/sample 或全量历史补取。认证、daemon、会话列表/恢复、模型目录、compact、限额和原生事件继续复用 Codex。

关机恢复修复依据：CLI 0.166.0 `app-server proxy --help` 明示只转发 stdio 到运行中的控制 socket；启动器仅此路径跳过密钥预检。systemd 262 的 systemd-run/systemctl 文档明确 Type=exec 的返回不代表后台程序完成，以及 failed unit 状态保留与 reset-failed。通过 ActiveState/Result/ExecMainStatus 检查启动，退出码 78 是本项目 DeepSeek 启动器新定义的密钥环预检失败契约，不是 Codex 原生错误码。实际密钥环未解锁仍需桌面解锁，不能由网关绕过。

自启依据：systemctl enable/disable 不等同于 start/stop；disable 会删除指向 unit 的链接，link 可保留仅按需启动。Windows [Set-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/set-scheduledtask) 明确修改定义不影响当前运行实例；[Trigger.Enabled](https://learn.microsoft.com/en-us/windows/win32/taskschd/trigger-enabled) 控制触发器启用状态。本项目只切换登录触发器，自启不作为进程失败重试开关。

安装依赖边界依据官方 [App Server](https://learn.chatgpt.com/docs/app-server) 的 CLI app-server 入口，以及 [Windows CLI 部署](https://learn.chatgpt.com/docs/enterprise/windows-deployment)。本分支支持范围仅为普通 CLI 和 codex-deepseek；安装软检查不等于运行依赖已满足。

在线更新使用 [Git clone](https://git-scm.com/docs/git-clone) 的 `--depth 1`、`--single-branch`、`--branch`：ref 可为分支或标签。源码进入临时目录，项目私有数据和 `.git` 不进入版本载荷；继续由本地稳定安装器执行源码校验、确认、构建和切换，不直接运行下载仓库的安装脚本。

一行入口的 Windows 下载使用 [Invoke-WebRequest 5.1](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/invoke-webrequest?view=powershell-5.1) 的 `-UseBasicParsing`、`-OutFile` 和超时参数；下载到本次临时文件后调用 Python 安装器。此公开入口本身就是用户明确执行的远程安装代码，不提供来源签名或免信任保证；实际版本化安装逻辑仍只有一份。
