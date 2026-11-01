# 维护与恢复契约

## 职责

- `codex_transport.py`：启动器、共享 daemon 接入、WebSocket/stdio、RPC 配对与连接关闭。
- `backend.py`：会话、原生事件、轮次与审批；通过 transport 访问 Codex。恢复已有会话与普通 turn/start 不重放缓存的 model/effort/cwd，只有显式设置命令传覆盖；设置事件更新显示缓存。Codex 保存的设置和轮次状态是权威；限额属于账户缓存，模型目录属于连接缓存，不随切换会话丢失。
- `codex_wechat.py`：组合根、微信接收、控制调度与共享运行上下文。
- `input_delivery.py`：输入准备/提交状态转换、恢复、steer 和 queue 协调。
- `output_delivery.py`：输出组、附件快照、重试与顺序发送。连接、准备任务、模型轮次和发送状态不能简化为同一个忙闲值。
- `records.py`：跨边界的 `Source`、`TurnResult`、输入阶段；不实现通用状态机。
- `commands.py`、`command_specs.py`：会话操作和唯一指令目录；目录决定别名、帮助、参数约束、后端需求和执行通道，保持无业务依赖；处理函数由 commands 的单一分发表关联，Bridge 只调用 dispatch。语法拒绝先于后端连接。
- `prompts.py`、`errors.py`、`configuration.py`：展示、错误分类、配置默认值/迁移；不重复模型或权限清单。
- `questions.py`：原生问题文字展示和选项转换。backend.pending 保存请求及来源、逐题答案；/answer 走 urgent 通道，不进入模型输入队列。每题独立短编号，resolved/切换/断线清理；最终响应写入结果未知时不重试。原生问题与审批互不使用对方的响应格式。
- `slicing.py`：纯文本切片及预览。`media.py` 先剥离附件标记并校验快照，正文才进入切片。
- `wechat.py`、`storage.py`、`profiles.py`、`service_control.py`：微信协议、私有原子 JSON、后端选择、用户服务重启。
- `platform_support.py`：平台路径、原生可执行文件发现、curl 名称和无窗口子进程选项；Linux 路径与默认行为不变。Windows 数据与代码分离，文件锁用 msvcrt、持久化写 UTF-8，不以 chmod 冒充 Windows ACL。
- `pipe_websocket.py`：显式 Windows 共享模式的管道适配，复用 websockets Sans-I/O 的握手、掩码、帧校验和控制帧；不开放 TCP 监听、不依赖 asyncio Unix socket。Windows 默认 stdio；共享模式的 CLI/桌面行为待实机确认。

输入/输出组件引用同一个运行上下文，由 Bridge 统一保存；不复制当前后端或持久队列。保留 Bridge 薄入口以兼容调用与离线用例。

持久化选择的 backend/thread、用户覆盖和微信交付信息。连接对象、连接身份、选择代次、active/pending/loaded 留在内存；不把旧连接的运行状态当恢复事实。

## 输入交付

1. 普通输入默认 steer，`/queue` 在接收阶段剥离前缀后作为 queue 输入，与游标和去重 ID 一起保存，不走内存控制队列。队列中的 queue 不阻塞后来的 steer；queue 在桌面轮次抢先启动的竞态下也不得转为 steer。客户端 ID 在入队时生成，重启或明确拒绝后的排队回退不改变 ID。
2. 从 inbox 取出时，在同一份状态快照转入 currentInput，阶段为 preparing；附件准备完成后保存路径。
3. 输入预检完成、实际 RPC 之前保存 dispatching、来源及目标轮次。接受响应后保存 accepted。
4. 最终结果准备完成后，清除 currentInput 与加入整个输出组在一次保存中完成。
5. 重启时 preparing 回到待办；dispatching 进入 uncertain 记录；accepted 只记为已接受。后两者不自动执行。记录有界保留 20 项。

RPC 参数/方法验证拒绝和已识别的轮次前置条件错误可判为未投递；内部错误、未知拒绝、超时及断连不作此保证。steer 明确不能接受时转 queue；未知结果记录后通知，不自动重试。不能仅根据 clientUserMessageId 字段存在声称服务器幂等。

