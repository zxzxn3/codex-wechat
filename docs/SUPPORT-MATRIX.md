# 支持矩阵

本文定义当前产品支持面；[VALIDATION](VALIDATION.md) 记录证据和缺口。未列为“支持”的
组合不是承诺行为，应在安装或启动时 fail closed，而不是静默回退。

| 范围 | Linux 状态 | Windows 状态 | 当前证据/边界 |
|---|---|---|---|
| 首次安装、版本更新、卸载 | 支持 | 待验证 | Linux 当前树有安装/入口/卸载离线测试；Windows 当前提交仍需复测 |
| 前台 `start` | 支持 | 待验证 | 状态目录锁、前台日志和退出路径有离线测试；Windows 当前提交待复测 |
| 后台服务 | 支持 | 待验证 | Linux 有 systemd 用户服务契约测试；Windows 任务时序仍需实机验证 |
| `autostart` | 支持 | 待验证 | 只切换登录触发器；Windows 当前任务 action 已改为只传安装根 |
| 自定义数据根 | 待验证 | 待验证 | 新增 fail-closed 冲突测试；Linux systemd 和 Windows 服务的端到端行为待实机复测 |
| 短命令入口 | 支持 | 待验证 | Windows `.cmd` 在非 ASCII 用户目录下仍待实机验证 |
| 共享 daemon | 支持 | 待验证 | Linux 状态/回落逻辑有离线测试；Windows App 并发仍待复测 |
| App IPC follower | 实验性 | 实验性 | 依赖 App 版本；Windows 命名管道和三端并发仍待实机复测 |
| 私人仓库安装 | 实验性 | 实验性 | 网络入口和认证路径有离线契约测试；真实 PAT 安装未在独立审查中复现 |

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
- 服务状态按 `queued/activating → running → deactivating` 处理；过渡态必须经过稳定窗口，
  已处于 `running/active` 的服务可以直接复用。
- 版本化稳定入口只执行 `current` 指向的完整 release；损坏的指针必须给出修复指引，不能猜测
  其他版本。

版本基线：Python 3.11+；Windows PowerShell 5.1+；当前验证的 Codex 版本以
[SOURCES](SOURCES.md) 和 [VALIDATION](VALIDATION.md) 为准。未列出的 Codex/App 版本按
“待验证”处理，不应从旧版本的实机结果推断兼容。
