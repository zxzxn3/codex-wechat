# 验证状态

更新：2026-11-05。文档、源码核对和 Linux mock 不能替代真实 Windows、微信、CDN 或模型验收。
本页只记录当前状态和缺口；详细历史日志见 git 提交历史。

| 范围 | 当前证据 | 状态 |
|---|---|---|
| Linux 离线单测 | `python -m unittest discover -s tests`：787 通过、20 跳过、0 失败 | 已验证 |
| 静态检查 | ruff、shellcheck、`py_compile`、pyright（0 errors） | 已验证 |
| Windows 服务基础生命周期 | PowerShell 5.1、Scheduled Task、Job Object 清理、单实例、通知 | 已实机验证（历史窗口） |
| Windows App IPC | writer 冲突连接、身份核对、无 App / App 已持有 / App 后启动三种命名管道状态 | 已验证；仍受 App 版本影响 |
| Windows 真实微信 | 文本、图片、文件、语音、引用、原生提问、审批 | 已实机验证（历史窗口） |
| Windows 新命令入口 | `codex-wechat.cmd`、`start` / `service-start` / `status` 等完整闭环 | 仍需按当前 release 复测 |
| Windows 卸载/重启 | 稳定入口、`-PurgeData`、服务重启、连接池退役 | 仍需按当前 release 复测 |
| App / CLI / 微信三端并发 | 部分单端和多客户端测试 | 未覆盖完整三端并发 |
| CDN 上传重试幂等 | 超时重试行为已知；服务端幂等未证实 | 仅待运行/协议验证 |
| WMI 凭据路径 | 替身进程和直接 helper 已验证；真实 daemon + 真实系统代理组合未闭合 | 仅待实机验证 |

## 已知缺口

- Windows 命名管道存在性、App 版本更新后的 IPC 兼容和三端并发需要在目标机器复测；
- CDN 上传 POST 在没有服务端幂等证据前，不能把自动重试写成已验收行为；
- WMI 真实 daemon、计划任务上下文和带凭据系统代理三者同时成立的条件尚未完整复现；
- 共享后台回落路径的提示和日志有离线覆盖，但真实失败条件可能需要破坏 Codex 安装才能稳定制造。

提交或发布前，至少更新本表的“当前证据”列；不要把一次成功样本扩大成所有平台组合通过。
