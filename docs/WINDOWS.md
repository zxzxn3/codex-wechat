# Windows

需要原生 Windows Python 3.11+、curl（bootstrap 与网络测速）和可用 Codex runtime。独立 CLI 或已注册 App 的内置
codex.exe 均可发现；显式 command 始终优先，不把 .cmd/.bat 当原生 exe 自动执行。
App 包版本通过 Get-AppxPackage 查询，不按文件更新时间猜路径。也可显式配置完整命令数组。

## 待 Windows 实机验证（优先）

**命名管道存在性判断尚未在 Windows 实机确认。** `CodexBackend.app_available()` 当前用
`Path(r"\\.\pipe\codex-ipc").exists()` 判断 App IPC 是否出现。Windows 对命名管道路径的
`stat` 行为可能与普通文件不同；如果这里返回 `False`，桥接空闲时每 5 秒的自动 follower
检查会被跳过，表现为 App 后启动并接管会话后，桌面端回复不进入微信。Linux mock 不能
证明这一点，也不能据此直接判定为缺陷。

请在 Windows 上按以下三种状态实测，并记录 App/CLI 版本与日志：

1. 无 App 时启动桥接，确认不会误报已连接 App。
2. App 已启动并持有目标会话时，从微信 `/switch`，确认输入可达且桌面回复进入微信。
3. 桥接先空闲，随后启动 App 并让它接管当前会话，等待超过 5 秒；预期日志出现
   `backend.app_followed`，桌面端后续回复进入微信。

若第 3 项失败，应把存在性判断改为直接尝试有界连接，或提供等价的 Windows 专用探测；
仅改 mock 或把测试标绿不算实机验收。

## 本轮修复后的 Windows 验证

以下项目需要安装后的真实 Windows 进程、App、微信或 CDN 条件，不能只靠源码阅读或 Linux mock 判定。

- [ ] 用一行 PowerShell 分别执行首次安装和已安装更新，确认临时目录下载了
  `installer.py` 与 `release.py`，安装结束后没有 `ModuleNotFoundError: release`。
- [ ] 先确认没有前台桥接，再分别在没有微信绑定和已有绑定时运行 `codex-wechat.ps1 service-start`：未绑定时
  应显示二维码并等待扫码，已有绑定时应启动后台服务并输出 `后台服务已启动。`。确认
  `start` 与 `service-start` 的扫码准入一致，且 `service-start` 不会显示前台 `Ctrl+C`
  停止提示。
- [ ] 从 release A 启动服务后，执行稳定入口的 `status` 和 `logs`；确认
  `active-release` 仍指向 A，没有被只读命令改写。
- [ ] 保持 A 运行，连续更新到 B、C 且不重启桥接；确认 A 的 release 目录不会被
  清理。停止 A 并由新桥接启动后，确认下一次更新可以清理 A。
- [ ] 构造一个同名 `CodexWeChat` 任务，但把 action 指向另一个安装目录或另一个可执行文件；
  确认快速 `autostart.status` 不会误报；需要 PowerShell 检查时 `installed` 回退也不会把它当作当前安装，
  启用/禁用也不会修改该任务。
- [ ] 在真实 Windows App 上观察 `\\.\pipe\codex-ipc` 的 `Path.exists()`、App 空闲
  后接管会话和空闲 follower；结果写入验收记录，不能用 Linux mock 代替。
- [ ] 断开/重启 App，检查 `initialize`、owner discovery、turn start/steer 响应缺少
  字段时是否以受控失败结束，而不是让 `KeyError` 穿透到输入投递层。
- [ ] 人为让共享 daemon 启动失败，确认 `/status` 显示 `shared_fallback`，日志出现
  `backend.shared_unavailable` 和可理解的失败原因；配置中的 `sharedDaemon: true`
  不被改写。
- [ ] 使用带用户名和密码的系统代理，触发一次 WMI 启动的共享 daemon；用
  `Win32_Process.CommandLine` 或等价方式确认代理凭据没有进入命令行，并确认一次性
  环境文件在启动后已删除。
