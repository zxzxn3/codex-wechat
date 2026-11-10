<p align="center">
  <img src="assets/branding/codex-wechat-pixel-mascots.png" alt="codex-wechat" width="360">
  <br>
  <sub><a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">CC BY-NC-SA 4.0</a>
  <a href="assets/branding/README.md">素材说明</a></sub>
</p>

<p align="center">
  <img src="assets/branding/codex-wechat-wechat-contact.jpg" alt="codex-wechat 微信联系人" width="640">
</p>

<h1 align="center">codex-wechat</h1>

<p align="center">
  <strong>“这样你就有更多时间来烧你的人民币、你的重置卡，和你的青春了”</strong>
</p>

<p align="center">
  <sub>注：DeepSeek 需要额外方法接入 Codex，Linux 用户可见
  <a href="https://github.com/zxzxn3/codex-deepseek">codex-deepseek</a></sub>
</p>

<p align="center">
  <strong>在微信中继续 Codex 的工作</strong>：离开电脑后，仍然可以
  <strong>与 Agent 交流</strong>、<strong>处理审批</strong>、<strong>取回结果和文件</strong>。
  不使用云端 Agent，也不需要第二份模型凭据。安装后<strong>扫码即用</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-3157ff?style=flat-square" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows-72c945?style=flat-square" alt="Linux and Windows">
  <img src="https://img.shields.io/badge/license-Apache--2.0-3157ff?style=flat-square" alt="Apache-2.0">
</p>

<p align="center">
  <img src="assets/code-churn-12h-split.svg" alt="每 12 小时代码增加与删除" width="720">
</p>

<p align="center">
  <sub>codex-wechat 是第三方独立项目，与 OpenAI、DeepSeek、腾讯及其关联方没有隶属、合作、授权或背书关系。相关名称与商标归各自权利人所有。</sub>
</p>

## 快速开始

