# 架构与恢复边界

安装、更新和卸载的用户流程见 [INSTALL](INSTALL.md)；本文只保留运行期和恢复契约。

## 职责

- codex_connection：唯一连接意图和校验；native_codex：CLI/App 可执行入口发现。
- backend_runtime：后台准备与所有权；codex_transport：字节连接、JSON-RPC和关闭；backend：原生会话/轮次/事件。
- app_ipc：App 的有界帧、持有者发现、会话快照/增量和 follower 请求适配；app_ipc_windows 只负责本机管道连接与身份核对。
- desktop：内部 CLI 接入和共享后台准备；不启动 App、不改写桌面入口，也不作为公开命令。
- wechat / media：微信和CDN协议；wechat_auth / rebind：认证状态、身份与重绑编排。
- ingress：微信入站过滤、去重、引用归一化和命令路由；不复制 Bridge 状态。
- backend_session：连接建立和后台恢复循环；不承载微信或交付策略。
- input_delivery / output_delivery：输入提交和有序输出；Bridge是组合根和持久化入口。
- lifecycle：按操作维护会话变更、重启、输入准备和 steer 的准入条件；调用方仍持有锁及提交屏障。
- commands / command_specs / prompts / configuration：唯一命令目录、展示快照和配置；不重复业务状态。
- diagnostics 写入安全事件，log_viewer 读取和跟随日志；命令行入口只负责调用。
- platform_ui / platform_support / service_adapter：二维码、通知、平台路径与原生服务操作。
- subprocess_io / storage：有界子进程收尾、私有JSON与原生单实例锁。
- installer：安装策略和依赖声明；系统脚本仅适配入口与原生服务。安装器必须是单文件可运行，
  stable payload 清单由测试校验顶层本地导入闭包。release.py 只负责 release 目录清理，
  active-release 标记保留正在运行的版本。Windows Mutex/Job Object 使用 pywin32 封装原生 API。
  每个版本建独立虚拟环境，但依赖安装复用本机 pip 缓存（`PIP_NO_CACHE_DIR=1` 可恢复旧行为）；
  重复更新因此不再重下同版本 wheel，缓存未命中时才联网。

共享后台与微信服务是两种所有权：前者可以被 App/CLI 继续使用，后者只管理桥接。
连接失败不删除写入锁、不抢停桌面、不静默另开独立写入后台。App 的共享WS仅回环，
原生daemon优先；平台缺口不通过伪造跨平台实现隐藏。`active-release` 只由取得 bridge
状态锁并完成启动的 `start` 进程写入；只读 `status`/`logs` 不改变运行版本标记。
backend 的事件入口保留来源过滤、早到事件缓冲和轮次检查；用量、设置、消息及轮次收口
分开处理，仍共用同一 Backend 状态，不增加事件总线或第二份会话状态。
CLI 和微信继续复用原生 daemon 或显式 WebSocket；App 使用系统正常入口。
已独立启动的 App 在 resume 明确报告 active writer 时走 App IPC：Linux 使用同用户私有 socket，
Windows 使用本机命名管道，依据已连接句柄查服务 PID，核对同用户和当前已注册 App 可执行文件。
Windows 管道不是按 CODEX_HOME 隔离，只允许主 home；独立后端仍走自己的原生连接。
按后端 CODEX_HOME 发现持有者并固定 owner，收到初始快照后才提交选择。非写入冲突不回退，
不删除锁、不接管 App 进程。只有选定 owner/thread/host/version 的事件可更新会话；
快照作为历史基线，增量按 revision 应用，缺口重取快照；旧正文不回发。
App 请求结果超时/断连仍为投递不确定，不切回原生后台重发；审批/问题答案始终返回原 App owner，
已失效请求不落入另一个后台。关闭桥接只关闭 follower 连接。
IPC steer 在发出前核对快照中的 expectedTurnId；App 内部 follower 接口没有同名原子前置条件，
因此不能承诺与原生 turn/steer 相同的跨客户端竞态保证。项目不再维护专用 App 启动入口。
默认使用正常 App 入口时，微信发起的回合仍跑在同一个 thread 上：App 停在该会话时，
微信里的输入与回复会出现在 App 里，本机已观测到这种用法。"反向接入"只指另一件事——
让 App 跳到它没打开的会话。follower 接口只有 start-turn、steer、interrupt 和 stream 状态，
没有打开或聚焦会话的方法，所以桥不承诺这一条。
切换会话时会请求旧会话的 `thread/unsubscribe`，交还 Bridge 持有的写入者；这是 app-server
暴露的唯一释放手段（没有 `thread/unload`），失败只记日志、不阻断切换。
后台与代理职责仍各自独立，不把微信后台是否就绪作为普通 App 启动的前置条件。