- [ ] 在真实 CDN 上传大文件并模拟一次超时，确认同一个上传 `filekey` 的重试不会
  产生重复对象；未确认幂等前，不把 POST 自动重试写成已验收行为。

安装和首次扫码见 README。计划任务属于当前用户、交互登录、非管理员权限，首次自启关闭；
更改自启只改登录触发器，不启动或停止实例。bootstrap_supervisor选择current版本，运行时长不设上限，
不叠加Task Scheduler重试。supervisor处理桥接崩溃退避、主动重启75和持久配置错误78。
版本环境和稳定服务入口环境都从 requirements-windows.txt 安装原生服务依赖；
安装器在激活前检查服务入口可导入 win32api、win32event、win32job。
远程重启核对所有者令牌及进程链；Windows venv 多出的启动器必须正是记录的子进程、
属于记录的 supervisor，且可执行文件与当前 venv 的解释器一致。

App / CLI / 微信共用的WS后台由backend_runtime调用本项目的原生PowerShell适配，核对
PID、创建时间、exe和回环监听归属；不抢占未知端口，不因App更新结束正在运行的旧后台。
原生后台/Job Object与桥接寿命分开。后台配置来自同一config.json；后端可按 `useSystemProxy` 读取平台代理，不保存第二份 proxy。
共享后台身份记录只读取当前数据目录；旧的共享后台记录与CodexProxy修复/配置脚本不再沿用。

正常打开 App 后可从微信 `/switch` 跟随它持有的会话，无需 codex-desktop.ps1 或修改代理入口。
通过 `\\.\pipe\codex-ipc` 复用既有 follower 协议；连接前核对管道服务进程的用户和已注册 App 路径。
仅支持主 CODEX_HOME；先在 App 打开目标会话，微信后连接。源码与新增管道用例尚不等于实机验收。

CLI 接入与后台准备保留 codex-desktop.ps1，参数放在 cli 后并用 -- 分隔；不再提供 App 启动命令。
重新绑定统一用 codex-wechat.ps1 rebind，不复制独立业务。

Windows原生通知使用NotifyIcon；通知开关、无桌面等情况可导致不可见。2026-11-04 已在
本机实机确认气泡可见，但该结论只覆盖当时的登录会话。App/CLI/微信三端共享仍未实机验证，
具体覆盖以验收报告为准。

## 目录权限不能依赖 POSIX 0700

Windows 把 POSIX 的 0700 翻译成"仅对象**所有者**"的 DACL。提权进程创建的对象所有者是
`Administrators` 组，于是安装者本身没有权限：计划任务以普通身份启动 `pythonw` 时读不到
`bootstrap_supervisor.py`，Python 只退 2 且不写 `service.log`，表现为"安装成功、服务静默失败"。

规则：安装器与运行期统一用 `platform_support.make_private()`——显式授予"当前账户 + SYSTEM +
Administrators"并断开继承；其下所有目录改为普通创建、继承父目录。临时目录用普通目录名而不是
`tempfile.mkdtemp`（它固定 0700），否则 `os.replace` 会把私有 DACL 带到安装位置。安装收尾
会以当前用户身份实际读一遍入口与 release，读不到就报错并给出 `icacls ... /reset` 修复命令。

## 诊断与实机验证

结构化诊断位于 `%LOCALAPPDATA%\codex-wechat\logs\bridge.jsonl`，使用
`codex-wechat.ps1 logs [-Follow]`；默认不显示原始控制台输出。加 `-Service` 才显示
`service.log`，该文件未承诺脱敏。日志字段与网络计时定义见 [ARCHITECTURE](ARCHITECTURE.md)。

