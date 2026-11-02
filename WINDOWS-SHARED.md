# Windows 共用后台实验

完整事实、改动清单和合并建议见 [WINDOWS-HANDOFF.md](WINDOWS-HANDOFF.md)。所有实验设计均可重新评估，并非用户要求永久保留。

App、CLI 和微信复用同一个回环 WebSocket App Server。程序属于 codex-wechat 的 `windows/`，由稳定入口 `codex-desktop.ps1` 路由当前版本；外部 CodexProxy 启动器只设置代理并转交。

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" app
& "$env:LOCALAPPDATA\codex-wechat\app\codex-desktop.ps1" cli resume
& "$env:LOCALAPPDATA\codex-wechat\app\codex-desktop.ps1" ensure
```

正常使用仍点现有 Codex 快捷方式。直接从原生入口打开不保证连接共享后台；从项目入口打开不需要先运行微信桥。退出任一客户端不结束后台，撤销共享路由也不等于释放既有线程写入权。

运行配置、进程记录和启动日志在 `%LOCALAPPDATA%\codex-wechat\shared`。旧 CodexProxy 位置仅保留兼容入口与诊断历史；不要把机器配置、账号或 PID 记录合并进 Linux。App 连接变量仅对本次启动进程生效。

微信重新绑定使用 `codex-wechat.ps1 login -Rebind`，先停桥接；不需要删除整个 state。更新仍经安装器，之后按需重启桥接，不能把微信 /restart 当作下载更新。

当前官方 WS 接入属于实验性，App 环境变量来自本机版本实现。用户曾确认 App/微信共享收发与代理有效；后续重构和新 CLI 三端并发尚未验收。专用测试会话未确认可用，未代测。依据：https://learn.chatgpt.com/docs/app-server