错误处置使用多轴标记：`retryable`、`user_action`、`fallback`、`terminal` 可以并存，
调用方按 fallback → retryable → user_action → terminal 选择即时策略；`retry_after`
只作为重试提示，不与用户操作互斥。Writer conflict、共享后台回落和本地回合中止分别保留
各自语义，不压成互斥错误类别。

## 输入与输出

输入以一次保存从inbox进入currentInput。RPC前记dispatching，接受后记accepted；
preparing可恢复，dispatching结果未知、accepted均不自动重发。clientUserMessageId只是
追踪标识，未经验证不承诺服务端持久去重。明确不能steer可排队；超时/断连不算明确拒绝。

会话标题检索使用原生 `thread/list` 的 `searchTerm`；4–32 位会话 ID 前缀通过分页枚举
会话列表后本地匹配。两者都不解析 Agent 正文，也不调用模型。`/switch` 对唯一匹配直接
采用；多个结果只保存当前候选并要求编号选择，避免按模糊线索静默切错会话。会话切换仍受
busy 保护，不在运行中改写当前连接。

每份结果固定携带来源、会话、轮次与目录；选择代次、连接代次不持久化。异步附件快照完成
后再核对来源。已入发件箱的内容不因切换重新解释。完整中途消息按原生item ID去重，
最终回答不再合并重复进度；当前不回放断线期间全部结果。

正文、附件和结束提示成组入队，切片与稳定消息ID固定。正文重试耗尽停止本组后续正文；
单附件失败可继续其它附件，但抑制结束提示。失败通知不递归，失败历史有界。
每条耗尽只增加一次连续失败数，成功发送与出队/清零同次保存。微信接受后本机保存前
崩溃仍可能重复送达，不承诺恰好一次。

## 并发与认证

收信、发信、模型工作、后台恢复和命令调度独立；慢后端不阻塞本地帮助/状态/停止。
重启在首次await前关提交屏障，未提交输入可退回待办；已提交任务仍受busy保护。
未发出的回复在保存的发件箱里，重启后由新进程继续发送，因此重启只给回执一个很短的先机
（`RESTART_DRAIN_GRACE_SECONDS`）就继续，不等待发件箱排空——通道被暂停或静默丢弃时
排空永远不会发生，等待只会把重启卡住。
原生问题/审批使用各自响应格式，只回应本连接实际收到的请求，不自动选择答案。
App async 问题只在结构化 `agentMessage.questions` 上识别；多题依次收齐后按原
连接、会话和轮次用版本相关包装文本补充到原轮次，不解析Agent正文冒充题目。
等待期间切换连接、会话、轮次或桌面已作答会使手机端提示失效；已发出的答案不重放。

确定认证失效保存reauth_required并暂停收发和新提交，一次系统提醒；不将临时断网误判
为失效，也不擅自中断已运行Agent。同一微信用户重绑沿用聊天并保留交付；bot变化时重置
游标、上下文及上传引用，保留去重记录，收到新的上下文后恢复待办和发件箱。等待期间的新结果仍保存。
扫码等待不持有状态锁、不停止服务；收到确认后才短暂停服，持锁重读最新状态并核对绑定未被其它进程更改。
停服前和拿锁后均复核活动任务，扫码期间新增的任务也需要显式 --stop-active；拒绝提交时恢复原服务。
换微信用户以手机扫码授权为准，自动隔离旧交付，不在终端重复确认。变更先持久保存未完成标记，再提交凭据，成功后清标记。
半完成状态禁止启动业务，等待显式继续。
用户会话选择和提示偏好不属于微信token，不因换bot清空。

