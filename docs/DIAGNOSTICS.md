# 诊断、日志与隐私

本文说明结构化日志、微信连接池、请求采样和计时字段。投递状态与发送失败处置见
[DELIVERY](DELIVERY.md)，配置项见 [CONFIGURATION](CONFIGURATION.md)。

## 结构化日志

结构化诊断写入数据目录的`logs/bridge.jsonl`，按5 MiB轮转，旋转份数由 `logs.keep` 控制
（1–100，默认 3，可 `/settings logs.keep` 热改；结构化日志和 Windows 控制台日志各自约为
5 MiB ×(keep+1)）。每行是一个
JSON对象，用随机`request_id`关联由微信输入触发的接收、准备、提交、输出入队、发送和
发送API。中途回复携带轮次关联的请求号；同一轮有多条输入时，通过`output.related`和
输出组`job_id`关联，不表示每段输出由某条输入独立产生。连接/恢复等生命周期事件不强行
关联到单条消息。微信请求只记录
endpoint、HTTP/curl状态和耗时。事件字段由固定白名单校验，正文、prompt、回复、文件名、
账号、token、context token、Authorization、请求/响应体和绝对路径不会进入结构化日志。
异常仅保留类型与可选错误码；路径形状的 `reason` 不保留原文，统一替换为固定安全占位符。
endpoint只接受已知微信API路径。写入与轮转使用原生进程锁，
锁被占用或写盘失败时丢弃本条诊断，不等待、不阻断消息收发。`codex-wechat logs`
默认展示结构化日志及轮转文件；`--service`才加入服务控制台输出，后者未脱敏，
不是结构化日志。跟随模式按文件身份和读取位置追踪轮转，在备份保留范围内补读旧文件。
默认保留命令处理、输出入队与结果及网络计时，便于判断延迟所在阶段；`output.send_start`、
`wechat.send_ack`、poll、getconfig、sendtyping 成功以及 2 秒内完成的微信请求只记 debug，
故障与慢请求仍在 info/warning。`output.sent` 带 `queue_wait_ms`，不再靠 send_start 才能拿到
排队等待。每条记录都带桥的 `version` 与 `platform`；`backend.version` 在连接建立后记一次
`backend_version`（取自 app-server initialize 响应的 `userAgent`），用于区分 Codex 侧的版本；
带绝对路径形状的 `userAgent` 字段会被丢弃，不进入结构化日志。

## Typing 与连接池

typing 的 getconfig/sendtyping 与输入处理并行，不等待网络完成再准备或提交消息。
并发输入共用引用计数，最后一项结束时取消启动/保活任务，再尽力发送停止状态。
typing ticket 缓存最多 600s，以单调时钟计时；到期或身份变化时，在下一次启动、保活或停止
状态请求前重新获取，失败不沿用过期 ticket。这是本地刷新策略，不代表服务端保证的有效期。
typing 的认证失效进入同一暂停与重绑通知流程，但发现失效前输入可能已经提交；
不撤回或重发已提交输入。普通 typing 错误不阻断输入处理。
微信 API 通过 curl_cffi 0.17 的异步 libcurl 连接池发送。长轮询和其它请求各有一个池，
每池最多 8 个并发请求；空闲时限由 connectionIdleSeconds 控制（默认 60s，0–3600 的整数），
整池空闲后关闭，单条连接超过时限不再复用。失败连接退役时按单个 session 的活动请求计数：
该 session 自己的请求全部结束后立即关闭，不等待整个池空闲；连续失败不会让退役列表随
长期存活请求无限增长。该值支持整组校验和热更新：新请求使用新值，
已发出的请求不中断；空闲池按新值减去已空闲时长重新计时，删除配置键恢复默认。
0 不创建空闲回收任务；libcurl 的 MAXAGE_CONN 无无限值，内部以跨平台有符号 long 上限
替代，不把配置 0 直接传入。生命周期上限、缓存容量、故障和对端关闭等机制仍然有效。
保留 HTTPS 校验、代理环境变量、禁止重定向、总超时和 16 MiB 响应上限，不增加应用层重试。
取消直接移除 libcurl 请求；服务退出、登录结束和一次性通知结束均关闭连接池。
媒体传输复用 outgoing 连接池，独立限制响应大小且不携带 API 凭据；/ping 仍用 curl 子进程。微信请求统一记录一次结果，包含连接失败、超时及解析失败；
取消和成功长轮询仅记 debug。

