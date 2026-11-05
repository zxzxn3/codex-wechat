# 命令面决策记录

更新时间：2026-11-05。本文只记录产品决策，不代替代码或运行契约。

## 已确认

- Windows 保留 `.cmd` 短命令入口；它只是转交稳定 PowerShell 入口的启动器，不承载业务逻辑。
- Windows 的管理员终端拒绝策略补到 PowerShell 入口；`uninstall` 仍可例外。
- 从用户可见命令面移除 Windows 的 `service-stop`、`service-status`，底层服务控制保留为内部实现；Linux 不新增这两个重复顶层命令。
- 补 Linux `autostart`；Windows 同步改为接受 `true|false`，不保留 `on|off` 兼容。
- 补跨平台 `uninstall`；默认保留用户数据，显式 purge 才删除。
- 保留 `cli`；Windows 暂不增加薄转发，继续使用 `codex-desktop.ps1 cli`。`cli` 的真实价值主要在 `websocketUrl` 模式。
- 删除 `codex-wechat desktop` 别名；保留 `codex-desktop` 本身。
- `start` / `service-start` 的前置互斥检查下沉到共享 Python 层。
- `status` 的 `service` 段写入平台运行契约；Linux 和 Windows 使用相同字段形状，平台原生状态值单独说明。
- 合并平台命令入口和服务控制层，减少 PowerShell、Python、`.cmd` 之间的重复职责。
- Linux 默认拒绝 root/sudo：普通安装、更新、启动、登录、重绑、自启和服务控制都要求以普通用户运行；以实际 `euid == 0` 判断，不只检测 `sudo`。`uninstall` 作为显式例外，只读 `status` / `logs` 也放行；CI/容器保留 `CODEX_WECHAT_ALLOW_ROOT=1` 逃生口。

## 已实现

- `codex-wechat.ps1` 只定位 release 并转发到 `codex_wechat.py`；`-TaskOnly` 已删除。
- Windows 原生计划任务操作集中到内部 `windows/task-service.ps1`。
- `start` / `service-start` 的互斥检查已在 Python 层统一。
- `autostart`、`uninstall` 已跨平台；Linux 卸载默认保留数据。

## 当前设计含义

`.cmd` 的调用链是：

```text
codex-wechat <args>
  -> %USERPROFILE%\.local\bin\codex-wechat.cmd
  -> powershell -ExecutionPolicy Bypass -File <stable>\codex-wechat.ps1 <args>
```

它不修改 PATH；只有 `~/.local/bin` 已存在且在 PATH 中时才会安装。

当前合并形态是：

```text
codex-wechat / codex-wechat.cmd
  -> codex_wechat.py（唯一公开命令路由）
       -> Linux systemd adapter
       -> Windows 原生任务 helper（只做启动/停止/查询）
```
