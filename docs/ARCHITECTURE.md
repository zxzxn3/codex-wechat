# 架构与运行契约

代码是实现事实的唯一来源。本文只记录跨模块的所有权、不能从单个函数直接看出的运行时
不变量，以及用户可观察的行为边界；命令、别名、默认值和范围由
[生成参考](REFERENCE.generated.md) 提供。安装与路径见 [INSTALL](INSTALL.md)，平台差异见
[PLATFORMS](PLATFORMS.md)，外部协议依据见 [SOURCES](SOURCES.md)。

## 术语

| 术语 | 含义 | 不要混淆 |
| --- | --- | --- |
| bridge / 桥接 | 连接微信消息和本机 Codex App Server 的进程 | 不是 Codex 后端 |
| backend / 后端 | `backends.<name>` 定义的连接目标 | 不是模型或 provider |
| thread / 会话 | Codex 的持久会话，拥有独立历史和目录 | 不是 turn |
| turn / 回合 | 一次从输入到完成或中断的执行 | 不是一条微信消息 |
| writer / 写入者 | 当前持有 thread 写权的客户端 | 同一 thread 同时只有一个 writer |
| prepared selection | 已连接、但尚未改变当前选择的 thread 目标 | 不是已提交会话 |
| selection lease | 已提交的 backend、连接、selection epoch、thread 和 writer 身份 | 不是可跨切换复用的旧输出 |
| shared daemon | App、CLI 和桥接复用的 Codex App Server 后台 | 不是微信桥接服务 |
| steer / queue | 追加到运行中的 turn / 等 turn 结束后执行 | 两者不能互换 |
| detach | 切换或新建会话时放弃旧回合的本地交付 | 不是 stop |
| inbox / outbox | 尚未开始的输入 / 等待发给微信的输出 | 不是同一个队列 |
| delivery unknown | 请求被接受但没有返回消息 ID | 不是已送达，也不是明确失败 |
| reply budget | 每条入站消息允许桥接发出的回复预算 | 不是微信服务端配额 |
| release ID | 不可变安装版本的内容标识 | 不是产品版本或 Git commit |
| installation layout / 安装布局 | `.installation.json` 中以同一记录保存的平台、release 和三个运行时根 | 不是 `active-release` 运行租约或第二份配置 |

## 模块职责

| 模块 | 边界 |
| --- | --- |
| [app.py](../codex_wechat/app.py) | 组合根、状态保存和启动生命周期 |
| [backend.py](../codex_wechat/backend.py) | Codex 会话、回合、事件和设置 |
| [backend_interactions.py](../codex_wechat/backend_interactions.py) | 问题/审批 token、归属和失效 |
| [backend_turns.py](../codex_wechat/backend_turns.py) | turn 记录、错误映射和结果构造 |
| [backend_runtime.py](../codex_wechat/backend_runtime.py) / [codex_transport.py](../codex_wechat/codex_transport.py) | 后台所有权、JSON-RPC 连接和关闭 |
| [app_ipc.py](../codex_wechat/app_ipc.py) / [app_ipc_windows.py](../codex_wechat/app_ipc_windows.py) | 跟随 App 已持有的 thread；不抢 writer |
| [ingress.py](../codex_wechat/ingress.py) | 微信入站过滤、去重、引用归一化和命令路由 |
| [input_delivery.py](../codex_wechat/input_delivery.py) | 输入保存、提交、steer/queue 和恢复 |
| [output_delivery.py](../codex_wechat/output_delivery.py) | 有序出站、附件、发送确认、节流和投递未知 |
| [command_specs.py](../codex_wechat/command_specs.py) / [commands.py](../codex_wechat/commands.py) | 唯一命令目录、设置边界和命令实现 |
| [prompts.py](../codex_wechat/prompts.py) | 默认用户文案和模板渲染 |
| [diagnostics.py](../codex_wechat/diagnostics.py) | 白名单结构化事件；不记录正文和凭据 |
| [install_layout.py](../codex_wechat/install_layout.py) | 唯一安装清单、路径、release 选择和 purge owned leaves |
| [launcher.py](../codex_wechat/launcher.py) | stable 入口到已选 release 的唯一转换 |
| [platform_support.py](../codex_wechat/platform_support.py) / [service_adapter.py](../codex_wechat/service_adapter.py) | 消费安装布局；平台权限和原生服务 |
| [installer.py](../installer.py) | 安装策略、依赖和布局提交 |

