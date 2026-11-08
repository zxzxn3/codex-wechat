# 支持矩阵与能力边界

本文同时定义当前产品支持面和能力实现边界；[VALIDATION](VALIDATION.md) 记录证据和缺口。
能力表不代表所有平台组合已通过实机验证。“支持”是已实现并
纳入维护的能力；“实验性”允许尝试但不保证；“待验证”尚未在当前平台验证。只有“不支持”
或明确拒绝的配置应 fail closed；实验性/待验证能力必须给出边界和错误，不能静默回退。

## 平台支持状态

| 范围 | Linux 状态 | Windows 状态 | 当前证据/边界 |
|---|---|---|---|
| 首次安装、版本更新、卸载 | 支持 | 待验证 | Linux 当前树有安装/入口/卸载离线测试；Windows 当前提交仍需复测 |
| 前台 `start` | 支持 | 待验证 | 状态目录锁、前台日志和退出路径有离线测试；Windows 当前提交待复测 |
| 后台服务 | 支持 | 待验证 | Linux 有 systemd 用户服务契约测试；Windows 任务时序仍需实机验证 |
| `autostart` | 支持 | 待验证 | 只切换登录触发器；Windows 当前任务 action 已改为只传安装根 |
| 自定义数据根 | 待验证 | 待验证 | 新增 fail-closed 冲突测试；Linux systemd 和 Windows 服务的端到端行为待实机复测 |
| 短命令入口 | 支持 | 待验证 | Windows `.cmd` 在非 ASCII 用户目录下仍待实机验证 |
| 共享 daemon | 支持 | 待验证 | Linux 状态/回落逻辑有离线测试；Windows App 并发仍待复测 |
| App IPC follower | 实验性 | 实验性 | 依赖 App 版本；Windows 命名管道和三端并发仍待实机复测 |
| 私人仓库安装 | 实验性 | 实验性 | 网络入口和认证路径有离线契约测试；真实 PAT 安装未在独立审查中复现 |

## 能力实现与边界

| 能力 | 实现与边界 |
| --- | --- |
| Linux / Windows 运行 | 同一应用、微信协议、配置和交付；原生服务/进程边界保留平台实现 |
| 普通 Codex / DeepSeek | 同一个 App Server 接口；DeepSeek 使用自己的启动入口和身份环境，不复制密钥 |
| App 内置 CLI 发现 | native_codex；PATH、独立安装、已注册 Windows App，Linux 已知打包位置或显式配置 |
| CLI / App / 微信共享 | ConnectionSpec + BackendRuntime + desktop；共享失败不悄悄改成另一写入后台 |
| App 与内部 CLI 接入 | 同一配置；内部 helper 可用原生 CLI --remote 做显式连接，App 连接环境仅注入子进程；不暴露公开 CLI 包装命令 |
| 代理启动器 | 仅代理与转交；项目不保存第二份 proxy 设置，后端按环境变量或平台系统代理注入 |
| 后台与连接所有权 | 原生 daemon 优先；App WS 必要时按平台管理；客户端关闭不终止共享后台 |
| 会话与模型设置 | 原生列表、搜索、翻页、恢复、新建、压缩、模型/强度/目录和 shared 模式；`/switch`、`/new` 运行中 detach 旧回合，`/model`、`/effort`、`/cwd` 只更新后续回合，普通输入不重放显示缓存 |
| steer / queue | 默认 steer，显式 queue，queue 不阻挡后续补充；明确拒绝和结果未知分开 |
| 交付恢复 | preparing 可恢复；dispatching / accepted 不自动重发，稳定客户端ID仅用于追踪 |
| 来源隔离 | backend / connection / selection / thread / turn；异步附件处理后再核对来源 |
| 中途与最终回复 | 完整 commentary / partial_answer 及时发，最终收口不重复进度，不发送推理/工具流水 |
| 原生审批和问题 | 独立控制指令、token失效、选项与自由文本；不声称转移未投递请求的所有权 |
| 停止与重启 | 准备阶段停止；重启提交屏障；本地命令不依赖慢后台；保留未提交待办 |
| 手机图片/文件输入 | 原生图片输入，普通文件精简路径引用；不附加重复图片路径提示 |
| 文件/图片/视频回传 | 附件 skill、自动回传与 `/get` 共用 cwd 范围、保守近似而非 sandbox 等价、快照、加解密、大小限制 |
| 智能切片 | AI正文与进度按结构；控制回复仅硬拆；内容不丢、UTF-8硬上限、设置可调整 |
| 顺序与发送失败 | 输出组、正文fail-stop、附件部分失败、完成提示抑制、未知投递隔离、有界失败历史与连续计数 |
| 延迟与测速 | 微信时间戳估算/发送API耗时；供应商TCP/TLS建连，不调用模型，不冒充后台真实网络路径 |
| 状态与提示 | 保留满意的紧凑格式、字段、动作与代码块位置、列表提示和短指令 |
| 用量与限额 | 原生用量/限额事件及查询；限额重置时间、跨阈值提醒，WS 不依赖本地子进程 |
| 上下文压缩 | 原生手动compact与可选压缩事件通知，不伪造自动/手动事件区分 |
| 热配置 | 单个配置文件、整组验证、模板覆盖规则、来源hint、切片、压缩与限额阈值 |
| 认证失效与通知 | 区分断网/-14，持久暂停、一次提醒、本机重绑命令；暂停新提交，保留运行中任务结果 |
| 同身份重绑 | 刷新凭据，保留交付、会话和偏好，不复制/同步双系统运行状态 |
| 重绑与部分失败 | 同微信号保留交付，bot变化重取协议状态；换微信号扫码授权后自动隔离旧交付；保留会话/偏好，未完成标记阻止身份混用 |
| 自启与崩溃恢复 | 安装时注册服务但不启用登录自启；`autostart true\|false` 只改登录触发器，不启动或停止当前实例；按平台原生机制，有界崩溃重试与配置错误退出 |
| 命令与卸载 | 公开入口统一分流；Windows 原生任务操作只在内部 helper。`uninstall` 默认保留用户数据，显式 purge 才删除；卸载从稳定入口分派，不依赖 `current`；Linux 与 Windows 的 purge 都只删已知用户数据项 |
| 权限边界 | root/管理员拒绝写操作；只读 `status`/`logs` 和显式卸载例外，容器可用 `CODEX_WECHAT_ALLOW_ROOT=1` |
| 日志查看 | 结构化 `bridge.jsonl` 共用；`--service` 读取平台原生控制台来源：Windows `service.log`（按 5 MiB 和 `logs.keep` 轮转），Linux systemd journal |
| 安装与更新 | 同一网络入口、菜单、dry-run、非交互、旧版本按自身清单校验、激活前能力检查 |
| 首次扫码与组件 | 首次交互 start / service-start 初始化，安装不扫码；skill单独入口；原生QR优先、回退按需安装 |
| 取消与资源清理 | 统一子进程收尾，重复取消不遗弃进程/管道；原子JSON临时文件失败后清理 |

