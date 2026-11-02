# 整体设计复审

> 代理评审提案，不代表用户已认可或要求保留；最新情况以 [WINDOWS-HANDOFF.md](WINDOWS-HANDOFF.md) 为准。

2026-11-02。范围：本实验副本、原导出副本、CodexProxy 本机脚本；对应 CLI 0.166.0-alpha.5.1 与本机 App 实现。静态评审，不修改运行配置，不运行模型或测试。

## 结论

主干应是“微信客户端 → 原生 App Server ← 桌面客户端”。保留微信交付逻辑，重整后台管理和部署；不为减少文件而重写已经稳定的功能。

## 确认事实

- 同版本原生源码将 stdio 标为 single_client_mode；stdio、Unix socket、TCP WebSocket 是不同启动分支，CLI 的 --stdio 与 --listen 冲突。不能假设 App 的默认后台还藏着第二个本机监听入口。
- 原生代码有 Windows daemon 生命周期管理，包括进程身份、锁、后台启动、退出和更新。App 不选择某条 Windows daemon 接入路径，不代表 CLI 没有原生管理能力。
- daemon 的连接端点能否直接满足 App 当前接入路径，尚未确认；不能据前一条直接删除我们的管理代码。
- 当前 App 的 WS 路径不经过 stdio 路径的 getConfigOverrides。连接成功不证明桌面工具和配置覆盖完全等价。
- 用户已确认本机共享会话收发和代理有效；这不等于所有 App 功能、升级和退出场景通过。

## 需要优先改进

1. **后台管理分散。** App 入口和微信连接层都调用启动脚本，transport 还包含原生 daemon 启动，修复助手再改部署。应只有一处负责后台身份、启动、就绪和所有权；各入口仅调用它。连接关闭永不推导成停止共享后台。
2. **实验部署容易退回旧模式。** Repair-Wechat.ps1 默认使用原副本；共享配置可能保留，但程序被更新成不识别 websocketUrl 的旧实现。启用、更新、停用必须形成完整操作，检查配置与程序能力匹配；机器脚本不能作为正式安装器外的第二套安装器。
3. **传输模块承担过多职责。** CodexTransport 同时处理发现、进程管理、systemd、管道、WebSocket 和 RPC。保留 RPC 接口，将后台准备与连接分开；只为已有的 stdio 和共享连接提供小实现，不再增加通用框架。
4. **退出修正尚未统一。** wechat.py、media.py、latency.py 各有 curl 子进程生命周期代码；只修文字请求不能证明附件和测速退出安全。统一进程取消与管道回收，分别保留请求限制和业务错误处理；需针对取消、超时和部分输出验证。
5. **配置观测点不清。** 共享后台的真实环境与桥接进程环境可能不同；Windows probe_target 当前采用 transport.launch_env，不能把探测结果当成后台实际路径的测量。设置以原生返回为准；本地缓存说明来源，不能静默变成覆盖。
6. **重新绑定缺少明确操作。** 不应长期依靠清空整个 .state。显式重新绑定时区分微信身份相关游标、context、旧发件箱与用户偏好、Agent 会话选择；旧身份消息不自动移交新身份。
7. **支持范围缺少闭环。** App 的配置覆盖、原生工具、审批/问题归属、连接断开后事件与输出行为需单独核对。CLI 路径、CODEX_HOME、连接类型和最低能力集中声明，不能以“二进制相同”代替完整支持保证。

## 应保留

- 原生认证、会话、turn/steer、compact、模型和限额接口；不复制供应商客户端与密钥管理。
- 微信输入的 preparing/dispatching/accepted/uncertain、不自动重发结果未知的输入；这些是微信交付契约，不能仅因原生有 queue 就删除。
- 来源身份、顺序发件箱、分组失败策略、媒体权限校验、切片和用户已满意的提示格式。
- 微信桥自己的登录自启与重启管理。原生 daemon 管理 Codex，不等于能代替微信桥的监督器。
- bounded usage fallback 暂保留；原生指标更完整后再评估移除，不扩大对私有历史格式的依赖。

## 建议顺序

先统一支持范围与后台所有权，并消除更新后配置/程序不匹配；再核对原生 daemon 与 App 的连接组合；之后收口部署与重复进程清理。最后才评估原生 queue/settings 能替换多少代码。每步独立验收，不一轮重做整个项目。

## 依据

- 同版本启动分支：https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/app-server/src/lib.rs#L768
- CLI 参数互斥：https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/cli/src/main.rs#L533
- 原生生命周期：https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/app-server-daemon/src/lib.rs
- Windows 进程管理：https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/app-server-daemon/src/backend/windows.rs
- 协议文档：https://learn.chatgpt.com/docs/app-server
- App 入口与配置覆盖：本机安装包 app.asar 的 eRY、getConfigOverrides 与 CODEX_APP_SERVER_WS_URL 路径；属于版本相关实现，不是公开稳定承诺。
## 本轮落地状态

已实现：后台准备从 RPC 传输抽出；三处 curl 生命周期合并；共享能力更新门禁和构建前导入检查；更新助手不再改配置或默退旧源码；代理配置集中；显式重新绑定及部分提交隔离；测速观测点澄清。

保留：原生认证与会话协议、输入/输出交付契约、现有紧凑提示。未引入新的转接层，也未改变正在运行的后台。

待验证：新增 11 项定向用例和旧套件、跨平台回归、真实退出和重连、原生工具与审批。专用测试会话未能确认可用，本轮未代测。原生 daemon 对 App 接入的直接替代仍未完成兼容证明，不能宣称已删去自管后台。

静态检查结果：68 个 Python 文件解析通过；变更实现模块的全局名称检查通过；5 个脚本用 Windows PowerShell 5.1 解析通过；JSON 声明有效。未运行测试套件、安装器、模型请求或服务重启。实际源文件变更清单见 .refactor-files.json。