`/stop` 清待办并中断当前共享轮次；准备阶段同样检查停止，预检过程中也不丢停止请求。已被后端接受的补充不能撤回。旧版本仅保存 running、没有保存当前输入正文的情况无法恢复正文，只保留旧待办并提醒。

## 来源与输出

TurnResult 固定携带 backend、connection ID、selection epoch、thread、turn、cwd、正文和完成状态，桌面和微信发起的轮次使用同一格式。

完整的中途 agentMessage（commentary / partial_answer）作为 AgentMessage 携带相同来源身份，在 item/completed 时同步冻结到发件箱，避免独立异步进度队列被最终回答抢先。使用原生 item ID 去重；轮次结束后的迟到 item 不发送。中途回复只入正文，不退休 currentInput、不发结束提示、不处理附件；最终结果保留原有轮次收口契约。无 phase 的旧消息仍等待最终收口。

尚未进入发件箱的外部结果，只接受当前来源；异步文件快照结束后再校验一次。已入发件箱的结果保持原来源，不因切换重新解释。切换回同一 thread 也不能接受之前连接/选择代次的迟到结果。当前不会回放离线期间的结果。

输出组入队时确定切片、顺序、稳定消息 ID、成员编号和总数。每次成功发送后保存剩余队列。永久正文失败保存失败成员和跳过成员，停止整个组；单附件失败只抑制完成提示，允许其余附件继续。失败记录有界保留 20 条。`consecutiveSendFailures` 独立持久化，重试耗尽加一；成功发送后的出队、计数清零在同一次保存中完成。它属于微信通道，不随 backend/thread 切换或重启重置；旧状态缺少该键时初始化 0，不从历史推算。所有成员顺序发送，暂不做优先级插队。

崩溃可能发生在微信接受消息但本机尚未保存成功之间，因此仍可能重复送达。不能靠本地 ID 承诺恰好一次。旧发件箱没有组信息的条目继续独立发送，不猜测它们的分组。

## 并发与关闭

微信 HTTP 使用可取消的 curl 子进程；任务取消时终止并回收请求，避免 urllib 后台线程拖延重启退出。凭据及请求体通过 stdin 配置传入，不出现在 argv；不跟随重定向。微信长轮询与发送独立运行；receive 不等待慢控制指令。指令分为本地、停止/审批、后端、测速、附件五个有界内存通道，各自串行处理。停止/审批等待有期限，不阻塞本地帮助或状态。控制消息默认不持久重放。重启在本地通道：拒绝已提交/不确定输入、活动轮次、steering 和用户会话操作；允许保留待办及准备阶段，后台恢复锁不挡重启。restarting 在首个 await 前关闭提交屏障，准备任务退回待办，失败时重新开放；手动发送附件在接收指令时冻结来源、目录和回复 context，独立通道的快照工作不阻塞本地控制。

连接锁防止重复连接；会话锁保护后端/会话切换；任务标记防止处理中修改设置。后台连接维护与收发并行，失败后重试，不因此清空输入。`/status` 只读缓存，不等待账户查询；账户限额由后台尽力刷新，模型任务运行中也刷新。Token 更新按 thread 暂存最多 20 项，恢复响应到达前的更新不会因尚未选中目标而丢失；重连清除此连接的计数缓存。缺少事件时从原生 Thread.path 的当前会话 JSONL 有界尾部提取 token_count 元数据；回退读取按路径、inode、mtime 和大小缓存（最多 20 项）；实时事件优先，切换/断线后丢弃迟到读取结果，不推算未知用量。

close 只结束自己的 proxy/独立子进程，不中断共享 daemon。原有 expectedTurnId、runtime generation、turn/start 早到事件缓冲和输入预检停止检查继续保留。原生 settings/update、队列迁移、退订旧 thread 和输入接受状态历史核对仍未引入：官方已读页面没有充分建立这些接口在当前版本的持久化/跨客户端保证。保留本地交付契约，不因 schema 存在而替换。

控制回复的标题/动作在代码块外，数据在块内，操作语法在块外用行内代码；参数保留 `{}`，字段使用英文冒号。审批中的待执行命令是数据，批准/拒绝指令是块外操作。

## 后台启动

`daemon_control.py` 只读取本桥接稳定命名 unit 的 systemd 状态，不读取 journal 或凭据。active/activating 启动复用；失败状态保留以识别退出码，下次重试 reset-failed 后再启动。DeepSeek launcher 专用退出码 78 映射密钥环未就绪，冷却 60 秒；不停止运行中的共享 daemon。

