# 支持矩阵

本文定义当前产品支持面；[VALIDATION](VALIDATION.md) 记录证据和缺口。未列为“支持”的
组合不是承诺行为，应在安装或启动时 fail closed，而不是静默回退。

| 范围 | Linux | Windows | 说明 |
|---|---|---|---|
| 首次安装、版本更新、卸载 | 支持 | 支持 | 使用稳定入口或网络入口；内部 helper 不是公开命令 |
| 前台 `start` | 支持 | 支持 | 持有状态目录锁，`Ctrl+C` 结束 |
| 后台服务 | 支持（systemd 用户服务） | 支持（Scheduled Task + PowerShell 5.1） | 未注册时 `service-start` 应明确失败 |
| `autostart` | 支持 | 支持 | 只切换登录触发器，不负责前台转后台 |
| 自定义数据根 | 支持 | 支持 | 安装器写入 `.data-root`；环境变量与标记不一致时 fail closed |
| 短命令入口 | `~/.local/bin/codex-wechat` | `.cmd`；仅当目标目录存在且在 PATH | 否则使用稳定 `.ps1` 入口 |
| 共享 daemon | 支持，默认开启 | 支持，默认关闭 | Windows 以 App 为主时保持关闭 |
| App IPC follower | 部分支持 | 部分支持 | 依赖 App 版本；Windows 命名管道和三端并发仍待实机复测 |
| 私人仓库安装 | 支持 | 支持 | fine-grained PAT，仅需 `Contents: Read-only` |

## 明确不支持或仅实验性

- root/sudo 或管理员终端运行写命令默认拒绝；只读 `status` / `logs` 和显式 `uninstall`
  例外，容器/CI 才使用 `CODEX_WECHAT_ALLOW_ROOT=1`。
- 直接执行 `uninstall.py`、`uninstall.ps1`、`windows/task-service.ps1` 等内部 helper 不属于
  用户命令契约；应通过稳定入口调用。
- Windows 空 App 发现、三端并发、`service.log` 连续轮转、非 ASCII 用户目录下的 `.cmd`、
  Linux systemd 下的 GNOME 代理探测尚未完成或未复测，不能写成全平台已验证。
- CDN 上传超时重试幂等性未证实；相关行为只在明确标注的范围内使用。
- WSL、群聊、App 主动反向接入和未经 Codex/微信支持的内部接口不在此矩阵承诺内。

## 数据根与服务的硬约束

- `CODEX_WECHAT_DATA_ROOT` 与安装根 `.data-root` 必须一致；安装器是更新标记的唯一入口。
- 服务状态按 `queued/activating → running → deactivating` 处理；只有稳定运行才算启动成功。
- 版本化稳定入口只执行 `current` 指向的完整 release；损坏的指针必须给出修复指引，不能猜测
  其他版本。
