# codex-wechat

通过微信使用本机 Codex，会话、模型、工具和认证沿用 Codex。Linux 和 Windows 共用程序，
后台服务分别使用 systemd 用户服务与 Windows Scheduled Task。普通 Codex、App 内置 CLI 和
自定义启动入口使用相同的 App Server 接口；DeepSeek 是可选后端，不是安装前提。

## 快速开始

仓库保持私有。创建一个只能读取 `zxzxn3/codex-wechat` 的 fine-grained PAT，Repository
permissions 仅授予 `Contents: Read-only`，然后运行下面的命令；PAT 从终端交互输入。
入口通过 GitHub Contents API 取得 bootstrap，再由 bootstrap 认证克隆到临时目录并安装。

Linux：

```bash
read -rsp 'GitHub PAT: ' CODEX_WECHAT_TOKEN; echo
export CODEX_WECHAT_TOKEN
curl -fsSL --proto "=https" --tlsv1.2 \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "$CODEX_WECHAT_TOKEN") \
  -H "Accept: application/vnd.github.raw+json" \
  "https://api.github.com/repos/zxzxn3/codex-wechat/contents/bootstrap.sh?ref=main" | bash
unset CODEX_WECHAT_TOKEN
```

Windows PowerShell：

```powershell
$secure = Read-Host 'GitHub PAT' -AsSecureString
$env:CODEX_WECHAT_TOKEN = [Net.NetworkCredential]::new('', $secure).Password
try { Invoke-Expression ((Invoke-WebRequest -UseBasicParsing -Headers @{Authorization="Bearer $env:CODEX_WECHAT_TOKEN"; Accept='application/vnd.github.raw+json'} 'https://api.github.com/repos/zxzxn3/codex-wechat/contents/bootstrap.ps1?ref=main').Content) } finally { Remove-Item Env:CODEX_WECHAT_TOKEN -ErrorAction SilentlyContinue }
```

安装需要 Python 3.11+、Git、Codex 和 `curl`。PAT 可以共享给多名测试者，也可以每人一个；
撤销后新的安装和更新立即失效。上面的入口用交互输入或内存中的认证头，不会把 PAT
写入 shell 历史、仓库配置或安装状态。
详细前提、更新和卸载见 [安装、更新与卸载](docs/INSTALL.md)。

## 第一次使用

Linux：

```bash
~/.local/share/codex-wechat/codex-wechat service-start
```

Windows：

```powershell
codex-wechat service-start
```

未绑定时会在当前终端显示二维码，扫码后后台服务继续启动。若安装器提示服务未注册，改用
前台 `start` 并保持终端运行。微信只处理扫码账号的私聊，群聊不会响应；发送 `/help`
查看命令。

Windows 的短命令入口是 `%USERPROFILE%\.local\bin\codex-wechat.cmd`。只有该目录已存在且在
用户 PATH 中时安装器才会创建它；否则使用
`& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-start`。
入口细节见 [COMMANDS](docs/COMMANDS.md)。

## 常用命令

```text
codex-wechat status
codex-wechat service-start
codex-wechat service-restart
codex-wechat stop
codex-wechat autostart true|false
codex-wechat logs [--follow] [--service]
codex-wechat uninstall [--purge-data]
```

无参数运行默认执行 `status`。`service-restart` 等待服务稳定运行后返回；`stop` 只停止后台
服务，前台桥接使用 `Ctrl+C`。Windows 的 `--follow`、`--service`、`--purge-data` 分别写作
`-Follow`、`-Service`、`-PurgeData`。完整命令表见 [COMMANDS](docs/COMMANDS.md)。

后台服务运行时不能在前台执行 `start`；前台桥接持锁时不能执行 `service-start`。除只读
`status` / `logs` 和显式 `uninstall` 外，root/管理员终端会被拒绝。容器或一次性 CI 可设置
`CODEX_WECHAT_ALLOW_ROOT=1`。

## 微信里的基本行为

- 普通消息默认 steer；`/queue {prompt}` 等空闲后独立执行，不阻挡后续 steer；`/unqueue`
  撤回最后一条仍在等待的 queue。
- `/sessions` 支持翻页和按标题检索；`/switch` 支持 4–32 位会话 ID 前缀、编号和标题，
  多个匹配时显示候选。
- 图片、视频和文件可直接从手机发送；附件 skill 可从安装器的 `components` 入口单独安装；
  其它本机文件用 `/get` 取回。带转写文本的微信语音可直接作为输入。
- 引用文字和局部引用会作为上下文交给 Agent；`/ping` 只测微信链路和供应商建连，不调用模型。
- 中途完整回复直接发往微信，不发送逐字增量、推理或工具流水；中断、重启和失败恢复遵循
  [ARCHITECTURE](docs/ARCHITECTURE.md) 的提交边界。
- `/stop` 清空待办并中断当前共享轮次，不能撤销已经发生的操作；`/restart` 保留确定未提交
  的输入。
