# 架构与所有权

安装、更新和卸载的用户流程见 [INSTALL](INSTALL.md)。本文保留模块职责、连接所有权和认证
边界；输入输出与恢复见 [DELIVERY](DELIVERY.md)，热配置与资源见
[CONFIGURATION](CONFIGURATION.md)，日志与隐私见 [DIAGNOSTICS](DIAGNOSTICS.md)。

## 职责

- codex_connection：唯一连接意图和校验；native_codex：CLI/App 可执行入口发现。
- backend_runtime：后台准备与所有权；codex_transport：字节连接、JSON-RPC 和关闭；backend：原生会话/轮次/事件。
- app_ipc：App 的有界帧、持有者发现、会话快照/增量和 follower 请求适配；app_ipc_windows 只负责本机管道连接与身份核对。
- desktop：内部 CLI 接入和共享后台准备；不启动 App、不改写桌面入口，也不作为公开命令。
- wechat / media：微信和 CDN 协议；wechat_auth / rebind：认证状态、身份与重绑编排。
- ingress：微信入站过滤、去重、引用归一化和命令路由；不复制 Bridge 状态。
- backend_session：连接建立和后台恢复循环；不承载微信或交付策略。
- input_delivery / output_delivery：输入提交和有序输出；Bridge 是组合根和持久化入口。
- lifecycle：按操作维护会话变更、重启、输入准备和 steer 的准入条件；调用方仍持有锁及提交屏障。
- commands / command_specs / prompts / configuration：唯一命令目录、展示快照和配置；不重复业务状态。
- diagnostics 写入安全事件，log_viewer 读取和跟随日志；命令行入口只负责调用。
- platform_ui / platform_support / service_adapter：二维码、通知、平台路径与原生服务操作。
- subprocess_io / storage：有界子进程收尾、私有 JSON 与原生单实例锁。
- installer：安装策略和依赖声明；系统脚本仅适配入口与原生服务。安装器必须是单文件可运行，
  运行期代码集中在 `codex_wechat/`，根目录只保留安装器、supervisor 薄入口和平台入口脚本；
  stable payload 与包内导入由测试校验。release 目录清理并入安装器，active-release 标记
  保留正在运行的版本。Windows Mutex/Job Object 使用 pywin32 封装原生 API。
  每个版本建独立虚拟环境；pip 默认复用本机缓存，设置 `PIP_NO_CACHE_DIR=1` 可禁用缓存；
  重复更新因此不再重下同版本 wheel，缓存未命中时才联网。

共享后台与微信服务是两种所有权：前者可以被 App/CLI 继续使用，后者只管理桥接。
连接失败不删除写入锁、不抢停桌面、不静默另开独立写入后台。App 的共享 WS 仅回环，
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

## 验证边界

运行期状态的验证命令、隔离方式和证据归档见 [DEVELOPMENT](DEVELOPMENT.md) 与
[VALIDATION](VALIDATION.md)。文档、源码、离线结果与实机证据分别报告；无法验证的组合
不写成通过。外部协议和版本依据见 [SOURCES](SOURCES.md)。
