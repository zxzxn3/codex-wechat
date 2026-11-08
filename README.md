<p align="center">
  <img src="assets/branding/codex-wechat-pixel-mascots.png" alt="codex-wechat" width="360">
</p>

<h1 align="center">codex-wechat</h1>

<p align="center">
  <strong>电脑上的 Codex，微信里接着说。</strong>
</p>

<p align="center">
  <strong>继续同一份本机 Codex 工作</strong>：离开电脑后，仍然可以
  <strong>补充要求</strong>、<strong>处理审批</strong>、<strong>取回结果和文件</strong>。
  不另起云端 Agent，也不复制第二份模型凭据。
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

<p align="center">
  <img src="assets/code-churn-12h-split.svg" alt="每 12 小时代码增加与删除" width="720">
  <br>
  <sub>每 12 小时代码变更；绿色为新增，红色为删除，右下角为源码/测试行数与 Git 工时估算。</sub>
</p>

## 它解决什么问题

你正在电脑上用 Codex 做一件事：读代码、改文件、跑测试，或者等一个较长的任务。临时离开
电脑后，任务还在本机继续，但你不方便一直守着屏幕。

codex-wechat 把微信变成这台电脑上 Codex 的远程入口。你在微信里发消息、图片或文件，内容
进入同一个 Codex 会话；需要审批、选择或补充信息时，可以直接回复；结果和生成的文件再回到
微信。电脑仍然是执行任务的地方，Codex 仍然掌握会话、模型、工具和权限。

它适合“任务已经在电脑上开始，但人暂时不在电脑前”的情况。它不是另起一个云端账号，也不是
把 Codex 搬到微信里重新开一份对话。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 同一份工作 | 继续同一个 Codex 会话，不复制上下文、账户或历史 |
| 手机控制面 | 会话切换、新建、模型、强度、目录、压缩、审批、停止和重启 |
| 运行中切换 | `/switch`、`/new` 可切换会话，旧回合留在后台但不再显示；`/model`、`/effort`、`/cwd` 从下一回合生效 |
| 微信原生 I/O | 文本、语音转写、图片、视频、文件和引用消息；结果可回传微信 |
| 投递可靠性 | 无法确认送达时暂停发送并保留内容，不假报成功；`/discard` 可清空待发消息 |
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

## 和直接在电脑上使用有什么不同

> 对比口径：2026-11-09，按当前仓库能力。这里是类别对比，不是“功能总冠军”排名；
> 外部项目会变化，具体能力以对方文档和源码为准。

| | 只在电脑上使用 Codex | codex-wechat |
| --- | --- | --- |
| 入口 | 桌面 App 或终端 | 微信私聊，电脑继续执行 |
| 离开电脑后 | 只能等回来再查看 | 可以补充要求、处理审批、查看结果 |
| 上下文 | 当前会话 | 继续同一个会话，不另起一份 |
| 文件 | 在电脑上打开或发送 | 手机发送，结果可以回传微信 |
| 适合 | 全程在电脑前 | 任务开始后需要临时离开 |

## 与其它项目的能力矩阵

> 2026-11-09 按公开源码口径。`●` 直接实现，`◐` 部分或间接，空白表示未实现或当前未确认。
> 只比较与本项目核心路线相关的能力，不是功能总分，也不代表各平台均已实机验收。

| 能力 | codex-wechat | [CLI-WeChat-Bridge](https://github.com/UNLINEARITY/CLI-WeChat-Bridge) | [CodexBridge](https://github.com/Gan-Xing/CodexBridge) | [Kites](https://github.com/kites262/codex-wechat) |
| --- | :---: | :---: | :---: | :---: |
| 接管已有 Codex CLI 会话 | ● | ● |  | ● |
| 跟随 Codex App 活会话 | ● |  |  | ◐ |
| 写者所有权与会话来源隔离 | ● | ◐ | ◐ | ◐ |
| 会话、模型、目录与压缩控制 | ● | ● | ● | ◐ |
| 运行中切换会话与隔离旧回合 | ● |  |  |  |
| 忙时 steer / queue / unqueue | ● | ◐ |  |  |
| 原生审批与提问 | ● | ● | ● | ● |
| 入站微信媒体 | ● | ◐ | ● |  |
| 出站媒体与结果文件回传 | ● | ● | ● |  |
| 持久输入 / 输出队列 | ● | ◐ | ◐ | ● |
| 投递确认与静默丢包隔离 | ● | ◐ | ◐ |  |
| 热配置、提示与智能切片 | ● | ◐ | ◐ |  |
| 多后端 / 多 CLI | ◐ | ● | ◐ |  |
| 多渠道入口（企业微信 / Telegram 等） |  | ● |  |  |
| Web 控制台 / Mission Control |  |  | ● |  |
| 平台支持 | <kbd>Linux</kbd> <kbd>Windows</kbd> | <kbd>Linux</kbd> <kbd>macOS</kbd> <kbd>Windows</kbd> | <kbd>Linux</kbd> <kbd>macOS</kbd> <kbd>Windows</kbd> | <kbd>Linux</kbd> |

其它路线的强项也很明确：CLI-WeChat-Bridge 覆盖多 IM，CodexBridge 提供更宽的控制台与任务
编排，Kites 保持更小的核心实现。外部项目会变化，具体能力以对方当前文档和源码为准。平台行
只列各项目当前公开文档明确支持的系统。

<details>
<summary>其它相邻项目</summary>

- **WeChat ACP**：通用 ACP 接入方向。
- **weixin-codex-bridge**：桌面自动化与桥接方向。
- **Claude-to-IM Skill**：多 IM skill 方向。
- **WeClaw**：应用侧方向；源码未公开，不纳入逐项符号比较。

</details>

### 它不是哪一类工具

- 不是云端 Agent：模型认证、文件、工具和任务执行仍然在本机 Codex。
- 不是通用多平台网关：只做 Codex + 微信私聊，不承诺企业微信、Telegram、飞书或 Discord。
- 不是团队客服系统：面向个人继续自己的工作，不做多租户或多人协作调度。
- 不是桌面自动化：通过 Codex 的原生会话和审批接口工作，不模拟键鼠操作。

codex-wechat 不提供 macOS 版本，也不把 WSL 当成原生平台验收。App IPC 只跟随已经打开的
thread，没有远程聚焦接口。

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

## 安全与信任

- **账户不被复制**：继续使用本机 Codex 的认证和配置，桥接不保存第二份模型凭据。
- **工作留在本机**：文件、工具、项目目录和权限仍由本机 Codex 处理。
- **不做假成功**：消息没有明确回执时标记“投递未知”，暂停出站，不悄悄重复执行。
- **只开放私人入口**：默认只处理绑定账号的私聊，不响应群聊，不自动变成公共入口。
- **审批仍然有效**：原生审批和提问只在当前连接实际收到请求时回应，桌面已处理的请求不会重放。

## 平台与边界

| | Linux | Windows |
| --- | --- | --- |
| 状态 | 当前树有完整离线回归 | 当前版本已实机复测 |
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

**适合团队客服使用吗？**

不适合。它是给个人在微信里继续本机 Codex 工作用的，不是多渠道客服或通用 Agent 平台。

## 文档与开发

README 用于项目介绍；agent、维护者和测试者从 [文档索引](docs/INDEX.md) 开始。

- 安装、更新、卸载：[INSTALL](docs/INSTALL.md)
- 用户提示与文案边界：[USER-MESSAGES](docs/USER-MESSAGES.md)
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