- 原生提问与审批只在当前连接实际收到请求时回应；桌面已处理的请求不会被手机重放。
- 已知限制：按住语音转文字、松开自动发送时，手机可能显示已发送但桥接收不到；改用
  “点击语音转文字，文字进入输入框后手动发送”已确认可用。

## Codex CLI 与 App

桥接只在真正运行时持有会话写者，回合结束会交还；空闲时 App 可以打开同一会话，下一条微信
消息会重新获取写者。遇到 App 的 active writer 时，桥接通过 App IPC 跟随，不抢锁、不关闭
App。App 必须先打开目标会话；反向接入尚未实现。

Linux App IPC 使用同 `CODEX_HOME` 的私有 Unix socket。Windows 使用本机命名管道，并核对
服务进程用户和已注册 App；Windows 只跟随主 `CODEX_HOME`。详细边界见
[ARCHITECTURE](docs/ARCHITECTURE.md) 和 [SOURCES](docs/SOURCES.md)。

只用 CLI 与微信双向收发时，在微信发送：

```text
/settings shared true
```

该开关默认 Windows 为 `false`、Linux 为 `true`；开启后 Codex App 打开桥接当前会话可能显示
锁定。选择原则：

- Windows App 为主：保持 `false`
- Windows Codex CLI 为主：改为 `true`
- Linux Codex CLI 为主：保持 `true`
- Linux App 为主：改为 `false`

普通 CLI 直接运行 `codex` 或对应 backend 的 launcher，例如 `codex-deepseek`。`codex-wechat`
不提供 CLI 包装命令；桥接和 CLI 通过同一 Codex daemon/`CODEX_HOME` 协同。
`codex-desktop` / `codex-desktop.ps1` 只作为内部共享后台适配器保留，不是公开命令。

## 配置

运行配置是 `config.json`，示例见 [config.example.json](config.example.json)。配置按文件变化
热加载；无效配置保留上一组有效值并提示一次，不中断当前回合。

后端定义位于 `backends.<名字>`，进程启动和每次 `/backend` 前重新读取，支持 `label`、
`command`、`env`、`sharedDaemon`、`useSystemProxy` 和 `threadOptions`。修改当前后端后再次
执行 `/backend` 会重连；`/settings` 只处理热配置，不重读后端定义。新建会话才应用
`threadOptions`，已有会话保留自己的设置。

常用微信设置：

```text
/settings
/settings ack <template>
/settings finish <template>
/settings sourceHint <text>
/settings autostart true|false
/settings shared true|false
/settings compactionNotice true|false
/settings slicing.smart true|false
/settings connectionIdleSeconds 0-3600
/settings media.maxMiB 1-100
/settings slicing.targetChars <number>
/settings logs.keep 1-100
/help settings
```

`/settings ack fields` 和 `/settings finish fields` 列出模板字段。更多提示、切片、限额和
代理配置见 [ARCHITECTURE](docs/ARCHITECTURE.md#配置与资源)。

## 绑定失效与日志

确定认证失效后，桥接暂停微信收发和新输入提交，保留待办、发件箱和正在运行的 Agent 结果，
通过系统通知提示重绑：

```bash
codex-wechat rebind
```

Windows 使用 `.\codex-wechat.ps1 rebind`；已有提交工作时要加
`--stop-active` / `-StopActive`。同微信号保留交付和偏好；bot 变化时重置协议上下文；换微信
号时隔离旧交付。

日志默认写入数据目录的 `logs/bridge.jsonl`，按 5 MiB 轮转并保留 3 份备份；可用
`/settings logs.keep 1-100` 调整保留份数：

```bash
codex-wechat logs [--follow] [--service]
```

`--service` 才读取平台服务控制台（Linux `journalctl`，Windows `service.log`）；它不是脱敏
结构化日志。Windows 的 `service.log` 同样按 5 MiB 轮转，并与 `bridge.jsonl` 共用
`logs.keep`；Linux 控制台日志的保留由 journald 管理。字段、脱敏边界和延迟阶段见
[ARCHITECTURE](docs/ARCHITECTURE.md#诊断与隐私)。

## 文档导航

- [安装、更新与卸载](docs/INSTALL.md)
- [命令面](docs/COMMANDS.md)
- [平台差异](docs/PLATFORM-DIFFERENCES.md)
- [架构与恢复边界](docs/ARCHITECTURE.md)
- [Windows 操作与故障排查](docs/WINDOWS.md)
- [验证状态](docs/VALIDATION.md)
- [外部依据](docs/SOURCES.md)
- [开发与检查](docs/DEVELOPMENT.md)
- [性能采样](docs/PERFORMANCE.md)
- [复杂度与取舍](docs/COMPLEXITY.md)

## 许可证

本项目是专有内测软件，**禁止再分发和商业使用**。仅授权个人进行非商业评估和测试；完整
条款见 [LICENSE](LICENSE)。

配置、账户、交付状态和日志只保存在本机；凭据不进入命令行、仓库或微信。自动回传只允许
工作目录内非隐藏文件或私有媒体目录，不提高原生沙箱权限。
