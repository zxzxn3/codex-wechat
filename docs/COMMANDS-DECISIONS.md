# 命令面决策记录

更新时间：2026-11-05。本文只记录已作出的产品选择及其理由；当前可执行行为以
[COMMANDS](COMMANDS.md) 和 [PLATFORM-DIFFERENCES](PLATFORM-DIFFERENCES.md) 为准。

## 入口

- 用户只通过 Linux `codex-wechat` 或 Windows `codex-wechat.cmd` 操作；Windows `.cmd` 只是
  调用 stable `codex-wechat.ps1` 的薄启动器，不承载业务逻辑。
- Windows 原生计划任务操作集中在内部 `windows/task-service.ps1`，不暴露顶层
  `service-stop` / `service-status`；统一使用 `stop` / `status`。
- 保留 `cli`；Windows 当前使用 `codex-desktop.ps1 cli`，暂不增加第二个薄转发入口。
- 删除 `codex-wechat desktop` 别名；`codex-desktop` / `codex-desktop.ps1` 继续保留。

## 命令语义

- `autostart` 只接受 `true|false`，不保留 `on|off` 兼容；未注册服务时查询返回 `false`。
- `start` 与 `service-start` 的前置互斥检查统一在 Python 层，平台脚本只负责入口适配。
- `service-start` / `service-restart` 以稳定运行作为成功条件，不能只提交异步请求。
- `uninstall` 默认保留用户数据，显式 purge 才删除；卸载从 stable 入口分派，不依赖
  `current` 或活动 release 的完整性。

## 安全与权限

- Linux 以实际 `euid == 0` 拒绝 root；Windows 以管理员令牌拒绝提权终端。
- 只读 `status` / `logs` 和显式 `uninstall` 例外；CI/容器保留
  `CODEX_WECHAT_ALLOW_ROOT=1` 逃生口。
- 用户只通过公开入口卸载；Linux stable `uninstall.py` 和 Windows stable `uninstall.ps1`
  是内部实现，避免出现第二套锁、超时和数据目录语义。
