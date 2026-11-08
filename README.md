<p align="center">
  <img src="assets/branding/codex-wechat-pixel-mascots.png" alt="codex-wechat" width="360">
</p>

<h1 align="center">codex-wechat</h1>

<p align="center">
  <strong>电脑上的 Codex，微信里接着说。</strong>
</p>

<p align="center">
  通过微信继续本机 Codex 的会话、工具、审批和文件。不是另一个云端 Agent，也不复制第二份模型凭据。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-private%20beta-f36d49?style=flat-square" alt="Private beta">
  <img src="https://img.shields.io/badge/python-3.11%2B-3157ff?style=flat-square" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows-72c945?style=flat-square" alt="Linux and Windows">
  <img src="https://img.shields.io/badge/license-Proprietary%20Beta-6d7167?style=flat-square" alt="Proprietary Beta">
</p>

<p align="center">
  <sub>第三方独立项目，与 OpenAI、腾讯及其关联方没有隶属、合作、授权或背书关系。相关名称与商标归各自权利人所有。</sub>
</p>

> **内测中**：当前仓库保持私有，仅供从仓库所有者处取得 fine-grained PAT 的用户测试。
> 下方内测安装命令包含临时 PAT，公开发布前会移除。

## 它解决什么问题

普通微信机器人从零开始理解你、重新准备环境，最后给你一段回答。codex-wechat 连接的是
你电脑上已有的 Codex 会话：同一个 thread、同一份工作目录、同一套工具和权限。你在电脑上
开始任务，临时出门后用手机补充要求、回答问题或查看结果，回来再继续。

Codex 仍然是会话、模型、工具、审批和权限的权威；codex-wechat 只负责把微信变成远程输入口，
并把结果带回正确的位置。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 同一份工作 | 连接本机 Codex 的 thread/turn，不复制上下文、账户或历史 |
| 手机控制面 | 会话切换、新建、模型、强度、目录、压缩、审批、停止和重启 |
| 运行中切换 | `/switch`、`/new` 可 detach 旧回合；`/model`、`/effort`、`/cwd` 从下一回合生效 |
| 微信原生 I/O | 文本、语音转写、图片、视频、文件和引用消息；结果可回传微信 |
| 投递可靠性 | 无消息 ID 即暂停出站并保留队列；`/discard` 主动清空；回复预算可控 |
| 多后端 | 内置 `codex` 与 `deepseek` 后端，同一个 App Server 接口 |
| 跨平台 | Linux systemd 用户服务；Windows Scheduled Task |
| 本地优先 | 文件、工具、审批和模型认证继续由本机 Codex 处理 |

## 适合的场景

- 出门前留下一个长任务，路上继续追问或补充要求。
- 用手机发报错截图、设计稿、文档或素材，让 Codex 直接处理。
- 当前任务还在跑时，把下一件事放进队列，或撤回尚未开始的排队消息。
- 在多个项目或会话之间切换，不必回到电脑前。
- Codex 需要审批、选择方案或回答问题时，直接拿手机处理。
- 生成的图片、视频或文件直接回传微信，不必打开电脑查看。

## 能力对比

> 对比口径：2026-11-09，按当前仓库能力。这里是类别对比，不是“功能总冠军”排名；
> 外部项目会变化，具体能力以对方文档和源码为准。

### 普通微信机器人 vs codex-wechat

| | 普通微信机器人 | codex-wechat |
| --- | --- | --- |
| 上下文 | 每次重新准备环境和背景 | 继续电脑上已有的 Codex 会话 |
| 桌面与手机 | 通常是两段独立对话 | 同一份任务进展与结果 |
| 文件与权限 | 另一套配置 | 工作目录、工具和权限留在本机 |
| 适合 | 问一句、答一句 | 把正在做的事往前推 |

### 结构性定位

| 维度 | codex-wechat | 本地会话桥 | 多渠道 Agent 网关 | ACP/Desktop 自动化 |
| --- | --- | --- | --- | --- |
| 与本机 Codex 会话 | 原生 thread/writer，继续同一会话 | 共享本机 daemon/thread | 通常是平台侧新任务 | 取决于实现 |
| 运行中切换 | `/switch`、`/new` detach；`/model`、`/effort`、`/cwd` 下一回合生效 | 视实现 | 视实现 | 视实现 |
| 审批与提问 | 原生审批/提问转发到微信 | 可能有 | 视实现 | 可能有 |
| 微信原生 I/O | 文本、语音转写、图片、视频、文件、引用 | 视实现 | 多渠道 | 通常无 |
| 部署 | Linux systemd / Windows Task | 本机 | 云或容器 | 本机/桌面 |
| 定位 | 个人 Codex 远程控制面 | 终端/手机同步 | 平台化多 Agent | 通用自动化 |