曾观察到curl56/28和发送积压。2026-11-04 在本机定位到不可达的 IPv6 DNS 会让 curl_cffi 每次
新建连接多等 4–25 秒（详见下方「本机环境观察」，只覆盖该机器）；其它机器仍保留 TLS 校验、
系统默认 IP 选择及现有代理入口。
2026-11-04 实测：`novac2c.cdn.weixin.qq.com` 的 AAAA 地址在本机连接黑洞（10 秒以上无响应），
而双栈客户端 10/10 自动选择 IPv4（TCP 0.25–0.32 秒），因此未把该黑洞当作发送超时的成因；
超时请求的 `dns/connect/tls` 均为 0、卡在 `response_body`，指向服务器响应慢而非本机路由。
2026-11-04 的实机验收已覆盖：真实 PowerShell 5.1、计划任务生命周期、Job Object
随任务停止的清理、单实例互斥、NotifyIcon 可见性、由 writer 冲突触发的 App IPC
连接与身份核对、用户重启
（含跨版本切换），以及真实微信的文本、图片、文件、语音、引用、原生提问与审批。
同日补充的平台契约核对：状态目录字节锁拒绝第二个持有者、压缩后释放；bridge.jsonl
按 5MB 轮转出 .1/.2/.3；service.log 达阈值后轮转为 .1；子进程取消后退出码已回收；
远程重启的授权依次拒绝「无令牌」「令牌不匹配」「令牌正确但非托管子进程」；安装器
`--dry-run` 未改动 releases 与 current；无 qrencode 时二维码回退可渲染。
仍待核对：App/CLI/微信三端并发共享会话；重绑未在本轮重做。源码审查、Linux/mock
测试及一次成功样本不能替代这些实机证据。

## 自带 Codex 后端的第三方客户端会抢走会话写者

**这是本机遇到的最容易误判成"桥接 bug"的一类问题。** 任何自带 Codex 后端、并且会打开
会话的客户端，都可能：抢走 `\\.\pipe\codex-ipc` 的 router 身份，以及替用户持有会话写者
（`~/.codex/thread-writer-locks/<threadId>.lock`）。

2026-11-04 本机实测（同一份代码、同一台机器，只改一件事）：

| 项 | 装着 `openai.chatgpt` 扩展 | 卸载该扩展并重载窗口 |
|---|---|---|
| `codex-ipc` 宿主 | `Code.exe`（扩展宿主进程） | `ChatGPT.exe`（Codex App） |
| 额外 `codex.exe` 后端 | 有（父进程为扩展宿主） | 无 |
| 写者锁数量 | 4 把 | 2 把（只剩 App 与桥接各自的） |

该扩展自带完整的 `IpcRouterManager`（`extension.js` 内的 `codex-ipc` router 与
`EADDRINUSE` 退让逻辑），并会在激活时拉起自己的 `codex.exe`。搜遍其代码未发现任何
可关闭 router 的配置项（`codex.ipc`、`ipcEnabled`、`startRouter`、`ipc.router`、`enableIpc`
均不存在），因此只能通过禁用/卸载扩展来消除。

已知会抢占的客户端（不限于此列表）：VS Code 的 `openai.chatgpt` 扩展、Codex App 本体、
以及任何会把同一 `CODEX_HOME` 的会话 resume 到自己连接上的包装器/启动器。

核对方法：连一次 `\\.\pipe\codex-ipc` 并读服务进程映像，再枚举 `codex.exe` 进程按可执行
文件路径分类（扩展目录 / WindowsApps / 独立 CLI），最后对照
`~/.codex/thread-writer-locks` 的锁数量变化。

处置：出现"App 打不开会话""微信提示会话被占用"时，先按上表核对；确认是第三方客户端
抢占后，禁用该客户端（或让它在桥接之后启动），再重启桥接。

### 如果无法卸载那个客户端

桥接的身份校验要求 pipe 的宿主是**已注册 App 本体**，因此被别的客户端托管时会拒绝跟随，
日志记 `backend.app_attach_failed`。确实无法移除该客户端、又需要跟随 App 时，可以考虑给
`app_ipc_windows.verify_server()` 增加一个"信任本机其它 Codex 客户端托管的 relay"的开关：
保留"服务进程属于当前用户"的校验，只跳过映像校验。这是需要显式打开的安全降级，默认不应
存在；本项目曾实现过并已移除，需要时再评估。这类放宽只解决"跟随"，不解决"别人已经持有
写者"——后者必须让那个客户端放手。

