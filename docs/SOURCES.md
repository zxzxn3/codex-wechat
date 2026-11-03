# 依据与兼容边界

2026-11-02。文档/源码核对不等于功能验收；实际执行见测试报告。

| 范围 | 本轮依据 |
| --- | --- |
| Codex 协议 | [官方 App Server](https://learn.chatgpt.com/docs/app-server)、[CLI 参数](https://learn.chatgpt.com/docs/cli/reference)；普通 CLI 本机版本 0.166.0 |
| 原生后台 | [0.166.0 daemon](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-daemon/src/lib.rs)、[Windows 实现](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-daemon/src/backend/windows.rs)；原生负责锁、脱离启动和后台发现，不由桥接模拟 |
| App 接入 | 本机 Linux 重打包 App 26.1027.51404，内置 CLI 0.166.0-alpha.5.1；app.asar 中 .vite/build/early-bootstrap-824-3c24dda6dba622ec.js 的连接选择：CODEX_APP_SERVER_WS_URL、CODEX_APP_SERVER_FORCE_CLI |
| Windows App | Windows 交接记录 App 26.1027.3384.0 / CLI 0.166.0-alpha.5.1；[对应 App Server 源码](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/app-server/src/lib.rs)，本轮无 Windows 实机 |
| 微信 | [Tencent openclaw-weixin 协议](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)及项目此前使用的2.4.10实现；认证、媒体参数以该协议为准 |
| 网络与媒体 | [curl](https://curl.se/docs/manpage.html)、[websockets](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html)、[cryptography](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/)；采用成熟传输和密码实现 |
| 原生服务与 UI | systemd 用户服务、Windows ScheduledTasks；二维码/通知的具体官方依据见 [PLATFORM-CAPABILITIES](PLATFORM-CAPABILITIES.md) |
| 诊断日志 | Python 标准库 `os.open`/`os.replace`/`contextvars`；仅写本机文件，不上传 |

公开协议提供 stdio、WebSocket 和 Unix socket；CLI --remote 可接指定共享后台。
WebSocket 仍为实验性；App 连接环境变量是版本相关内部接口，不是公开稳定承诺。
本机 Linux App 的原生路径由 `CODEX_APP_SERVER_USE_LOCAL_DAEMON=1` 启用：仅在非 Windows、
无 App 配置覆盖、无强制 CLI/可执行覆盖且 daemon 版本满足 App 内部检查时，连接当前
CODEX_HOME 的 app-server-control socket；本版本最低接受 0.141.0。桥接启动入口先准备同一原生 daemon，并用
App 内置 CLI 的 `app-server daemon version` 确认其可见；不改写 App 包，不停止已有后台。
该入口是当前版本源码依据，离线测试不证明 App 所有运行条件均满足；真实共享仍需联调确认。
Linux App IPC 依据同一 app.asar：early-bootstrap-339 的 IpcClient/IpcRouter 和 owner/follower
请求路由；early-bootstrap-824 的 4 字节小端长度帧、私有 ipc.sock；early-bootstrap-439 的
会话快照/Immer 增量、请求处理及状态转换；early-bootstrap.js 的方法版本表。
本版本 thread-stream-state-changed=11、following-changed=1、start-turn=2、steer-turn=1、
interrupt-turn=4。只有 initialize/owner discovery 已有本机只读查询证据，完整收发、审批和
App 更新兼容性仍需验证；不是公开 App Server 协议，不据字段存在承诺幂等或原子 steer 条件。
App webview app-initial 的 continuation 检查直接使用 input.text_elements.some；IPC 文本需
显式补空数组，不能依赖原生服务端的默认值。首次真实投递已触发并从 error boundary 堆栈确认
该字段缺失会导致 App 渲染失败；修复后的真实投递仍待验证。
用户桌面入口遵循 [freedesktop Exec 规则](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html)，
用同名用户文件覆盖系统入口，参数独立引用且保留 `%U`；没有 shell 拼接执行。
Windows 原生 daemon 有自身脱离和管理能力，但不能据此认定 App 默认连接同一后台；
App 共享路径明确配置，不能在失败时静默换为独立后台。

item/completed 提供完整 agentMessage；commentary/partial_answer 可即时转发，final_answer
在轮次收口，reasoning/工具输出不混入聊天。原生请求仅回答本连接收到的部分。
CLI 0.166.0 的公开schema确认 agentMessage 可带 `delivery=async` 与结构化 `questions`
（`title`、字符串 `options`）；本机 App 26.1027.51404 用
`<send_user_message_question_reply>` 包裹逐题答案并通过当前轮次补充输入提交。
该包装和字段组合是版本相关客户端约定，未作为稳定公开协议承诺；桥接不从Agent正文
推断题目。连接/会话/轮次变化或桌面已提交答案时，内存中的未答提示失效。
clientUserMessageId、原生queue、settings字段存在不构成持久幂等、跨客户端所有权或工具隔离保证；
未证明等价前不替换微信交付层。现有显式设置仍使用已采用的resume覆盖，不做隐式重放。

Token优先原生事件，JSONL token_count仅是有界回退，不是稳定API；限额重置使用返回的
resetsAt Unix秒，不从窗口长度推算。压缩使用 contextCompaction item 生命周期，事件本身
不能区分自动/手动。模型图片使用localImage；普通文件不冒充该类型。

微信任意鉴权请求返回-14进入本项目的持久暂停；上游暂停一小时不代表一小时后token必然恢复。
腾讯 [getUpdates 实现](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/src/api/api.ts#L409-L457)
默认长轮询等待 35s，客户端等待超时返回空结果。本项目采用更窄的阶段判定，连接/TLS/
部分响应超时不忽略；取消退出与正常等待分开，不改变认证状态或游标。
输入状态依据协议 `getconfig` / `sendtyping`；引用字段依据 `ref_msg`、`partial_text`、`svr_id`；
语音转写读取 `voice_item.text`，视频使用上传类型 2 与消息项类型 5。
2026-11-03 核对腾讯插件 [config-cache.ts](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/src/api/config-cache.ts)：
配置缓存以 24h 为范围，成功后随机安排 0–24h 内的下次按需获取；这是客户端刷新策略。
协议和 GetConfigResp 仅返回 typing_ticket，未声明有效期或到期时间，因此不能确认服务端
是否按固定时长过期，也不能将 24h 或本项目的 600s 写成服务端有效期。
binded_redirect保留已有凭据，但重绑流程若此前已失效，会用getconfig鉴权确认，不消费消息游标。
同微信号重绑沿用同一聊天，按用户实测确认为项目行为；[腾讯微信渠道文档](https://cloud.tencent.cn/document/product/1759/132562)
佐证单一有效绑定、新连接使旧连接失效，但未承诺bot ID不变。旧游标与context_token跨绑定有效性
无保证；[官方插件](https://github.com/Tencent/openclaw-weixin/blob/main/src/auth/accounts.ts)换账号时清理它们。
本项目同用户保留交付和去重，bot变化时重取协议状态；换微信用户以扫码授权为准，自动隔离旧交付。
媒体沿用上游AES-128-ECB/PKCS7与MD5；API token不发给CDN，不跟随重定向。

/ping使用当前原生配置决定目标，curl time_connect/time_appconnect用于TCP/TLS建连；
HEAD不携带模型密钥，不测生成速度。微信create_time_ms不是已证明的手机点击时间，发送API成功
也不是手机显示回执；该字段缺失时按update_time_ms、item时间戳回退，但这些回退值只是
估计，不保证等于首次发送时刻；全部缺失仍显示`-`。
WS场景探测使用桥接环境，不能冒充另一个后台进程的实际代理路径。

请求日志的 `proxy_used` 来自 [curl write-out](https://curl.se/docs/manpage.html#--write-out)
（8.7新增）；旧版不识别变量时只输出警告、不改变传输结果，依据
[curl 8.4 tool_writeout.c](https://github.com/curl/curl/blob/curl-8_4_0/src/tool_writeout.c)。
可选标记单独成行，随后仍输出兼容的计时JSON；原始警告不写日志。未增加每请求版本探测。

微信 API 连接池依据 [curl_cffi 0.17 AsyncSession](https://curl-cffi.readthedocs.io/en/v0.17.0/_modules/curl_cffi/requests/session.html)
及本机安装源码：AsyncCurl multi 共享连接缓存，取消移除 handle，close 释放 multi 与空闲 handle；
[libcurl MAXAGE_CONN](https://curl.se/libcurl/c/CURLOPT_MAXAGE_CONN.html) 限制空闲连接复用，
项目另在整池空闲 60s 时主动 close。计时来自 getinfo，代理标记来自 USED_PROXY；
未设置浏览器伪装、关闭 TLS 校验或自动重试。跨平台接口不代表 Windows 已实机验证。

Windows 监督器的 Mutex 与 Job Object 使用 pywin32 的 win32event/win32job：
[CreateMutex](https://mhammond.github.io/pywin32/win32event__CreateMutex_meth.html)、
[Job Object](https://mhammond.github.io/pywin32/win32job.html)。仅 Windows 安装此依赖；
父进程只读快照仍用现有 ctypes，服务策略不由库决定。CDN 上传/下载复用 curl_cffi 0.17
异步请求与连接池，使用独立请求头、按附件上限中止回调，不输出带令牌的 URL。
