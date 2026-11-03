# Windows

需要原生 Windows Python 3.11+、curl（bootstrap 与网络测速）和可用 Codex runtime。独立 CLI 或已注册 App 的内置
codex.exe 均可发现；显式 command 始终优先，不把 .cmd/.bat 当原生 exe 自动执行。
App 包版本通过 Get-AppxPackage 查询，不按文件更新时间猜路径。也可显式配置完整命令数组。

安装和首次扫码见 README。计划任务属于当前用户、交互登录、非管理员权限，首次自启关闭；
更改自启只改登录触发器，不停止实例。bootstrap_supervisor选择current版本，运行时长不设上限，
不叠加Task Scheduler重试。supervisor处理桥接崩溃退避、主动重启75和持久配置错误78。

App / CLI / 微信共用的WS后台由backend_runtime调用本项目的原生PowerShell适配，核对
PID、创建时间、exe和回环监听归属；不抢占未知端口，不因App更新结束正在运行的旧后台。
原生后台/Job Object与桥接寿命分开。后台配置来自同一config.json，不读取系统代理或保存第二份proxy。
以前的共享后台身份记录可核对沿用；旧CodexProxy修复/配置脚本不再是产品依赖。

桌面接入使用codex-desktop.ps1，原生CLI参数放在cli后，推荐用--分隔。App连接变量只给
子进程，退出不更改调用者环境。重新绑定统一用codex-wechat.ps1 rebind，不复制独立业务。

Windows原生通知使用NotifyIcon；通知开关、无桌面等情况可导致不可见。源码和Linux mock
不能证明Windows 5.1、任务、Job Object、App/CLI三端与真实微信都通过，具体覆盖以验收报告为准。

## 诊断与实机验证

结构化诊断位于 `%LOCALAPPDATA%\codex-wechat\logs\bridge.jsonl`，使用
`codex-wechat.ps1 logs [-Follow]`；默认不显示原始控制台输出。加 `-Service` 才显示
`service.log`，该文件未承诺脱敏。日志字段与网络计时定义见 [ARCHITECTURE](ARCHITECTURE.md)。

曾观察到curl56/28和发送积压，但尚无充分证据将根因归于IPv6；保留TLS校验、系统默认IP选择及现有代理入口。
需要核对真实PowerShell5.1、字节锁、日志轮转、子进程取消、计划任务、Job Object、NotifyIcon及App/CLI共享；
再由用户确认手机回复、语音/附件、重绑和重启。源码审查、Linux/mock测试及一次成功样本不能替代这些实机证据。