前提：Python 3.11+、Git、Codex、`curl`。完整前提和卸载说明见 [INSTALL](docs/INSTALL.md)。

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/zxzxn3/codex-wechat/main/bootstrap.sh | bash
```

Windows:

```powershell
irm https://raw.githubusercontent.com/zxzxn3/codex-wechat/main/bootstrap.ps1 | iex
```

### 第一次启动

Linux/Windows:

```sh
codex-wechat service-start
```

未绑定时终端会显示二维码，使用微信扫码并确认授权。
发送 `/help` 查看命令。Windows 如果没有生成短入口，使用安装器给出的完整
`codex-wechat.ps1` 路径。连接上 Codex 后，也可以直接让 Agent 教你使用（通过原生技能）。

## 它解决什么问题

你正在电脑上用 Codex 做一件事：读代码、改文件、跑测试，或者等一个较长的任务。临时离开
电脑后，任务还在本机继续，但你不方便一直守着屏幕。

codex-wechat 把微信变成这台电脑上 Codex 的远程入口。你在微信里发消息、图片或文件，内容
进入同一个 Codex 会话；需要审批、选择或补充信息时，可以直接回复；结果和生成的文件再回到
微信。电脑仍然是执行任务的地方，Codex 仍然掌握会话、模型、工具和权限。

它适合“任务已经在电脑上开始，但人暂时不在电脑前”的情况。它不是另起一个云端账号，也不是
把 Codex 搬到微信里重新开一份对话。

## 为什么不让你的 AI 从零写一个

**人类开发者**：因为你没有一星期来开发它，也不想花时间维护它。你可以让你的 Agent 帮你用 5 分钟做出一个，而且四成真的可以连上（而且还有考虑它可能参考了谁谁谁的开源项目），但后续不断的试错、设计调整、新功能，会让你的项目越来越臃肿，而且重新发明轮子会让你感到自己多多少少有点自虐倾向。

这个项目已经帮你投入了足够多。按 Git 历史估算，这个项目已经投入了约 77 个有效开发小时、400 多次提交、约 850 条包含明确取舍、修改或否决的实质发言、数百个独立判断，以及 API 服务费用，这还不包括在 Windows 和微信上排查的时间。

这个项目除用户提示外，绝大部分代码都是 AI 编写的。但 AI 仅仅只负责将人类想法用代码完成。在合适的时机和范围重构、知道什么架构需要调整、知道何时该审查何时该收敛、知道用户体验应该如何设计、知道何时该信任 AI，何时该坚持己见，这一些还是由有经验的开发者掌握的。AI 可以让第一版很便宜，但这只是价值链的重新调整，而不是消失。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 同一个会话 | 继续同一个 Codex 会话，不复制上下文、账户或历史 |
| 手机控制面 | 会话切换、新建、模型、强度、目录、压缩、审批、停止和重启 |
| 运行中切换 | `/switch`、`/new` 可切换会话，旧回合留在后台但不再显示；`/model`、`/effort`、`/cwd` 从下一回合生效 |
| 微信原生 I/O | 文本、语音转写、图片、视频、文件和引用消息；结果可回传微信 |
| 投递可靠性 | 无法确认送达时暂停发送并保留内容，不假报成功；`/discard` 可清空待发消息 |
| 自定义多后端 | 内置 `codex` 与 `deepseek` 后端（`deepseek` 仅供 Linux，见[codex-deepseek](https://github.com/zxzxn3/codex-deepseek)），同一个 App Server 接口 |
| 跨平台 | Linux systemd 用户服务；Windows Scheduled Task |
| 本地优先 | 文件、工具、审批和模型认证继续由本机 Codex 处理 |

## 适合的场景

- 出门前留下一个长任务，路上继续追问或补充要求。
- 用手机发报错截图、设计稿、文档或素材，让 Codex 直接处理。
- 当前任务还在跑时，把下一件事放进队列，或撤回尚未开始的排队消息。
- 在多个项目或会话之间切换，不必回到电脑前。
- Codex 需要审批、选择方案或回答问题时，直接拿手机处理。
- 生成的图片、视频或文件直接回传微信，不必打开电脑查看。
- 躺在床上，不想起身去电脑前。（我开发的主要原因之一）

## 与其它项目的能力矩阵

> 2026-11-10 按公开 README 与源码口径。`●` 直接实现，`◐` 部分或间接，空白表示未实现或当前未确认。
> 只比较与本项目核心路线相关的能力，不是功能总分，也不代表各平台均已实机验收。

| 能力 | codex-wechat | [CLI-WeChat-Bridge](https://github.com/UNLINEARITY/CLI-WeChat-Bridge) | [CodexBridge](https://github.com/Gan-Xing/CodexBridge) | [Kites](https://github.com/kites262/codex-wechat) |
| --- | :---: | :---: | :---: | :---: |
| 接管已有 Codex CLI 会话 | ● | ● | ● | ● |
| 跟随 Codex App 活会话 | ● |  |  | ◐ |
| 写者所有权与会话来源隔离 | ● | ● | ◐ | ● |
| 会话、模型、目录与压缩控制 | ● | ◐ | ◐ | ◐ |
| 运行中切换会话与隔离旧回合 | ● |  |  | ● |
| 忙时 steer / queue / unqueue | ● | ◐ |  |  |
| 原生审批与提问 | ● | ● | ● | ● |
| 入站微信媒体 | ● | ● | ● |  |
| 出站媒体与结果文件回传 | ● | ● |  |  |
| 持久输入 / 输出队列 | ● | ◐ | ◐ | ● |
| 投递确认与静默丢包隔离 | ● | ◐ | ◐ | ◐ |
| 热配置、提示与智能切片 | ● | ◐ | ◐ |  |
| 多 Codex 后端 / 实例 | ● | ● | ◐ | ◐ |
| 异构 CLI（Claude Code / OpenCode / Pi） |  | ● |  |  |
| 多渠道入口（企业微信 / Telegram 等） |  | ● |  |  |
| Web 控制台 / Mission Control |  |  | ◐ |  |
| 平台支持 | <kbd>Linux</kbd> <kbd>Windows</kbd> | <kbd>Linux</kbd> <kbd>macOS</kbd> <kbd>Windows</kbd> | <kbd>Linux</kbd> <kbd>macOS</kbd> <kbd>Windows</kbd> | <kbd>Linux</kbd> |

其它路线的强项也很明确：CLI-WeChat-Bridge 覆盖多 IM、多 CLI 和双向文件；CodexBridge 在
线程管理、自动化和个人记录上更宽；Kites 以单个 Rust 二进制实现原生会话、运行中切换和持久
交付。外部项目会变化，具体能力以对方当前文档和源码为准。平台行只列各项目当前公开文档明确
支持的系统。

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

架构、所有权和恢复边界见 [ARCHITECTURE](docs/ARCHITECTURE.md)。

## 安全与信任

- **账户不被复制**：继续使用本机 Codex 的认证和配置，桥接不保存第二份模型凭据。
- **工作留在本机**：文件、工具、项目目录和权限仍由本机 Codex 处理。
- **不做假成功**：消息没有明确回执时标记“投递未知”，暂停出站，不悄悄重复执行。
- **只开放私人入口**：默认只处理绑定账号的私聊，不响应群聊，不自动变成公共入口。
- **审批仍然有效**：原生审批和提问只在当前连接实际收到请求时回应，桌面已处理的请求不会重放。

## 平台与边界

| | Linux | Windows |
| --- | --- | --- |
| 状态 | 当前树有完整离线回归 | 已通过历史实机验证，当前提交待复测 |
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

**需要把 Codex 密码或 API Key 给微信桥吗？**

不需要。认证继续由本机 Codex 管理，codex-wechat 不另存一份模型凭据。

**电脑关机后还能用吗？**

不能。电脑需要开机并保持后台服务运行，因为真正执行任务的是你的电脑。

**支持 Codex App 和命令行吗？**

支持围绕原生 Codex 会话工作。App 已打开目标会话时可跟随；仅使用 CLI 也能共享本机 Codex
daemon。

**安装后可以用 `cmd.exe` 启动吗？**

可以。安装后直接在 `cmd.exe` 运行：

```cmd
codex-wechat service-start
```

**PowerShell 提示“在此系统上禁止运行脚本”怎么办？**

这是 PowerShell 执行策略拦截了 `.ps1`。运行 `bootstrap.ps1` 时遇到该错误，可以只为这次
执行临时绕过策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

如果已经安装但没有生成 `codex-wechat.cmd`，用同样方式运行稳定入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-start
```

