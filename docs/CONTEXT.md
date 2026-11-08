# 术语与领域上下文

本文只定义稳定术语，不重复具体行为。运行契约见 [ARCHITECTURE](ARCHITECTURE.md)，输入与
交付见 [DELIVERY](DELIVERY.md)，配置见 [CONFIGURATION](CONFIGURATION.md)，日志见
[DIAGNOSTICS](DIAGNOSTICS.md)。新增或改名术语时先改这里，再同步相关文档和代码。

## 核心对象

| 术语 | 定义 | 不要混用 |
| --- | --- | --- |
| 桥接 / bridge | 本项目的运行进程，连接微信消息与本机 Codex App Server | 不是 Codex 后端，也不是微信客户端 |
| backend / 后端 | `backends.<name>` 定义的一个连接目标，例如 `codex`、`deepseek` | 不是模型名，也不是 provider |
| backend label | 后端在界面上的显示名，来自 `backends.<name>.label` | 不是后端键名 |
| thread / 会话 | Codex 的一条持久会话，拥有独立的 thread ID、历史和工作目录 | 不是 turn |
| turn / 回合 | 一次从输入到完成/中断的执行过程，属于某个 thread | 不是 thread，也不等于一条微信消息 |
| writer / 写入者 | 当前持有 thread 写入权的客户端；可能是原生 CLI、App 或桥接 | 不是“后端”；一个 thread 同时只有一个 writer |
| App follower | 桥接通过 App IPC 跟随 App 已持有的 thread，不抢 writer | 不是共享 daemon，也不是原生 turn/steer |
| shared daemon / 共享后台 | App、CLI 和桥接可复用的 Codex App Server 后台 | 不是微信桥接服务本身 |
| permission profile | Codex 的 `activePermissionProfile`，描述本次运行的权限档案 | 不是 backend 配置 |

## 输入与交付

| 术语 | 定义 | 不要混用 |
| --- | --- | --- |
| steer | 把新输入追加到正在运行的 turn | 不是 queue；不保证跨客户端原子性 |
| queue | 把输入排队到当前 turn 结束后再执行 | 不是 steer；不会打断当前回合 |
| detach | `/switch`、`/new` 在 turn 运行时本地放弃旧回合的交付，旧回合可在后台继续 | 不是 stop；不会主动 interrupt |
| discard | `/discard` 清空待发送 outbox | 不是 stop；不停止回合，也不撤销已发生的操作 |
| currentInput | 当前正在准备、提交或执行的输入 job | 不是 inbox 中的等待项 |
| inbox | 尚未开始的排队输入 job | 不是 outbox |
| outbox | 已生成、等待发送到微信的回复、附件和提示 | 不是 inbox |
| delivery unknown / no-id | 服务端接受了请求但没有返回消息 ID；运行时暂停出站，等待下一条入站恢复 | 不是“已送达”，也不是普通 send failed |
| reply budget | 每条入站消息允许的回复次数预算，默认 9，可配置 1–100 | 不是服务端配额 |
| slicing | 把长回复按结构或硬限制切分成多条消息 | 不是摘要或压缩 |
| source / selection epoch | 输出所属的 backend、thread、selection 代次；用于阻止旧会话晚到输出进入新会话 | 不是 request_id |

## 认证、安装与服务

| 术语 | 定义 | 不要混用 |
| --- | --- | --- |
| rebind | 重新扫码绑定微信；可能换账号或换 bot | 不是 restart |
| auth invalid / reauth_required | 确认认证失效后的持久暂停状态；保留待办和正在运行的结果 | 不是临时断网 |
| release ID | 16 位小写十六进制安装版本标识，来自 release 内容清单 | 不是产品版本号，也不是 git commit |
| `current` | 指向当前激活 release 的指针文件 | 不是“正在运行的进程” |
| `active-release` | 取得 bridge 状态锁并完成启动的进程写入的运行版本标记 | 不是 `current` 的副本 |
| stable entry | Linux `codex-wechat`、Windows `codex-wechat.cmd` 等稳定入口 | 不是某个 release 内的临时脚本 |
| install root / data root | 程序安装根与用户数据根；两者可分开 | 不是同一个目录 |
| hot config | `config.json` 变化后按整组校验并应用，不重载代码 | 不是重读后端定义 |
| approval / question | Codex 原生审批与结构化提问；分别用 `/approve`、`/deny`、`/answer` 处理 | 不是普通聊天文本 |

## 命名约定

- “会话”统一指 thread，“回合”统一指 turn；“一轮”只在描述用户交互时使用。
- “后端”统一指 `backends.<name>`；“模型”指 model，“供应商”指 provider。
- “发送失败”指请求失败或明确被拒；“投递未知”指请求被接受但没有消息 ID，两者不要互换。
- “共享后台”指可被 App/CLI 复用的 Codex daemon；“桥接服务”指 codex-wechat 自身。
- 文档标题和正文用中文；命令、配置键、代码标识和日志字段保持英文原样。
