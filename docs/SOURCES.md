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

## Codex

- App Server 提供 thread/start、resume、read、list、turn/start、steer、interrupt、最终消息和审批事件。steer 的 expectedTurnId 必须匹配活动轮次，不接受模型/目录等 turn-level 覆盖。
- 本机 schema 中 turn/start 和 turn/steer 提供 clientUserMessageId，但在线文档没有明确其持久去重保证；本项目仅用它追踪提交，未实现历史接受状态核对。
- start/resume 响应及 settings/name/tokenUsage 事件提供实际设置和用量。账户限额来自 account/rateLimits/read 和稀疏 updated 事件，缓存独立于会话。没有数据时不推测。
- skills/extraRoots/set 在连接初始化后注册项目 skills 绝对路径；不往两个 CODEX_HOME 安装链接。技能只说明用户明确请求的附件格式，不扩大自动外发路径权限。
- 本机 app-server proxy 帮助明确其转发原始 socket 字节，不能按 JSONL 处理；共享控制 socket 需 WebSocket HTTP Upgrade。socketpair + unix_connect 复用成熟分帧实现。独立 app-server 仍用 JSONL。
- daemon start 复用已有后台。桥接通过独立 systemd transient user service、ExitType=cgroup、UMask=0077 承载后台，环境复制只传变量名，不把密钥值放 argv；不启用 remote-control 或网络监听。
- thread/settings/update、thread/queue/*、thread/sample、thread/unsubscribe 的字段可在本机 schema 查到，但跨客户端影响、持久性或工具隔离未完全核实，因此未用它们替换既有流程。

图片输入沿用官方 Turns 的 `localImage`（本机 0.166.0 UserInput schema 同样支持）；正常图片不再附加重复路径说明。该 schema 未提供通用本地文件输入类型，普通文件及非视觉模型的图片保留精简 JSON 路径引用，不把文件冒充 skill/mention。

## 微信和附件

认证 token 仅发送到可信 HTTPS API 域；拒绝重定向。CDN 不携带 API 认证。应用声明 AgentWechat/0.1.0，不冒充腾讯插件版本。

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