**回复太多会被微信限流吗？**

当桥接短时间向微信发送大量回复时，微信可能静默丢弃下行消息：接口可能返回成功但没有消息
ID，接收端什么也看不到。codex-wechat 会限制每条入站消息触发的出站回复数量，并在连续回复
之间留出间隔；首次发现无消息 ID 时会暂停出站并保留待发内容。当前数值见
[生成参考](docs/REFERENCE.generated.md#出站保护)。这是本地保护机制，不是微信对限流阈值的
承诺。

**会不会重复发送，或者告诉我“已完成”但其实没收到？**

系统把发送确认和投递未知分开处理。没有明确回执时不会把它伪装成成功。

**适合团队客服使用吗？**

不适合。它是给个人在微信里继续本机 Codex 工作用的，不是多渠道客服或通用 Agent 平台。

## 文档与开发

README 用于项目介绍；agent、维护者和测试者从 [文档索引](docs/INDEX.md) 开始。

- 安装、更新、卸载：[INSTALL](docs/INSTALL.md)
- 微信命令和短别名：[COMMANDS](docs/COMMANDS.md)、[生成参考](docs/REFERENCE.generated.md)
- 架构、配置、交付、诊断和用户文案：[ARCHITECTURE](docs/ARCHITECTURE.md)
- 平台操作与支持面：[PLATFORMS](docs/PLATFORMS.md)
- 验证证据：[VALIDATION](docs/VALIDATION.md)
- 开发检查：`scripts/check.sh`；测试与静态检查约定见 [DEVELOPMENT](docs/DEVELOPMENT.md) 和 [AGENTS](AGENTS.md)

## License

项目源代码和文档采用 [Apache License 2.0](LICENSE)。归属声明见 [NOTICE](NOTICE)，项目名称
和品牌的商标边界见 [TRADEMARKS](TRADEMARKS.md)。`assets/branding/` 中的 mascot 是基于
社区二创角色的改编，按 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
单独提供，不适用 Apache-2.0；署名、来源和修改记录见
[品牌素材说明](assets/branding/README.md)。

品牌形象由 GPT 基于 Bilibili 来源图生成；原角色上善无形、二创 ZipZipPipe，相关素材按
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 使用。

“Codex”“OpenAI”“DeepSeek”“微信”“WeChat”“Tencent”等名称与商标归各自权利人所有。
本项目是第三方独立开发，与 OpenAI、DeepSeek、腾讯及其关联方没有隶属、合作、授权或背书关系。