## 配置与资源

热配置从同一JSON按inode/mtime/size变化读取，整组校验后应用；无效文件保留旧值。
校验失败保留 stderr 提示，并写 config.reload_failed 安全事件（仅失败类别与异常类型）；
Bridge 将一次失败提示入发件箱。同一文件签名不重复报告，无微信上下文时暂存提示标记；
有效配置重新加载后清除该标记。失败提示使用最后生效的文案快照，不递归触发配置读取。
实际文件键变化才清对应微信模板覆盖。显示快照不成为原生设置的隐式写入来源。
运行期文案唯一目录为prompts.py的DEFAULT_PROMPTS，config.prompts仅存覆盖；不重载代码模块。
HotSettings在应用前统一验证键、文本类型、字节上限及简单占位符语法；删除键恢复默认。
Bridge.prompt按需重读配置，仅从现有状态快照与局部参数渲染一次；后端回调使用同一入口，
无入站消息也能读取新文案。纯展示函数可注入该入口，独立后端默认使用目录原文。
帮助描述也在提示目录维护，命令元数据仍只在command_specs声明；不把用户文案当成路由或状态。
展示标签与语义字段键分离，不改变审批token、原生内容或交付分组。
模板共享字段和局部参数统一使用英文snake_case，不保留旧中文字段映射或旧模板自动改写。
默认模板、共享快照、字段列表和渲染调用采用同一套名称，显示标签保持原文。
Token优先原生事件，缺失时只读当前Thread.path的有界尾部，按文件身份/时间/大小缓存；
该本地格式不是稳定协议，不扩大依赖。限额属于账户缓存，不依赖子进程存在。

取消必须先完成子进程退出、stdout/stderr回收与stdin关闭，再向调用者传播；第二次取消
也不遗弃清理任务。共享连接关闭仅结束自己的代理。原子JSON临时文件成功/失败都清理。
传输临时目录即时清理；已保存附件与用户原文件不按年龄自动删除。

共享 daemon 启动失败时，本次运行按设计回落独立进程；原因保留在 `/status` 的
`shared_fallback` 字段和 `backend.shared_unavailable` 日志中，持久 `sharedDaemon`
设置不被改写。Linux DeepSeek 密钥环失败也走这一回落路径；若产品要求 fail-fast，应作为
独立设计变更，而不是假定错误已被静默吞掉。

安装器保持准备阶段独立、激活集中，不初始化账户、不默认开启自启、不重启业务、不提供回滚。
安装、更新和卸载的稳定入口、数据保留和 `current` 损坏处理见 [INSTALL](INSTALL.md)。
服务启动的桥接子进程由 systemd unit 或 Windows supervisor 设置
`CODEX_WECHAT_SERVICE_CHILD=1`，因此不会被用户前台 `start` 的互斥检查误伤。
`logs --service` 保持平台原生来源：Windows 为 `service.log`，Linux 为
`journalctl --user -u codex-wechat`。Windows 的 `service.log` 按 5 MiB 轮转，
保留份数与 `bridge.jsonl` 共用 `logs.keep`；Linux 控制台日志的保留由 journald 管理。

## 诊断与隐私

