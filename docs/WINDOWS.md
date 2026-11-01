# Windows

原生 Windows 与 Linux 共用 installer.py，只支持 Codex CLI；codex-deepseek 可以后续通过 backend command/env 接入。桌面 App 适配另行开发。

## 安装与更新

公开仓库确定后，使用 README 中 PowerShell 一行入口；也可先下载脚本审阅再执行。入口需要 Python 3.11+ 与 Git，Windows 下载使用 Invoke-WebRequest，不要求 curl。运行桥接仍需要原生 Codex CLI 和 curl.exe，缺失会提示。

本地源码启动统一菜单：

```powershell
py -3 installer.py
# 或 .\install.ps1
```

菜单提供安装/更新、附加组件、退出；默认位置为 `%LOCALAPPDATA%\codex-wechat\app`，用户数据在其父目录。安装不扫码、不录入凭据、不启动程序；新自启默认关闭，更新保留既有选择。窗口后台、非终端环境请显式用 `--non-interactive`（PS 适配参数 `-NonInteractive`）。

```powershell
py -3 installer.py --dry-run
py -3 installer.py install --non-interactive
py -3 installer.py update --repo https://github.com/OWNER/REPO --non-interactive
```

演练不会写目录、建环境或操作任务，阻塞检查仅提示。网络入口的 -DryRun 不下载，只显示流程计划。安装过程不提供 rollback 或失败后自动恢复；版本切换前后发生的失败会保留实际状态并报错。

## 使用

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" start
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" relink
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" menu
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" update
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-restart
```

首次 start 在需要时引导微信绑定，已有登录不重复初始化。`/settings autostart on|off` 仅改变登录自启；计划任务为当前交互用户运行，不需管理员权限、不在登出后运行。安装器保留当前运行实例，更新后由用户决定重启时机。

接口返回 `-14` 时后台不会退出；它保存 `reauth_required`、暂停微信收发并保留待办。运行 `relink` 会通过共享 Python 逻辑检查任务、停止并等待计划任务、扫码和恢复服务。有活动任务时加 `-StopActive`；它允许停止微信网关，但不保证中断已经提交到共享 Codex 的轮次。扫码账号不同时必须显式加 `-ReplaceIdentity`，旧聊天数据会隔离。命令只接受默认数据路径和交互终端。

附加组件从统一菜单安装；微信附件 skill 默认安装到当前用户 `.agents/skills`，可用 --skills-dir 明确指定其他目录；主体安装不自动附带组件。

## 验证边界

旧版 Windows 11 实机曾确认直接安装、版本化迁移、同版更新和当时的回滚流程；这些证据不证明本次菜单、安装策略和精简后的计划任务集成已在 Windows 验证。本轮应以专用测试会话报告为准；Windows PowerShell 5.1、真实任务、扫码和微信链路仍需实机验证。
