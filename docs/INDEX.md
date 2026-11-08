# 文档索引

README 用于项目介绍和公开推广；本文是 agent、维护者和测试者的文档入口。代码是实现事实的
来源；文档解释当前契约、操作方式和已验证边界。先按任务定位文档，再只读相关章节。

## 按任务查找

| 要查什么 | 先读 |
| --- | --- |
| 模块职责、后台/App/CLI 所有权、认证与重绑边界 | [ARCHITECTURE](ARCHITECTURE.md) |
| 稳定术语、命名约定和易混概念 | [CONTEXT](CONTEXT.md) |
| 用户可见文案、隐式约定和提示边界 | [USER-MESSAGES](USER-MESSAGES.md) |
| 公开命令、参数、平台入口和微信短别名 | [COMMANDS](COMMANDS.md) |
| 由代码生成的命令别名、完整命令和数值设置范围 | [REFERENCE.generated](REFERENCE.generated.md) |
| 安装、更新、卸载、目录和数据根 | [INSTALL](INSTALL.md) |
| 热配置、提示模板、资源清理和后台回落 | [CONFIGURATION](CONFIGURATION.md) |
| 输入提交、会话切换、outbox、发送确认、额度和恢复 | [DELIVERY](DELIVERY.md) |
| 日志字段、脱敏、微信轮询、连接池和网络计时 | [DIAGNOSTICS](DIAGNOSTICS.md) |
| 当前支持面、能力边界和实验性项 | [SUPPORT-MATRIX](SUPPORT-MATRIX.md) |
| 实机覆盖、历史局限和剩余缺口 | [VALIDATION](VALIDATION.md) |
| Linux/Windows 行为差异、二维码和桌面通知 | [PLATFORM-DIFFERENCES](PLATFORM-DIFFERENCES.md) |
| Windows 操作、IPC、共享 daemon 和故障排查 | [WINDOWS](WINDOWS.md) |
| Codex/App/微信协议、版本和外部依据 | [SOURCES](SOURCES.md) |
| 测试隔离、静态检查、依赖和发布退出标准 | [DEVELOPMENT](DEVELOPMENT.md)、[AGENTS](../AGENTS.md) |
| 分发方式、私有入口和内测 PAT 边界 | [DISTRIBUTION](DISTRIBUTION.md) |
| 性能采样和暂不实施的恢复增强 | [PERFORMANCE](PERFORMANCE.md) |
| 维护成本与重构取舍 | [COMPLEXITY](COMPLEXITY.md) |
| 已作出的命令、投递和权限决策 | [决策索引](decisions/README.md) |

## 推荐阅读顺序

- 第一次接手：README → 本文 → [ARCHITECTURE](ARCHITECTURE.md) → [DELIVERY](DELIVERY.md)。
- 修改命令或微信交互：本文 → [COMMANDS](COMMANDS.md) → [REFERENCE.generated](REFERENCE.generated.md) → 对应测试。
- 修改安装、服务或平台入口：本文 → [INSTALL](INSTALL.md) → [PLATFORM-DIFFERENCES](PLATFORM-DIFFERENCES.md)。
- 修改配置、日志或恢复边界：本文 → [CONFIGURATION](CONFIGURATION.md) → [DIAGNOSTICS](DIAGNOSTICS.md)。
- 判断一项能力是否可发布：本文 → [SUPPORT-MATRIX](SUPPORT-MATRIX.md) → [VALIDATION](VALIDATION.md)。

## 文档原则

- 当前行为写进契约文档；历史证据和未验证项写进 `VALIDATION`，不混写成“已通过”。
- 命令、别名和数值设置由 `scripts/reference_docs.py` 生成，不手工维护第二份表。
- 文档之间用链接复用，不复制同一张能力表；改名或移动文件时同时更新 `scripts/check.sh` 的检查。
- 面向用户的推广内容放在 README；面向 agent 的入口放在本文。
