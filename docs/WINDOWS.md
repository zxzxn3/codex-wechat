# Windows

需要原生 Windows Python 3.11+、curl 和可用 Codex runtime。独立 CLI 或已注册 App 的内置
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

## 本机延迟排查（2026-11-02）

Windows 实机出现 curl 56/28，某次发送卡约 19.5 秒、重试约 534ms，后续正文因此排队。
接收重试间隔上限改为 5 秒，保留 TLS 校验和系统默认的 IPv4/IPv6 自动选择。
当时进程连接 IPv6 Fake-IP，但 IPv4/IPv6 的短请求都成功，不能据此认定 IPv6 是根因。
用户随后表示卡顿暂未再出现；因无法证明因果，已撤销临时强制 IPv4，继续观察默认连接路径。

新增请求失败/慢请求耗时、发送耗时和队列长度日志，不记录正文、令牌或响应内容。
发送延迟已补 create_time_ms 缺失时回退 update_time_ms/item 时间戳；Windows 需更新到
该源码后生效，尚未实机验证。仅有语法、安装及上述实机观察，未执行单元测试。