Windows 使用当前交互用户的计划任务和 `windows_service.py` supervisor；`/restart` 通过专用退出码重开桥接子进程，不重启正在运行的同一个任务。数据、配置、日志默认在 LOCALAPPDATA/codex-wechat。未配置的 codex-deepseek 不出现在后端列表，显式配置仍沿用 command/env 接口。Windows 探测只使用启动时选取的网络环境，不读取 /proc，也不声称能观察自定义启动器内部再次修改的环境。

监督器用 Local mutex 防重复实例，以短期 token 和父子 PID 确认重启归属。桥接加入 KILL_ON_JOB_CLOSE + SILENT_BREAKAWAY_OK 的 Job Object：停止监督器可清理桥接，同时其后代可脱离该 Job，避免将共享 Codex daemon 一并结束；后代退出仍依赖桥接与原生 CLI 的生命周期，实际进程树行为待 Windows 验证。此机制不保证清理任意外部工具进程。

## 自启与失败恢复

`autostart.py` 读取系统实际注册状态；/settings 直接调用，不经过后端、不持久化第二份布尔值。Linux 核对 FragmentPath 属于当前项目，缺失时只有显式开启可创建；首次创建还要求规范项目目录。enable/disable 之后读取实际结果；disable 会移除服务链接，因此重建 link 保留手动启动能力。Windows 委托本项目 PowerShell 入口，只切换登录触发器，不停止当前实例、不禁用整项任务。Linux 安装器仅准备命令入口，不注册、启用或启动服务；查询缺少服务视为关闭，首次显式开启才 enable，并启动缺失服务。Windows 仍由安装器注册任务，首次默认关闭登录触发器；已有状态不重置。修改不是后台持续重试的同步器。

已识别的启动配置错误使用 ConfigurationError / exit 78；运行期未知故障仍属普通异常。Linux 的 systemd 限制 300 秒/5 次启动，78 不重启；Windows supervisor 0/78 不重开、75 主动重启、其他退出采用有界退避，停止后保留失败结果，计划任务不叠加重试。微信网络重连和单条消息发送重试是独立交付策略，不因此禁用。

Linux 前台进程首次开启自启时，默认路径、空闲和无审批是移交前提；先设置提交屏障，安装并启用服务，发出确认、排空发件箱后再请求服务启动并取消前台主任务。新服务只有确认自身为 systemd MainPID 才能等待旧状态锁（最多30秒）；普通第二个进程仍立即拒绝，不容许两个桥接并行工作。Windows 不使用这一 Linux 交接路径。

短命令只在 COMMANDS.aliases 中声明，注册表拒绝重复名称；帮助从相同元数据生成，不做任意前缀推断。别名继承原命令完整参数校验、lane 和输入 route。/settings、/sessions 及危险控制无短名。

## 配置热加载

热配置键集中在 `configuration.HOT_CONFIG_KEYS`，由小型 `ConfigLoader` 管理，仍使用同一个 config.json。按文件 inode、修改时间和大小检测变化；应用前先验证全部热配置，失败或文件暂时缺失时保留旧值。接收消息、构造模型输入、生成输出及处理压缩/限额事件前按需刷新，无后台文件监视器。只更新白名单，不替换整个启动配置；提示文件键实际变化时才清除对应微信覆盖。已提交输入和已入发件箱的输出保持原样。

## 离线验证

测试只在临时目录创建状态，使用模拟 RPC、通道、异步事件及本地 fake_server.py；不启动真实 Codex、微信或 systemd，不读真实状态/配置/会话内容。执行角色遵循用户指定的专用测试会话。

在项目目录使用具备 requirements 的 Python：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

关注五组契约：输入准备与提交崩溃恢复；慢后端不阻塞本地控制及停止；来源隔离与账户缓存；分组部分失败；切片的内容、结构与 UTF-8 上限。旧有审批、路径权限和最终回答过滤用例保留。

故障注入只替换本地模拟方法。新增针对性用例可在 tests 中编写；不以“测试需要”为由接入当前用户会话或向微信发消息。测试报告区分静态检查、离线覆盖和未验证的在线路径。
