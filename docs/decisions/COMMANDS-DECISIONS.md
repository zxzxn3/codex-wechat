# 命令面决策记录

返回 [决策索引](README.md)。

更新时间：2026-11-11（原始命令面决策为 2026-11-07，本文按当前 HEAD 复核）。
本文记录已作出的产品选择及其理由，并明确列出暂未决策的行为边界；当前可执行行为以
[COMMANDS](../COMMANDS.md) 和 [PLATFORMS](../PLATFORMS.md) 为准。

## 入口

- 用户只通过 Linux `codex-wechat` 或 Windows `codex-wechat.cmd` 操作；Windows `.cmd` 只是
  调用 stable `codex-wechat.ps1` 的薄启动器，不承载业务逻辑。
- Windows 原生计划任务操作集中在内部 `windows/task-service.ps1`，不暴露顶层
  `service-stop` / `service-status`；统一使用 `stop` / `status`。
- 不提供公开 `codex-wechat cli`。普通 CLI 使用 `codex` 或对应 backend 的 launcher；
  可复用的连接、`ensure` 和 `setup` 能力继续留在内部 `codex_wechat/desktop.py` /
  `codex-desktop` / `codex-desktop.ps1`。
- 删除 `codex-wechat desktop` 和 `codex-wechat cli`；内部 helper 不作为用户命令。
- 不提供附加组件安装器；桥接继续读取随包 `skills/`，但不向其它 Codex agent 复制文件。

## 命令语义

- `autostart` 只接受 `true|false`，不保留 `on|off` 兼容；未注册服务时查询返回 `false`。
- `start` 与 `service-start` 的前置互斥检查统一在 Python 层，平台脚本只负责入口适配。
- `service-start` / `service-restart` 以稳定运行作为成功条件，不能只提交异步请求。
- `uninstall` 默认保留用户数据，显式 purge 才删除；卸载从 stable 入口分派，不依赖
  release 或活动 release 的完整性。

## 投递语义

- no-id 不自动清空积压：no-id 只证明投递状态未知，不能证明旧队列已过时。暂停只是运行时
  状态，不持久化，重启不保留；下一次被接受的入站消息解除暂停并恢复发送。需要清空时由
  用户显式执行 `/discard`。
- `staleOutbox` 已删除：恢复发送时不能证明旧队列必然过时，自动隔离会制造第二套状态语义。
  no-id 或恢复发送不会自动隔离、丢弃旧队列；剩余 outbox 按原顺序保留。
- 发送适配器返回显式 `SendOutcome`，不靠异常子类或响应 dict 形状表达结果。只有
  `Delivered` 扣预算并确认成功；`AcceptedUnknown` 扣预算后隔离且永不自动重发；
  `Rejected` 扣预算后终止该 payload；`Retryable` 不扣预算并走有界退避；
  `AuthenticationRequired` 不消费重试次数，转入重绑流程。

## 安全与权限

- Linux 以实际 `euid == 0` 拒绝 root；Windows 以管理员令牌拒绝提权终端。
- 只读 `status` / `logs` 和显式 `uninstall` 例外；CI/容器保留
  `CODEX_WECHAT_ALLOW_ROOT=1` 逃生口。
- `--purge-data` 拒绝任何经过符号链接的路径组件；不能用链接把删除范围导向外部目录。
- 用户只通过公开入口卸载；Linux stable `codex_wechat/uninstall.py` 和 Windows stable
  `uninstall.ps1` 是内部实现，避免出现第二套锁、超时和数据目录语义。

## 2026-11-10 补充决策

- 安装布局只有一个持久契约：`.installation.json` 同时保存 schema、平台、release ID、
  数据根、配置根和状态根。stable 入口只调用 `launcher.py`，卸载器和平台服务控制只通过
  `InstalledLayout` 读取同一记录；`active-release` 仅是运行期 release 租约。
- 不读取或迁移 `current`、`.runtime-roots`、`.data-root`、`.config-root`、`.state-root`
  等旧标记；旧标记即使与新清单同时出现也 fail closed。重装建立新契约，不维护原地迁移分支。
- `PurgePlan` 是安装契约给出的精确 owned leaves。Linux 和 Windows 只执行该计划，不分别
  重新解释“数据根下哪些东西属于应用”。这样可以删除调用点的重叠、链接和根目录 guard，
  把安全检查集中在不可构造非法状态的契约里。
- 会话切换只有一个提交路径：`PreparedSelection` 在不改变当前选择的情况下准备目标，
  `commit_prepared()` 必须匹配原连接和 selection epoch。成功返回 `SelectionLease`，
  输出和回合以该 lease 的 `Source` 判断归属；旧 dict 快照、启动直选和命令层恢复分支不再
  作为第二个状态机。
- 已安装实例的运行时根固定。普通更新忽略当前 shell 中变化的 XDG 变量，冲突的
  `CODEX_WECHAT_DATA_ROOT` fail closed，不提供 `--reconfigure-roots` 或换根事务。
- 需要搬迁运行时根时，用户先备份数据，再执行 purge、手动删除保留的程序安装根，并从新环境
  重新安装。通过引入服务停止、状态锁、失败恢复和多标记兼容来支持原地换根，成本高于一次性
  重新安装，因此不属于产品范围。
- purge 只删除应用拥有的已知项，不是“递归删除任意数据根”的通用工具。数据、配置和状态角色
  必须互不重叠；`config.json.tmp` 是配置替换的固定应用临时项，也属于 purge owned leaves。
  删除目标经过符号链接、包含安装树或范围过大时拒绝执行。
- Windows 清理普通目录时逐项遍历，遇到 junction、符号链接或其它重解析点只删除链接本身，
  不跟随到目标；PowerShell 5.1 的递归删除不能提供这个保证。
- purge 只停止 `WorkingDirectory` 属于当前数据根的 Linux shared daemon；保留数据的卸载
  不停止共享后台。
- bridge 状态隔离覆盖普通 JSON 语法错误、编码错误和已知字段类型错误。解析器资源上限或
  嵌套复制导致的 `RecursionError` 属于 fail-fast 边界，不承诺隔离；不为人为构造的极深
  JSON 维护单独恢复协议。
- 补丁债务雷达属于一次性历史分析启发式，不是运行契约；仓库不维护该脚本和测试，后续用
  Git 历史、issue 或 Taskwarrior 做迁移期分析。
- 服务停止采用有界关闭：worker 先取消，后端与微信连接共享总截止时间，单个关闭步骤卡住
  不会拖到 systemd 强杀，也不会阻止其它关闭步骤。

## 暂未决策

| 事项 | 当前实现 | 仍待决定 |
| --- | --- | --- |
| 非 `/queue` 命令带附件 | 命令不执行，斜杠文本和附件作为普通输入进入 inbox | 应拒绝并返回用法、忽略附件执行命令，还是定义命令与附件的组合语义 |
| `stateRecovery` 指向已删除隔离件 | 恢复标记继续保留并原样出现在 status 中 | 下次成功保存时清除、status 只显示仍存在的隔离件，还是永久保留历史记录 |

这两项改变都会影响用户可见语义，当前不因实现方便自动选择。