结构化诊断写入数据目录的`logs/bridge.jsonl`，按5 MiB轮转，旋转份数由 `logs.keep` 控制
（1–100，默认 3，可 `/settings logs.keep` 热改；结构化日志和 Windows 控制台日志各自约为
5 MiB ×(keep+1)）。每行是一个
JSON对象，用随机`request_id`关联由微信输入触发的接收、准备、提交、输出入队、发送和
发送API。中途回复携带轮次关联的请求号；同一轮有多条输入时，通过`output.related`和
输出组`job_id`关联，不表示每段输出由某条输入独立产生。连接/恢复等生命周期事件不强行
关联到单条消息。微信请求只记录
endpoint、HTTP/curl状态和耗时。事件字段由固定白名单校验，正文、prompt、回复、文件名、
账号、token、context token、Authorization、请求/响应体和绝对路径不会进入结构化日志。
异常仅保留类型与可选错误码；endpoint只接受已知微信API路径。写入与轮转使用原生进程锁，
锁被占用或写盘失败时丢弃本条诊断，不等待、不阻断消息收发。`codex-wechat logs`
默认展示结构化日志及轮转文件；`--service`才加入服务控制台输出，后者未脱敏，
不是结构化日志。跟随模式按文件身份和读取位置追踪轮转，在备份保留范围内补读旧文件。
默认保留命令处理、输出入队与结果及网络计时，便于判断延迟所在阶段；`output.send_start`、
`wechat.send_ack`、poll、getconfig、sendtyping 成功以及 2 秒内完成的微信请求只记 debug，
故障与慢请求仍在 info/warning。`output.sent` 带 `queue_wait_ms`，不再靠 send_start 才能拿到
排队等待。每条记录都带桥的 `version` 与 `platform`；`backend.version` 在连接建立后记一次
`backend_version`（取自 app-server initialize 响应的 `userAgent`），用于区分 Codex 侧的版本；
带绝对路径形状的 `userAgent` 字段会被丢弃，不进入结构化日志。
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
收信默认等待 35s 并留 5s 传输余量；成功响应的 longpolling_timeout_ms 可调整下一轮等待，
限制在 1–120s，余量另加。仅 getupdates 的 curl 28 且 HTTPS 已准备、无首字节或正文时，
作为 WeChatPollTimeout 立即开始下一轮，保留游标、不确认认证有效、不进入失败退避。
该事件以 debug 的 status=poll_idle_timeout 记录；不证明网络健康。连接、TLS、响应体阶段
超时及其它接口的超时仍为故障，外部取消继续传播。
业务错误额外记录固定 api_error_kind（auth_expired、prepare_failed、unknown_error、
rate_limit_hint、other），仅按明确提示归类，不保存 errmsg，也不将 -2 自动认定为限流。
发送是否成功以服务端返回的有效消息 id 为准：HTTP 200 且 ret=0、但响应不含非空的
msgid/msg_id/message_id/messageId/msgId 时判定为未投递（WeChatNotAcknowledged），按可重试处理，
重试耗尽后进入 failedOutbox 并计入连续发送失败，不再记 output.sent。这是**经验判据而非契约**：
协议文档的成功响应示例就是 `{"ret":0,"errmsg":""}`，字段表没有消息 id，官方维护者也说明
"ret=0 只代表 API 层接受、不保证投递"，契约本身无法区分投递与静默过滤（出处见
[SOURCES](SOURCES.md) 的"微信下行投递"一节）。该错误属于响应内容
而非请求本身，日志里体现为 output.send_failed / output.failed 的
error_type=WeChatNotAcknowledged 与 error_code=服务端 ret，不计入 api_error_kind。
实测依据：某 bot 出站文本被服务端静默丢弃期间，sendtyping 和 getconfig 全程正常，
sendmessage 一律返回 {"ret":0} 且无 id；若把这种响应当成功，条目会离开发件箱而用户
收不到任何内容，只有 wechat.send_ack 的 status=no_id 留有痕迹。排查投递失败与本地过滤
时，先看该事件的 acked/no_id 计数。
output.failed 的 send_failures 保存当时连续发送失败条目数，成功清零规则不变。

**当前怀疑，不是已证实的契约**：`WeChatNotAcknowledged` 可能是账号开始进入短时限流或
静默丢弃阶段的首个可观测标志；它也可能来自单条消息过滤、接口异常或响应格式变化。单次
出现不足以判定封禁，必须结合同一账号随后多条正文/完成提示是否同时无 ID、认证是否仍有效、
入站是否仍正常，以及换账号或等待后是否恢复。这个怀疑只用于排查顺序，不能替代平台侧证据。

### 出站节流与静默丢弃后的处置

2026-11 之前的行为是每条失败固定 1s 连试 3 次，耗尽后无条件发一条失败通知。
实测下行被整体静默丢弃时，这一条回复会产生 6 次下行请求（原条目 3 次 + 通知 3 次），
而通知本身也必然被丢弃。现在的策略：

- 段间间隔：连续入队条目之间至少 `SEND_SPACING_SECONDS`（2.5s），切片后的长回答不再一次性
  突发；单个条目发送时不受影响。