## 轮询与错误采样

收信默认等待 35s 并留 5s 传输余量；成功响应的 longpolling_timeout_ms 可调整下一轮等待，
限制在 1–120s，余量另加。仅 getupdates 的 curl 28 且 HTTPS 已准备、无首字节或正文时，
作为 WeChatPollTimeout 立即开始下一轮，保留游标、不确认认证有效、不进入失败退避。
该事件以 debug 的 status=poll_idle_timeout 记录；不证明网络健康。连接、TLS、响应体阶段
超时及其它接口的超时仍为故障，外部取消继续传播。
业务错误额外记录固定 api_error_kind（auth_expired、prepare_failed、unknown_error、
rate_limit_hint、other），仅按明确提示归类，不保存 errmsg，也不将 -2 自动认定为限流。
当前用户私聊在路由前记录input.observed：消息类型、状态、内容类别、字数、项数及跳过原因，
用于区分上游未投递与本地过滤/去重；不记录正文或消息标识。

## 网络计时

微信 API 从 libcurl getinfo 读取指定数值计时、代理标记和连接创建数；不采集 URL、IP 或异常原文。
失败响应可保留已到达的计时，取消不补造阶段数据。/ping 的 curl write-out 解析继续共用计时校验。

| 日志字段 | 含义 |
| --- | --- |
| dns_ms | curl观测到的名称解析累计耗时 |
| connect_ms / appconnect_ms / pretransfer_ms / first_byte_ms | 从curl开始到TCP连接、TLS/隧道握手、协议准备完成、首响应字节的累计时间点 |
| curl_total_ms | curl操作总耗时；duration_ms为包含连接池等待与收尾的墙钟耗时 |
| tcp_ms / tls_ms | connect−dns、appconnect−connect；TCP可能连到代理，TLS段可能包含代理隧道协商 |
| response_wait_ms | first_byte−pretransfer，包含发送请求、服务端等待及网络返回；长轮询等待也在内，不是纯网络RTT或模型生成时间 |
| download_ms | total−first_byte，收到首字节后到传输完成的耗时 |

单位均为ms；差值只在相关时间点已到达且顺序有效时生成。curl返回0的时间点可能表示尚未到达，
不能据此断言某阶段没有耗时。connection_reused=true 表示协议准备已到达且本次未创建连接；
此时 TCP/TLS 时间点为 0 不代表失败，仍可计算请求等待和下载耗时。

命令名 `command` 经唯一命令目录校验并规范化别名，未知名字和参数不记录。
`control.started.queue_wait_ms` 是收到命令至进入处理器的单调时钟间隔（含路由/保存/排队）；
`control.completed/rejected/failed.duration_ms` 是处理器执行耗时，包含后台等待和生成回复。
`output.send_start.queue_wait_ms` 是本进程中新回复入队至第一次发送的等待，包含附件准备；
重试不重复记此样本，旧发件箱重启后不补造。计时只存内存，发送/失败时清理。

超时仅按已取得的 HTTPS 时间点记录 `timeout_stage`：connect（含DNS/连接）、
tls_or_tunnel、request_or_response、response_body 或 unknown。请求发送和服务端等待未拆开，
这些阶段不等于故障根因。`proxy_used` 仅接受 libcurl 报告的 0/1，不从代理环境变量推断。
不记录代理地址、远端IP、原始stderr或响应内容；也不能由此识别透明代理/VPN。