媒体边界：微信桥只从已核对的微信 CDN 接收附件，并回传已存在的本机文件；不提供按任意
远程图片 URL 下载后转发的功能。这是明确的设计取舍，不是待补齐能力；保留 CDN 白名单、
HTTPS 与禁止重定向的限制。此边界约束桥接自身，不限制 Agent 在其授权工具中处理文件。

明确替换：Windows login -Rebind 的能力由统一 rebind 接管；不读取旧 shared-backend.json
代理配置或旧后台身份记录。机器专用修复脚本、D盘硬编码、独立能力JSON和试验目录不进入发行载荷。

## 明确不支持或仅实验性

- root/sudo 或管理员终端运行写命令默认拒绝；只读 `status` / `logs` 和显式 `uninstall`
  例外，容器/CI 才使用 `CODEX_WECHAT_ALLOW_ROOT=1`。
- 直接执行 `codex_wechat/uninstall.py`、`uninstall.ps1`、`windows/task-service.ps1` 等内部 helper 不属于
  用户命令契约；应通过稳定入口调用。
- Windows 空 App 发现、三端并发、`service.log` 连续轮转、非 ASCII 用户目录下的 `.cmd`、
  Linux systemd 下的 GNOME 代理探测尚未完成或未复测，不能写成全平台已验证。
- CDN 上传超时重试幂等性未证实；相关行为只在明确标注的范围内使用。
- WSL、群聊、App 主动反向接入和未经 Codex/微信支持的内部接口不在此矩阵承诺内。

## 数据根与服务的硬约束

- `CODEX_WECHAT_DATA_ROOT` 与安装根 `.data-root` 必须一致；安装器是更新标记的唯一入口。
- 服务状态按 `queued/activating → running → deactivating` 处理；过渡态必须经过稳定窗口，
  已处于 `running/active` 的服务可以直接复用。
- 版本化稳定入口只执行 `current` 指向的完整 release；损坏的指针必须给出修复指引，不能猜测
  其他版本。

版本基线：Python 3.11+；Windows PowerShell 5.1+；当前验证的 Codex 版本以
[SOURCES](SOURCES.md) 和 [VALIDATION](VALIDATION.md) 为准。未列出的 Codex/App 版本按
“待验证”处理，不应从旧版本的实机结果推断兼容。

验证状态、实机覆盖和剩余缺口集中维护在 [VALIDATION](VALIDATION.md)。能力表说明设计边界，
不能替代该页的运行证据。