模块可以独立重构；跨模块的所有权、持久状态和有外部依赖的格式属于兼容边界。

## 会话与连接所有权

- CLI 和微信优先复用原生 daemon 或显式 WebSocket。App 使用正常启动入口，项目不改写桌面入口。
- App 已持有 thread 且原生 resume 报告 writer 冲突时，桥接才尝试 App IPC follower。连接失败
  不会删除锁、停止 App 或另开一个写入后台。
- Linux App IPC 使用同用户私有 socket；Windows 使用本机命名管道并核对用户与已注册 App
  身份。Windows 管道只跟随主 `CODEX_HOME`。版本相关的接口依据见 [SOURCES](SOURCES.md)。
- 切换 thread 时会请求旧 thread 的 `thread/unsubscribe`，把 writer 交还 App。app-server
  没有卸载接口，释放失败只记日志，不阻断切换。
- 请求结果超时或断连仍是投递不确定，不切到另一个后台自动重发。审批和问题答案只返回原
  连接、会话和回合的 owner。
- 服务收到停止信号后先取消 worker；后端与微信连接在同一个总截止时间内并行尝试关闭。
  单个步骤卡住会记录结构化事件，不阻止其它关闭步骤，也不拖到 systemd 强制杀进程。

## 认证与重绑

- 确认认证失效后进入持久 `reauth_required`，暂停收发和新提交，并只提醒一次；临时断网不会
  被当作认证失效，也不会中断已运行的 Agent。
- 同号重绑保留聊天、交付和偏好。bot 变化时重新取得游标、上下文和上传引用，保留去重记录。
- 换微信账号以扫码授权结果为准，先持久化未完成标记，再隔离旧账号交付。半完成状态禁止
  继续启动业务。
- 扫码等待不持有状态锁；收到确认后才短暂停服，并在拿锁后重新核对绑定和活动回合。

## 输入与会话切换

- `/new`、`/switch` 和启动恢复都先生成 `PreparedSelection`，再按原连接和 selection
  epoch 提交。连接或选择已变化时拒绝提交，旧选择保持不变。
- 提交产生一个 `SelectionLease`；回合、审批、问题和输出必须携带该 lease 对应的
  `Source`。旧 selection 的晚到事件不能写入新会话。
- question 和 approval 还记录创建时的 lease；connection、selection epoch、thread 或 writer
  变化后，旧 token 只能失效，不能把答案送回新的运行身份。
- 输入从 inbox 进入 currentInput；RPC 前记录 dispatching，接受后记录 accepted。
  preparing 可恢复；dispatching 结果未知和 accepted 均不自动重放，用客户端消息 ID 仅追踪，
  不承诺服务端持久幂等。
- 明确不能 steer 时按 queue 排队。进入 service 前，输入和控制在有界队列内；异常恢复不会
  变成无界队列。
- `/switch` 和 `/new` 可以在回合运行时执行：新目标提交成功后，旧回合只在本地 detach，
  后台继续；旧来源未发送输出、当前输入和排队输入作废。已经进入发送 API 的一条无法撤回。
- `/model`、`/effort` 和 `/cwd` 只修改后续回合。目标准备失败时旧输入、输出和当前回合不变。
- `/new` 已创建 thread 但本地提交失败时，只尝试删除该空 thread；`/switch` 不删除用户原有
  会话。真实服务端的删除行为仍属于待验证能力。

## 输出、附件与恢复

- 每份输出携带来源、thread、turn 和目录；旧 selection 或晚到事件不能写入新会话。
- 正文、附件和完成提示成组排队。正文失败停止本组后续正文；附件可以部分失败；微信接受后、
  本机保存前崩溃仍可能重复送达，不承诺恰好一次。
- 自动回传和 `/get` 只接受当前会话 cwd 内的普通文件。该检查是保守近似，不等价于 Codex
  sandbox；附件说明 skill 是行为约束，不是强制隔离。
- 微信发送适配器只返回 `Delivered`、`AcceptedUnknown`、`Rejected`、`Retryable` 或
  `AuthenticationRequired` 五种结果；出站层只在这一个接口上决定扣预算、暂停、重试或重绑，
  不再从异常文本和原始响应推断投递状态。
