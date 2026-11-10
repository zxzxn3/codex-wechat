# 文档索引

代码是实现事实的唯一来源。文档只解释用户操作、跨模块不变量、外部依据和验证证据；
命令、别名、默认值和范围由生成参考维护，不在别处复制。

## 文档

| 文档 | 用途 |
| --- | --- |
| [README](../README.md) | 项目介绍、快速开始和用户 FAQ |
| [INSTALL](INSTALL.md) | 安装、更新、卸载、目录和数据根 |
| [COMMANDS](COMMANDS.md) | 公开入口、生命周期和命令语义 |
| [REFERENCE.generated](REFERENCE.generated.md) | 从代码生成的命令、设置默认值、范围和出站保护参数 |
| [ARCHITECTURE](ARCHITECTURE.md) | 术语、模块职责、所有权、交付、配置、诊断和文案不变量 |
| [PLATFORMS](PLATFORMS.md) | Linux/Windows 操作、平台差异、支持面和故障排查 |
| [DEVELOPMENT](DEVELOPMENT.md) | 测试、静态检查、依赖、变更检查和发布门 |
| [VALIDATION](VALIDATION.md) | 离线、实机和外部协议的验证状态与缺口 |
| [SOURCES](SOURCES.md) | Codex、App、微信和外部协议的版本依据 |
| [决策记录](decisions/README.md) | 入口、命令、投递和安全取舍 |
| [事件档案](incidents/2026-11-06-wechat-silent-drop.md) | 微信下行静默丢弃的冻结时间线和样本 |

## 按任务查找

- 第一次接手：README → 本文 → ARCHITECTURE → DEVELOPMENT。
- 修改命令或微信交互：COMMANDS → REFERENCE.generated → ARCHITECTURE → 对应测试。
- 修改安装或平台入口：INSTALL → PLATFORMS → COMMANDS。
- 修改配置、交付或诊断：ARCHITECTURE → 对应源码 → 测试。
- 判断能力是否可发布：PLATFORMS → VALIDATION → SOURCES。

## 文档原则

- 代码能表达的机械事实不复制到文档；使用源码链接或生成参考。
- 文档保留代码本身难以表达的“为什么”、跨模块不变量、用户操作和外部依据。
- 当前行为、历史证据和未验证项分开记录，不把一次样本写成全平台承诺。
- 修改生成器后运行 `scripts/reference_docs.py --check`；`scripts/check.sh` 会检查漂移。