codex-wechat 只做 Codex + 微信私聊，不承诺企业微信、Telegram、飞书、Discord 或通用多 Agent；
不提供 macOS 版本，也不把 WSL 当成原生平台验收。App IPC 只跟随已经打开的 thread，没有
远程聚焦接口。

## 它怎么接上

```text
微信客户端  <--->  codex-wechat  <--->  Codex App Server
                        |
                        +-- 本机文件、工具、审批、权限
```

- 桥接是 Codex App Server 的 JSON-RPC 客户端，使用原生 thread、turn 和审批接口。
- App 已打开目标会话时，桥接通过本机 IPC follower 跟随，不抢 writer、不关闭 App。
- Linux 使用同 `CODEX_HOME` 的私有 Unix socket；Windows 使用本机命名管道并核对身份。
- 会话切换会请求旧 thread 的 `thread/unsubscribe`，交还 Bridge 持有的写入者。

架构、所有权和恢复边界见 [ARCHITECTURE](docs/ARCHITECTURE.md) 与
[DELIVERY](docs/DELIVERY.md)。

## 开发活动

![codex-wechat 开发活动](assets/dev-activity.svg)

当前快照约 362 次提交、5.1 万行新增、1.3 万行删除（含仓库初始导入）。图表由
[`scripts/dev_chart.py`](scripts/dev_chart.py) 从本仓库 git 历史生成；仓库公开后也可以换成
GitHub 活动图或其他公开统计服务。

## 快速开始

前提：Python 3.11+、Git、Codex、`curl` 和微信客户端。Linux 可选 `qrencode`；没有时会使用
轻量 Python 回退。完整前提和卸载说明见 [INSTALL](docs/INSTALL.md)。

### 内测安装（私有仓库，含临时 PAT）

> 下面是内测用的一行安装命令。真实 PAT 只用于私有仓库，公开发布前会移除并由仓库所有者
> 单独分发。

<details>
<summary>Linux 一行安装</summary>

```bash
env CODEX_WECHAT_TOKEN='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' bash -o pipefail -c 'if [[ -z ${HTTPS_PROXY:-} && -z ${https_proxy:-} ]] && command -v gsettings >/dev/null; then mode=$(gsettings get org.gnome.system.proxy mode 2>/dev/null || true); mode=${mode#?}; mode=${mode%?}; if [[ $mode == manual ]]; then host=$(gsettings get org.gnome.system.proxy.http host 2>/dev/null || true); host=${host#?}; host=${host%?}; port=$(gsettings get org.gnome.system.proxy.http port 2>/dev/null || true); if [[ -n $host && $port =~ ^[0-9]+$ && $port -gt 0 ]]; then export HTTPS_PROXY="http://$host:$port" HTTP_PROXY="http://$host:$port"; fi; fi; fi; curl -fsSL -H "Authorization: Bearer $CODEX_WECHAT_TOKEN" -H "Accept: application/vnd.github.raw+json" "https://api.github.com/repos/zxzxn3/codex-wechat/contents/bootstrap.sh?ref=main" | bash'
```

</details>

<details>
<summary>Windows 一行安装</summary>

```powershell
$env:CODEX_WECHAT_TOKEN='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'; $r = Invoke-WebRequest -UseBasicParsing -Headers @{Authorization="Bearer $env:CODEX_WECHAT_TOKEN"; Accept='application/vnd.github.raw+json'} 'https://api.github.com/repos/zxzxn3/codex-wechat/contents/bootstrap.ps1?ref=main'; $body = $r.Content; if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }; & ([scriptblock]::Create($body))
```

</details>

### 第一次启动

Linux：

```bash
codex-wechat service-start
codex-wechat status
```

Windows：

```powershell
codex-wechat service-start
codex-wechat status
```

