# 文档依据

查阅日期：2026-10-31。先查文档后实现，未用试错推定协议。

- [OpenAI Codex App Server](https://learn.chatgpt.com/docs/app-server)：stdio JSON-RPC、initialize/initialized、thread/start、thread/resume、turn/start、turn/interrupt、item/completed、turn/completed 与审批请求。当前在线文档未按本机 CLI 0.166.0 固定；本次已生成并查阅本机该版本的协议 JSON Schema，核对 thread/list、thread/read、model/list、thread/resume、turn/start 的参数与响应。这是接口文档核对，不是运行测试。模型及权限参数默认不覆盖。
- [腾讯微信插件 README](https://github.com/Tencent/openclaw-weixin/blob/main/README.zh_CN.md)：扫码登录及长轮询接收机制。
- [腾讯微信后端协议](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)：二维码 bot_type=3、请求头、getupdates、sendmessage、context_token、消息类型、-14 错误。
- [腾讯 api.ts](https://github.com/Tencent/openclaw-weixin/blob/main/src/api/api.ts)、[types.ts](https://github.com/Tencent/openclaw-weixin/blob/main/src/api/types.ts)、[login-qr.ts](https://github.com/Tencent/openclaw-weixin/blob/main/src/auth/login-qr.ts)：与协议文档对照阅读。查阅时 package.json 的版本为 2.4.10。没有直接导入 OpenClaw 插件，也没有声称插件的服务端接口构成稳定、完整契约。

本桥接自行声明 AgentWechat/0.1.0，不冒充腾讯插件版本。独立客户端的接入资格及微信客户端适用条件尚需扫码联调确认；若服务端拒绝该客户端，必须报告差异，不改成伪装官方客户端来绕过。

API 地址重定向仅允许 HTTPS 的 ilinkai.weixin.qq.com 及其子域；若真实返回其他微信域名，应先核对官方代码和域名用途再调整。禁用 HTTP 自动跳转，避免 token 跟随到任意地址。

新增会话管理依据：thread/list 明确指定 cli/vscode/appServer 来源，排除子代理；model/list 提供支持的 reasoningEffort；thread/start 和 thread/resume 响应返回实际 model、reasoningEffort、cwd。thread/resume 通过 config.model_reasoning_effort 应用强度覆盖，turn/start 显式传递选中会话的 model/effort/cwd。恢复旧会话时不再强制覆盖为桥接初始 cwd。

服务安装依据：本机 `~/.local/share/ime-cursor/{README.md,install.sh,ime-cursor.service}`，systemd 262 的 systemd.unit/systemd.service 手册及 [systemd.special](https://www.freedesktop.org/software/systemd/man/latest/systemd.special.html) 的 graphical-session.target。使用用户服务随图形登录启停，`Restart=on-failure`，不启用 linger。

DeepSeek 接入依据：本机 `codex-deepseek/README.md` 与 launcher.py 的 app-server 转发逻辑。启动器自行设置独立 CODEX_HOME 并从桌面密钥环检查密钥；桥接仅按命令启动，不读取密钥。

微信重启指令依据：[systemctl 262](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html) 的 show/MainPID 与 --no-block：后者仅验证并排入作业，不等待作业完成。使用异步 restart 避免服务等待自身退出。仅做静态检查，未执行真实重启。

媒体实现依据：腾讯协议的 getUploadUrl/CDN 媒体流程，以及 src/cdn/upload.ts、src/cdn/cdn-url.ts、src/messaging/send.ts。入站兼容原始 16 字节 key 的 base64 和十六进制字符串的 base64，图片优先 image_item.aeskey；出站按上游把十六进制 key 字符串作 base64。CDN 上传 POST 密文，读取 x-encrypted-param，再单独发送媒体消息。

加解密依据 cryptography 50.x 官方 [Cipher/AES128/ECB](https://cryptography.io/en/50.0.0/hazmat/primitives/symmetric-encryption/) 及 [PKCS7](https://cryptography.io/en/50.0.0/hazmat/primitives/padding/)，本机包版本 50.0.2；附件输入依据已查阅的 Codex App Server 官方文档及本机 TurnStartParams schema 的 localImage/path。静态检查不代表 CDN、模型图片输入或附件收发已联调通过。

CDN 连接诊断：现场观察到网关到 CDN 的 IPv6 TCP 连接处于 SYN-SENT，DNS 返回多个 IPv6 地址排在 IPv4 前。Python 3.14 socket.create_connection 顺序尝试解析地址，原 60 秒超时可在多个地址上累计。修复改用本机 curl 8.23 的默认 Happy Eyeballs；[curl 手册](https://curl.se/docs/manpage.html) 核对 --connect-timeout、--max-time、--config、--max-filesize（8.4+ 可限制传输中大小）。未用真实附件做操作测试。

默认追加消息依据：已查阅的 OpenAI App Server 文档 Steer an active turn 段落和本机 TurnSteerParams schema。turn/steer 必须携带 expectedTurnId，接受与 turn/start 相同的 UserInput 数组，不允许模型/目录等 turn-level overrides。当前轮次已结束时拒绝追加；不把超时当成确定未投递。仅静态检查，未对运行中的会话做 steering 操作测试。

桌面共享接入依据：本机 Codex CLI 0.166.0 的 `app-server proxy --help`（通过 stdio 原样转发已有控制 socket 的字节）和 `app-server daemon start --help`（后台未运行时启动，已运行时复用；只查帮助，未执行）；官方 App Server 的 thread/resume、thread/status/changed、turn/started、turn/completed、thread/turns/list(itemsView=notLoaded)，以及本机对应 schema。网关自动连接或启动共享 daemon，不停止或重启它；接入使用现有用户/home 的原认证和权限，不复制凭据，不强行解锁或杀桌面进程。尚未执行真实会话接管测试。

2026-11-01 用户 /restart 失败后修正：日志显示 native daemon 返回 alreadyRunning，但网关 35 秒后连接失败。重新核对 [App Server Protocol](https://learn.chatgpt.com/docs/app-server#protocol)：Unix 控制 socket 使用 HTTP Upgrade 后的 WebSocket，每条 JSON-RPC 为一条文本消息；proxy 转发原始字节，不能直接使用 JSONL。改为 socketpair 接启动器的 stdin/stdout，通过 [websockets asyncio client](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) 的 unix_connect(sock=...)、send/recv/close 实现协议，禁用外部代理和压缩，限制 16 MiB。依赖安装到项目 .venv，不改系统 Python。安装得到 17.4；该版本在线文档页未能访问，已读稳定文档并对照本机 17.4 的 unix_connect 实现与 Python 3.14 create_unix_connection 文档确认 socket 接口。未执行连接、会话或消息操作测试。

后台自动启动依据（2026-11-01）：[systemd-run 262](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html) 的独立 transient service、--setenv=NAME（从调用环境复制值）、--collect、--expand-environment=no；[systemd.service 262](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) 的 Type=exec 与 ExitType=cgroup（派生进程仍存活时保持服务）。因此不用网关自身的子进程 cgroup 承载公共后台，不设置 PartOf=agent-wechat.service，不调用 daemon restart。启动就绪以实际 proxy initialize 成功为准，不以 systemd-run 退出成功为准。仅核对文档及源码，未启动或重启任何服务。

独立静态审阅指出的相关修正：在 input_items 预检前建立可停止的本地轮次状态；明确拒绝 steer 时改为 queue，超时不重发；所有出站消息限定重试并保留有限失败记录；自动附件仅限工作目录/媒体目录，任意其他路径须显式 /send；控制命令错误显式回报，后端切换后的旧连接清理错误不再假称切换失败。新增离线用例仅编写，未执行。

测速依据：Python 3.14 官方 time.perf_counter 为单调的本机耗时计量，time.time 仅用于标注跨时钟估算；微信 create_time_ms/sendmessage 字段依照已查阅的腾讯协议。Agent 首条文字按已查阅的 App Server item/agentMessage/delta 或完整 agentMessage 事件记录，完成按 turn/completed；不计桌面中途接入轮次，不把这些测量称为手机 RTT 或服务商 TTFT。只增加实际发送的本机计时，没有执行网络或模型探针。

/status 扩展依据（2026-11-01）：本机 Codex CLI 0.166.0 导出的 `ThreadStartResponse`、`ThreadResumeResponse`、`ThreadReadResponse`、`ThreadSettingsUpdatedNotification`、`ThreadNameUpdatedNotification` 与 `ThreadTokenUsageUpdatedNotification`。thread start/resume 提供 modelProvider、approvalPolicy、approvalsReviewer 和 sandbox；设置更新事件补充 collaborationMode 与 activePermissionProfile；Token 事件提供累计、最近轮次和上下文窗口。Thread 本身没有可直接读取的历史 Token 字段，因此刚接入且本次连接尚未收到 Token 事件时，/status 不推测用量，显示“暂不可用”。未显示 sessionId 等内部标识。

账户限额依据（2026-11-01）：本机 Codex CLI 0.166.0 导出的 `GetAccountRateLimitsResponse`、`AccountRateLimitsUpdatedNotification` 与 `ClientRequest`。`account/rateLimits/read` 提供 primary/secondary 窗口的 usedPercent、windowDurationMins、credits 等通用字段；`account/rateLimits/updated` 是稀疏更新，客户端应合并而非用缺失/null 字段清空旧值。/status 先做最多 3 秒的只读请求，30 秒内复用结果，并接收后续通知合并。请求失败、后端不支持或没有限额字段时显示“暂不可用”，不按 Codex/DeepSeek 名称分支，也不根据剩余百分比推断恢复。