## 会话写者交接的实测数据（2026-11-04）

本机实测记录，便于日后判断"锁没解"是预期还是缺陷：

- `thread/unsubscribe` 之后 app-server 仍会持有该会话的写者 **约 65–100 秒**（两次实测：
  23:50:33 交还 → 07:51:38 锁文件才消失；06:45:29 交还 → 06:47:09 消失）。因此"刚放手就立刻
  在 App 打开该会话"可能仍显示锁定，属服务端行为，不是桥接没释放。
- App 会缓存"锁定"状态，不会因为锁被释放就实时刷新；切换会话或点重试才会重读。
- 跟随（IPC follower）必须调用 `AppSession.activate()`：`broadcast()` 只在 `enabled` 时发布
  状态变化，漏调会让追随者"连接成功但零事件"——表现为桌面回合完成了、回复也存在，但桥接
  一条事件都收不到，进而让投递 worker 一直等待该回合。
- `thread-owner-discovery` 在"App 在运行但该会话没打开"时返回 `-32600`（App IPC:
  no-client-found）。它必须按"无法跟随"处理，否则会终止 `activate()`、让会话永不加载、
  输入暂停并每十秒重试一次。
- 回合静默兜底：断连本身会使回合立即失败（`connection_lost`），所以超时只覆盖"连接活着但
  事件停止"这一种情况，默认 900 秒无任何事件才中止。

## 共享 daemon 模式（Windows 默认关闭）

Windows 默认让桥接自己启动一个 `codex app-server`（"独立进程"）。开启共享 daemon 后，
桥接改为通过 `codex app-server proxy` 连接本机的 app-server daemon，与 Codex CLI 共用同一个
app-server。**这是 Codex CLI 双向的唯一途径**：CLI 没有 IPC follower 可以借笔，只有共用一个
app-server，两边的回合才会互相可见。

选择上，Windows App 为主应保持 `false`，Windows Codex CLI 为主应改为 `true`；Linux 相反，
CLI 为主保持 `true`，App 为主改为 `false`。

| 能力 | 独立进程（默认） | 共享 daemon |
|---|---|---|
| 微信 ↔ Codex App | 双向（App 先持笔，桥接用 IPC follower 借笔） | App 先打开会话时仍可跟随；桥接先发过消息后该会话在 App 里显示锁定 |
| 微信 ↔ Codex CLI | 不可用（只能轮流用，CLI 释放写者要 65–100 秒） | 双向 |

- 切换：`/settings shared true|false`，写入 `config.json` 的 `backends.<后端>.sharedDaemon`
  （`definitions()` 最后应用这一段，因此不会被 `codex-desktop setup` 写下的同名段覆盖），
  并立即重连，重启后保持。`/settings` 与 `/status` 都会显示当前模式。
- daemon 放手有延迟：刚退订后的私有 app-server 仍可能被 `-32600 active writer` 拒绝。因此模式
  切换会先重试约 5 秒，仍占用就以"不持笔"的方式采纳该会话（`/settings` 回复里会说明），
  下一条微信消息排队等写者，锁一放开就发出（最多等 10 分钟，重试间隔 30 秒起退避到 2 分钟；
  排队时发送一次接收提示，后续重试不重复）。`/backend` 切换不含这个让步，
  目标被占用时仍然直接报错。
- 共享模式**不会**在回合结束时退订：退订就收不到另一个客户端的回合（CLI 的消息进不了微信）。
  代价是桥接选中的那个会话在 App 里一直是锁定状态，直到桥接切到别的会话或 daemon 停止。
- 但**离开**一个会话（`/switch`、`/settings shared false`）必须退订，否则 daemon 会一直占着
  桥接历史上选过的每一个会话，App 里会累积打不开的会话。
- 共享 daemon 起不来时（例如只装了 Codex App、没有独立 CLI：`daemon start` 会报
  `this CLI has no complete local package`；或当前进程不允许拉起脱离父进程的后台进程：
  `os error 5`）自动退回独立进程，并在 `/status` 与日志 `backend.shared_unavailable` 里写明
  原因。配置里的 `sharedDaemon: true` 不会被改写。
