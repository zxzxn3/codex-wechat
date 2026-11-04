# Windows

需要原生 Windows Python 3.11+、curl（bootstrap 与网络测速）和可用 Codex runtime。独立 CLI 或已注册 App 的内置
codex.exe 均可发现；显式 command 始终优先，不把 .cmd/.bat 当原生 exe 自动执行。
App 包版本通过 Get-AppxPackage 查询，不按文件更新时间猜路径。也可显式配置完整命令数组。

安装和首次扫码见 README。计划任务属于当前用户、交互登录、非管理员权限，首次自启关闭；
更改自启只改登录触发器，不停止实例。bootstrap_supervisor选择current版本，运行时长不设上限，
不叠加Task Scheduler重试。supervisor处理桥接崩溃退避、主动重启75和持久配置错误78。
版本环境和稳定服务入口环境都从 requirements-windows.txt 安装原生服务依赖；
安装器在激活前检查服务入口可导入 win32api、win32event、win32job。
远程重启核对所有者令牌及进程链；Windows venv 多出的启动器必须正是记录的子进程、
属于记录的 supervisor，且可执行文件与当前 venv 的解释器一致。

App / CLI / 微信共用的WS后台由backend_runtime调用本项目的原生PowerShell适配，核对
PID、创建时间、exe和回环监听归属；不抢占未知端口，不因App更新结束正在运行的旧后台。
原生后台/Job Object与桥接寿命分开。后台配置来自同一config.json，不读取系统代理或保存第二份proxy。
以前的共享后台身份记录可核对沿用；旧CodexProxy修复/配置脚本不再是产品依赖。

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
随任务停止的清理、单实例互斥、NotifyIcon 可见性、App IPC 连接与身份核对、用户重启
（含跨版本切换），以及真实微信的文本、图片、文件、语音、引用、原生提问与审批。
同日补充的平台契约核对：状态目录字节锁拒绝第二个持有者、压缩后释放；bridge.jsonl
按 5MB 轮转出 .1/.2/.3；service.log 达阈值后轮转为 .1；子进程取消后退出码已回收；
远程重启的授权依次拒绝「无令牌」「令牌不匹配」「令牌正确但非托管子进程」；安装器
`--dry-run` 未改动 releases 与 current；无 qrencode 时二维码回退可渲染。
仍待核对：App/CLI/微信三端并发共享会话；重绑未在本轮重做。源码审查、Linux/mock
测试及一次成功样本不能替代这些实机证据。

## 会话写者交接实验未通过（2026-11-04）

分支 `experiment/writer-handover` 尝试"回合结束交还写者 + 空闲跟随 App"，实机验收未通过，
因此主线保持原样（桥接持有写者，按文档的启动顺序使用）：

- app-server 在 `thread/unsubscribe` 之后仍持有写者 **约 65–100 秒**（两次实测：23:50:33 交还 →
  07:51:38 锁文件才消失；06:45:29 交还 → 06:47:09 消失）。所以"先用微信、后打开 App"时，
  在最后一条微信消息后约一分钟内打开 App 必然显示锁定；App 还会缓存该状态，需切走再切回才刷新。
- 通过 App IPC 派发的 follower 回合会把 IPC 连接与后端连接一起断开（`app_ipc.disconnected`
  紧随 `input.dispatch accepted`），桌面端回信和微信投递同时中断。
- `thread-owner-discovery` 在无可用 owner 时返回 `-32600`；该错误曾逃出 `attach_app` 终止启动，
  形成 18 秒一轮的重启循环（已在该分支修复，主线不涉及）。

主线只保留与所有权无关的部分：`{elapsed}` 与默认结束提示、`install.ps1` 任务比对、以及
"桌面占用会话时降级采纳而不是进入恢复死循环""不可用的 App relay 按不可用处理"两项健壮性修复。

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

## App IPC 由其它客户端托管时

`\\.\pipe\codex-ipc` 是单实例名，由**最先启动**的 Codex 客户端托管；2026-11-04 本机观察到
VS Code 的 Codex 扩展（`Code.exe` 扩展宿主进程）先占用它，此时桥接的默认身份校验会拒绝
跟随，表现为"App 打开的会话在微信里提示被占用"。核对方式：连一次管道并读服务进程映像。

需要在这类机器上跟随 App 时，把后端配置写成 `"appIpcTrustRouter": true`：跳过"必须是已注册
App 本体"的映像校验，保留"服务进程属于当前用户"的校验，并在 `bridge.jsonl` 记一条
`backend.app_ipc_router_trusted`。放宽后接受的是该托管者转发本会话内容的风险。

注意：这个开关只解决"跟随"，不解决"别人已经持有写者"。第三方客户端替用户持有的写者
仍然会让桥接和 App 都写不进去，只能先让它放手。

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
- 复现方法：清空代理变量后，用发布版 venv 的 python 依次请求
  `https://ilinkai.weixin.qq.com/`，对比默认解析与显式 `CurlOpt.DNS_SERVERS="192.168.0.1"`
  的耗时；再直接连 IP，验证开销只在名字解析。若本机没有这个黑洞，两侧耗时应当接近。

排查其它机器时先看 `dns_ms` 是否接近 `curl_total_ms`：是则问题在解析，不在微信服务端。
