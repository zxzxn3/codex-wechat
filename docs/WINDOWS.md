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

## 诊断与实机验证

结构化诊断位于 `%LOCALAPPDATA%\codex-wechat\logs\bridge.jsonl`，使用
`codex-wechat.ps1 logs [-Follow]`；默认不显示原始控制台输出。加 `-Service` 才显示
`service.log`，该文件未承诺脱敏。日志字段与网络计时定义见 [ARCHITECTURE](ARCHITECTURE.md)。

曾观察到curl56/28和发送积压，但尚无充分证据将根因归于IPv6；保留TLS校验、系统默认IP选择及现有代理入口。
2026-11-04 实测：`novac2c.cdn.weixin.qq.com` 的 AAAA 地址在本机连接黑洞（10 秒以上无响应），
而双栈客户端 10/10 自动选择 IPv4（TCP 0.25–0.32 秒），因此未把该黑洞当作发送超时的成因；
超时请求的 `dns/connect/tls` 均为 0、卡在 `response_body`，指向服务器响应慢而非本机路由。
2026-11-04 的实机验收已覆盖：真实 PowerShell 5.1、计划任务生命周期、Job Object
随任务停止的清理、单实例互斥、NotifyIcon 可见性、App IPC 连接与身份核对、用户重启
（含跨版本切换），以及真实微信的文本、图片、文件、语音、引用、原生提问与审批。
仍待核对：字节锁、日志轮转、子进程取消、App/CLI/微信三端并发共享会话；重绑未在
本轮重做。源码审查、Linux/mock 测试及一次成功样本不能替代这些实机证据。