- **计划任务上下文如何自启 daemon（2026-11-04 实测）**：Codex CLI 在启动 daemon 前会探测
  "能否用 `CREATE_BREAKAWAY_FROM_JOB` 起子进程"，被拒绝就报
  `this Windows launcher prevents background processes from outliving it`（源码
  `app-server-daemon/src/backend/windows.rs::ensure_detached_launch`，官方文档也写明
  "Windows startup requires a non-elevated terminal whose host permits detached child processes"）。
  计划任务的 job 禁止 breakaway，所以直接启动必定失败。桥接因此保留直连尝试，失败后改用 WMI
  启动——该进程不在我们的 job 里。WMI 调用优先走管道：`pywin32` 的 COM 接口约 0.07 秒，而
  `powershell -Command Invoke-CimMethod` 要约 1.9 秒（PowerShell 启动开销），后者仅作为
  pywin32 不可用时的回退。实测（2026-11-04）：停掉 daemon 后经 `Start-ScheduledTask` 重启桥接，
  `bridge.start` → `backend.connected` **约 0.9 秒**（改造前 2.07 秒），随后 `backend.writer_kept`，
  子进程为 `app-server proxy`，即共享模式正常恢复。
- WMI 创建的子进程继承的是 WmiPrvSE 的环境，不是我们的；需要传递的
  `CODEX_HOME` 与 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY/OPENAI_BASE_URL`
  写入一次性私有环境文件，由 `windows/wmi_launch.py` 读取后启动目标，**不进入命令行**。
  官方文档也说明共享客户端继承的是 **daemon 启动时** 的环境。
- 模型请求在共享模式下由 daemon 进程发出，因此 daemon 必须继承代理环境变量：要么在带代理的
  shell 里启动它，要么把 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` 写进 `backends.<后端>.env`，
  由桥接启动 daemon 时传下去。
- 没写 `*_PROXY` 时桥接会读本机代理设置（Windows 的 WinINET、其它平台由
  `urllib.request.getproxies()` 决定）并补进后端环境；显式设置的环境变量优先，检测不到就
  保持原样，`OPENAI_BASE_URL` 指向本机或内网时也不注入（本地网关不经互联网代理）。
  该行为默认开启，可用 `backends.<后端>.useSystemProxy: false` 关闭。关闭后不自动探测，
  并清除从桥接进程继承的代理变量；`backends.<后端>.env` 中显式配置的代理仍优先保留。
  `backends.<后端>.label` 是可选显示名，不改变切换键。
  后端定义在进程启动和每次 `/backend` 前读取；修改当前后端后再次执行 `/backend`
  会重连该后端。`/settings` 不会重读这些后端定义。
  注意：任何 `*_PROXY` 变量（包括 `ALL_PROXY`）都算用户显式选择，会跳过注入——若把
  `ALL_PROXY` 设成 socks 而 WebSocket 客户端不支持 socks，这条路就静默回到旧行为。
  之所以需要注入，是因为 Codex 只有 HTTP 客户端跟随系统代理，**Responses WebSocket 只读
  环境变量**：代理机器上第一条
  消息会先按 15 秒一次重试 WebSocket，第六次失败后才回退 HTTP，实测第一条消息因此多等约
  100 秒（直连 114.9 秒 / 注入代理后 6.7 秒，同一提示词、同一版本）。注入时会记一条
  `backend.proxy_inherited`（只含 `host:port`），便于确认这次连接到底走了哪个代理。
- 这不是最佳解。**更好的做法是上游让 WebSocket 连接也跟随系统代理**：那时这段注入应当删掉，
  只剩"用户显式配置"这一条路径。桥内注入的覆盖范围只到我们自己拉起的进程树；App 先启动的
  daemon 仍然按它自己的环境走，所以它顶多是兼容层，不是产品特性。