- 失败退避：第 n 次失败后等待 `SEND_RETRY_SECONDS × 3^(n-1)`（1s、3s），不是固定 1s 连试。
- 未确认投递的特殊处置：`not_acknowledged` 重试耗尽后**不发失败通知**，并暂停出站
  `DELIVERY_PAUSE_SECONDS`（60s），以 `output.send_paused` 记录；暂停时间只存内存。
- 可观测性：连续发送失败达到 `DELIVERY_DEGRADED_THRESHOLD`（3）时，`status` 的 `delivery`
  从 `ok` 变为 `unconfirmed`，成功一次即恢复。

仍然待定的是**唤醒条件**：暂停目前是固定 60s，不看新入站消息；是否改成"等新入站再醒"
还没有决定。发送间隔、失败退避和暂停三项互不排斥；入站回复额度已在下节实现。

### 每条入站消息的回复额度

社区实测（[SOURCES](SOURCES.md) 有出处）每条入站消息约允许 10 条主动回复，超出返回 `ret:-2`，
官方未文档化。现在有显式预算：`sending.replyBudget` 默认 9、范围 **1–100**，按**服务端回话的次数**
计数（正文分段、处理回执、结束提示、附件；静默丢弃与明确拒绝也算，因为服务端处理了那次请求；
超时或连接失败这类根本没到达的请求不算），`/` 续传提示不计入，
作为第 10 条发出。默认值取在经验上限之内，上限 100 是留给知情者的逃生口：把额度调高会越过
平台的经验线，风险由改配置的人承担。

- 判据是"服务端有没有处理这次请求"而不是"用户有没有收到"：只按成功计数会在通道开始丢弃消息
  时让刹车变得最松（`used` 不涨、请求照发），而那正是最需要收手的时刻。代价是故障期交付条数
  可能少于额度值——一次抖动占掉一次额度，用户看到的段数会低于 `replyBudget`。
- 这个键**不进 `/settings` 面板，也不进 `/help settings`**（避免普通用户误调），但仍然可以改：
  编辑 config.json，或 `/settings budgets <整数>`（`sending.replyBudget` 是等价的长名）。
- 额度高于经验上限（9）时，启动或热加载会记一次
  `config.reply_budget_above_ceiling`（`budget`、`ceiling` 两个数值字段），值不变就不重复记；
  这样用户发来的日志能直接看出"被封可能是自己调高了额度"。
- 额度用尽时不再发正文，改为插入一条 `budgetNotice`（"还有 N 段待发，回复 `/` 继续"）后等入站；
  该提示豁免预算检查，每个入站周期最多一条。
- 这条提示本身也是一次发送。若它拿不到回执（通道正在丢弃消息），按"不再叠加"处理：不派生失败
  通知、也不重新排队，发件箱原样保留，等下一条入站刷新额度后按原顺序继续——所以提示丢失时
  用户看不到任何消息，需要靠再发一条消息来解开。
- 任何被接受的入站消息（含 `/` 与任意命令）都会刷新额度：`refresh_reply_budget()` 递增
  `inboundSeq`、把已用条数清零并唤醒发送循环，队列按原顺序继续。
- 已用条数与 `inboundSeq` 一起持久化，重启后不会被误当成"额度已刷新"；序号对不上时按新周期算。
- `/` 是 `/continue` 的别名，只出现在 `/help all`；它自己的处理函数不产生任何回复，
  避免"用 `/` 换一条 `/` 回执"的死循环。
- 这是**本地预算**，不是服务端承诺；它按经验值把每轮压在平台愿意接受的范围内。
当前用户私聊在路由前记录input.observed：消息类型、状态、内容类别、字数、项数及跳过原因，
用于区分上游未投递与本地过滤/去重；不记录正文或消息标识。

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

## 验证

指定测试会话从固定源码副本执行：

```bash
python -m unittest discover -s tests -v
```

全部状态、配置、进程与RPC均使用临时目录或模拟，不接入生产账户、服务、模型和微信。
除测试通过外，独立核对CAPABILITIES中的两侧能力。文档、源码、离线结果与实机证据分别
报告；无法验证的组合不写成通过。协议来源见SOURCES。