- 服务端返回有效消息 ID 才算成功。HTTP 成功但没有 ID 时标记 delivery unknown，暂停当前
  运行时出站，保留队列和新增输出；下一条被接受的入站消息恢复发送，`/discard` 可主动清空。
- 代码写入的持久失败记录只使用 `partial`、`delivery_unknown` 和 `identity_changed`；只有
  `delivery_unknown` 暂停当前运行时出站，只有前两者计入连续发送失败。
- 连续发送间隔、回复预算和 `delivery=unconfirmed` 阈值由代码定义，当前值只在
  [生成参考](REFERENCE.generated.md#出站保护) 维护。回复预算按服务端处理请求计数，不按用户
  是否看见计数。

Linux 卸载器读取 unit 实际的 `TimeoutStopUSec`，等 systemd 强制停止后再保留观察宽限；
不会再用与 systemd 相同的 20 秒截止点与内核强停竞速。

## 持久状态恢复

- `bridge.json` 使用版本化状态。当前 v1 加载器要求明确的 v1 标识，并验证已知字段、排队
  输出和通知目标的结构；未知字段保留。缺失版本或其它版本不迁移。
- 不可解析或类型无效的状态会先原样移动到同目录 `bridge.corrupt-*.json`，再以空 v1 状态
  启动。隔离路径和原因写入新状态的 `stateRecovery`，同时输出本地警告和
  `state.quarantined` 诊断事件。
- 只读的 `codex-wechat status` 不隔离、不覆盖损坏文件，而是返回
  `state=recovery_required` 和原始解析/类型错误，便于先检查再启动。
- 高于当前代码支持版本的未来状态 fail closed，不移动、不覆盖；应改用支持该版本的 release。
- 隔离文件可能含尚未发送的消息和上下文，始终留在私有状态目录，不写入结构化日志或微信回复。

## 配置与提示

- `config.json` 按文件身份、时间和大小检测变化，整组校验成功后应用；无效文件保留最后生效
  的配置并写一条安全事件。
- 设置名称和生成行由 `commands.SETTINGS` 汇总；默认值与范围常量分别由
  `configuration.py`、`media.py` 和 `log_rotation.py` 定义，出站保护值由
  `output_delivery.py` 定义。[生成参考](REFERENCE.generated.md#数值设置) 通过这些代码来源
  渲染，不维护手写副本。
- 默认用户文案只来自 `prompts.py` 的 `DEFAULT_PROMPTS`，`config.prompts` 只保存覆盖。
  展示标签与审批 token、语义字段键和交付分组解耦。
- 删除配置键恢复代码默认值。模板字段统一使用英文 `snake_case`，不维护旧中文字段映射。
- 安装器把平台、release ID、数据根、配置根和状态根一次写入 `.installation.json`。stable
  入口、systemd 和 Windows supervisor 只通过该契约取路径；`active-release` 只表示仍在运行
  的 release 租约。具体位置和改根规则见 [INSTALL](INSTALL.md)。

## 诊断与隐私

- 结构化日志每行一个 JSON 事件，只写字段白名单；正文、prompt、回复、文件名、账号、token、
  Authorization、请求体、响应体和绝对路径不得进入日志。
- 事件名、字段和计时含义以 `diagnostics.py`、`latency.py` 和 `log_viewer.py` 为准。文档不
  再维护事件字段副本。
- 服务控制台日志不是结构化脱敏日志，不能原样公开。平台日志来源见 [PLATFORMS](PLATFORMS.md)。
- 诊断写盘失败或锁被占用时丢弃该条诊断，不等待、不阻断消息收发。

## 用户可见文案

- 先给用户要求的结果，再说明可行动信息；成功回执不叙述队列、worker、writer 或重连实现。
- 可能重复执行、静默丢失、旧连接未清理、能力回落或需要桌面处理的风险必须保留。
- 设置成功显示生效后的当前值；失败给出下一步。参数形态不明显时，错误提示可以附一个例子。
- 生命周期通知优先于额外的“请求已提交”回执。默认文案以 `prompts.py` 为唯一来源。

## 验证边界

`scripts/check.sh` 证明当前树通过离线检查；真实 Windows、微信、CDN、App 和模型行为见
[VALIDATION](VALIDATION.md)。文档、源码、离线结果和实机证据分别报告，不能互相替代。