未绑定时终端会显示二维码，使用微信扫码并确认授权。微信只处理扫码账号的私聊，群聊不会
响应；发送 `/help` 查看命令。Windows 如果没有生成短入口，使用安装器给出的完整
`codex-wechat.ps1` 路径。

## 安全与信任

- **账户不被复制**：继续使用本机 Codex 的认证和配置，桥接不保存第二份模型凭据。
- **工作留在本机**：文件、工具、项目目录和权限仍由本机 Codex 处理。
- **不做假成功**：消息没有明确回执时标记“投递未知”，暂停出站，不悄悄重复执行。
- **只开放私人入口**：默认只处理绑定账号的私聊，不响应群聊，不自动变成公共机器人。
- **审批仍然有效**：原生审批和提问只在当前连接实际收到请求时回应，桌面已处理的请求不会重放。

## 平台与边界

| | Linux | Windows |
| --- | --- | --- |
| 状态 | 当前树有完整离线回归 | 当前版本仍需实机复测 |
| 后台 | systemd 用户服务 | Scheduled Task + supervisor |
| 服务日志 | `journalctl --user -u codex-wechat` | `logs/service.log`，按 5 MiB 轮转 |
| App IPC | 私有 Unix socket | 本机命名管道，只跟随主 `CODEX_HOME` |
| 自启 | systemd enable/disable | 计划任务登录触发器 |

当前明确不提供：macOS 版本、群聊、企业微信/Telegram/飞书/Discord、多租户客服、通用多 Agent
平台、App 反向聚焦未打开会话。App IPC 依赖 Codex App 版本，属于实验性适配面。

## FAQ

**会另开一个 Codex，消耗另一份上下文吗？**

不会按这个目标设计。它尝试接入本机已有 Codex 会话；App 已打开时会跟随当前会话，而不是复制
聊天记录。

**手机和电脑可以同时看同一个任务吗？**

可以围绕同一会话工作。桌面和微信看到的进展、结果和工作目录来自同一个 Codex 任务。

**需要把 Codex 密码或 API Key 给微信吗？**

不需要。认证继续由本机 Codex 管理，codex-wechat 不另存一份模型凭据。

**电脑关机后还能用吗？**

不能。电脑需要开机并保持后台服务运行，因为真正执行任务的是你的电脑。

**支持 Codex App 和命令行吗？**

支持围绕原生 Codex 会话工作。App 已打开目标会话时可跟随；仅使用 CLI 也能共享本机 Codex
daemon。

**会不会重复发送，或者告诉我“已完成”但其实没收到？**

系统把发送确认和投递未知分开处理。没有明确回执时不会把它伪装成成功。

**适合当团队客服机器人吗？**

不适合。它是给个人在微信里继续本机 Codex 工作用的，不是多渠道客服或通用 Agent 平台。

## 文档与开发

README 用于项目介绍；agent、维护者和测试者从 [文档索引](docs/INDEX.md) 开始。

- 安装、更新、卸载：[INSTALL](docs/INSTALL.md)
- 微信命令和短别名：[COMMANDS](docs/COMMANDS.md)、[生成参考](docs/REFERENCE.generated.md)
- 架构、配置、交付、诊断：[ARCHITECTURE](docs/ARCHITECTURE.md)、[CONFIGURATION](docs/CONFIGURATION.md)、[DELIVERY](docs/DELIVERY.md)、[DIAGNOSTICS](docs/DIAGNOSTICS.md)
- 支持面与验证证据：[SUPPORT-MATRIX](docs/SUPPORT-MATRIX.md)、[VALIDATION](docs/VALIDATION.md)
- 决策记录：[决策索引](docs/decisions/README.md)
- 开发检查：`scripts/check.sh`；测试与静态检查约定见 [DEVELOPMENT](docs/DEVELOPMENT.md) 和 [AGENTS](AGENTS.md)

## License

本项目采用 [Proprietary Beta License](LICENSE)，不是开源软件。仅授权仓库所有者直接许可的
个人在自控设备上进行非商业评估和测试；不得转售、再分发、商用或创建衍生作品。正式发布前
请以 `LICENSE` 原文为准。

“Codex”“OpenAI”“微信”“WeChat”“Tencent”等名称与商标归各自权利人所有。本项目是第三方
独立开发，与 OpenAI、腾讯及其关联方没有隶属、合作、授权或背书关系。