- 上游目前已经有一个开关能做到同样的事：在 `~/.codex/config.toml` 里写
  `[features] respect_system_proxy = true`（或在命令行加
  `-c features.respect_system_proxy=true`）。本机实测同一条提示词：无代理变量 114.90 秒 /
  加该开关 4.52 秒。它的覆盖面比桥内注入更广（App 自己启动的 daemon 也生效），代价是需要每台
  机器单独配置、并且它仍是实验 flag，上游若改名或移除就失效。桥内注入存在的理由就是**免配置**：
  装完即可用，不要求用户先知道有这么一个开关。两者可以并存。
  该开关当前只覆盖 macOS/Windows；Linux 的 Codex 代理解析器会返回不可用，仍由本项目读取
  GNOME manual 代理，不能把该 flag 当作 Linux 修复。
- 切换模式会重建后端连接，因此任务进行中（或有排队、正在切换会话）会被拒绝，等待结束或
  `/stop` 后再试。

## 本机环境观察（不代表其它机器）

以下只描述 2026-11-04 这台机器的网络环境，不是产品行为，也不应写成对外的通用结论。

- 本机 WLAN 通过路由通告（RA/RDNSS）拿到两个 IPv6 DNS（`2409:8057:2000:6::8`、
  `2409:8057:2000:2::8`），两者在本机都完全不可达：查询 15 秒超时，到它们的 TCP 连接也为假。
  `netsh interface ipv6 set dnsservers ... address=none` 会回复「没有配置 DNS」，但通告值仍在，
  说明 Windows 没有删除入口。
- 桥接的 HTTP 客户端用 curl_cffi（自带解析器，不共享 Windows 解析缓存）。它会先试不可达的
  IPv6 DNS 再回落 IPv4，于是**每次新建连接**多花 4–25 秒：日志表现为 `dns_ms≈4040` 且
  `pretransfer_ms≈dns_ms`，或在 `curl 28` 里停在 `pretransfer` 阶段。连接复用时为 0，
  所以只有空闲后的第一条消息、首次扫码、`/ping` 和 `输入中` 会受影响。
- 本机处置：`Disable-NetAdapterBinding -Name "WLAN" -ComponentID ms_tcpip6`。处置前后同一测试
  （发布版 venv、清空 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`）从 4.16 秒降到 0.12–0.18 秒；
  需要 IPv6 时用 `Enable-NetAdapterBinding` 恢复。该网络 IPv6 本来不可用，禁用没有实际损失。
- 本机代理只存在于注册表（WinINET，`127.0.0.1:7897`），用户级/机器级**没有** `*_PROXY`
  环境变量。Codex CLI 的 Responses WebSocket 只读环境变量，于是每次新建连接先失败 5 次
  （`responses_websocket: failed to connect`）再回退 HTTPS：能用，但多约 10 秒。
  桥接自 2026-11-04 起会自己把本机代理写进 daemon 环境，所以经微信发的消息不再付这笔钱；
  直接 `codex` / App 仍按各自环境走，必要时保留下面的包装函数。
- 本机处置：在 PowerShell `profile.ps1` 里加一个 `codex` 包装函数，调用前设
  `HTTPS_PROXY/HTTP_PROXY/ALL_PROXY` 与 `NO_PROXY`（排除 `localhost`、`127.0.0.1`、
  `ilinkai.weixin.qq.com`、`*.weixin.qq.com`），调用后恢复。实测同一 `codex exec`：
  无代理变量时出现 5 次重连与回退告警；经包装后一次成功、无重连。共享 daemon 模式下
  模型请求在 daemon 进程内发出，因此 daemon 也必须在该包装过的 shell 里重启才会继承代理。
- 复现方法：清空代理变量后，用发布版 venv 的 python 依次请求
  `https://ilinkai.weixin.qq.com/`，对比默认解析与显式 `CurlOpt.DNS_SERVERS="192.168.0.1"`
  的耗时；再直接连 IP，验证开销只在名字解析。若本机没有这个黑洞，两侧耗时应当接近。

排查其它机器时先看 `dns_ms` 是否接近 `curl_total_ms`：是则问题在解析，不在微信服务端。
