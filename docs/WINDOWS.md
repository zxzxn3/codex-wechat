# Windows 11 原生运行

原生 Windows 预览版：实现及离线验证已完成，真实 Windows 安装、扫码和自启仍待实机验收。无需 WSL。

本项目在 Windows 11 下按“当前交互用户”安装，不要求管理员权限，不注册
SYSTEM 服务，也不保存 Windows 密码。计划任务使用 Interactive/Limited
principal，只在该用户图形登录后启动。

## 前置条件

- Windows 11；当前用户能打开普通终端。
- Python 3.11 或更新版本，包含 `venv` 和 `pip`。每个程序版本使用自己的
  `releases\<版本>\.venv`，不再共享会被更新原地修改的环境。
- 已安装并登录原生 Codex CLI。安装器只接受 `codex.exe`（优先查找官方
  `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`），拒绝 npm 的
  `codex.cmd`、`.bat` 或 `.ps1` 转发包装。
- `curl.exe` 8.4 或更新版本。
- 微信登录必须在可见的交互终端完成。安装器不会在后台弹二维码，也不会
  保存扫码结果之外的 Windows 凭据。

如果 PowerShell 阻止本地脚本执行，可在当前终端显式使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

这只影响本次脚本进程，不修改系统级执行策略。其他脚本被阻止时也用同样的 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` 方式运行。

## 安装

运行依赖采取软检查：缺少 CLI、curl、Python、Python 模块或 pip 暂时失败时只提示，继续可完成的准备，不把缺依赖当成安装参数错误。Python/venv 未准备好时不会注册指向不存在解释器的任务；curl 不可用时跳过扫码；未绑定微信或运行依赖未齐时不请求启动。最后会区分任务是否注册、是否待绑定、是否具备运行条件。补齐后重新执行同一安装命令，不覆盖已有配置和登录。

当前后端需要可执行 `app-server` 的 CLI，只有 Codex App 不等同于 CLI 可用；App 本身不是必需依赖，也不自动扫描它的私有安装目录。Windows App 与原生 CLI 默认共享 Codex home，活动桌面接管仍需实机验证。

推荐从解压后的源码目录运行统一安装器：

```powershell
py -3 installer.py install
```

它会显示计划并二次确认，然后把程序安装到
`%LOCALAPPDATA%\codex-wechat\app`，再调用项目内的原生 Windows
安装脚本完成 Python 环境、微信扫码和计划任务设置。继续使用已有的手动安装方式也可以：

```powershell
.\install.ps1
```

原生 Windows 安装阶段会：

1. 检查 Windows 11、Python 3.11+、Codex CLI 和 `curl.exe` 版本；
2. 在版本目录创建独立 `.venv` 并安装 `requirements.txt`；
3. 在 `%LOCALAPPDATA%\codex-wechat` 创建数据目录；
4. 如果配置不存在，创建只含普通 `codex` 后端的 `config.json`，已有的
   `config.json` 不会被覆盖；
5. 在交互终端执行 `login`，由手机微信扫码；
6. 询问登录自启选择（首次默认关闭，已有安装默认保留实际状态），注册当前用户计划任务并另行请求立即启动。

## 更新

下载或解压新版本到另一份源码目录，在 PowerShell 中运行：

```powershell
py -3 installer.py update
```

更新器把新源码完整安装到
`%LOCALAPPDATA%\codex-wechat\app\releases\<版本>`，在该目录建立独立
Python 环境并校验，成功后原子切换 `app\current`。原版本目录保持不变，
因此可以只切指针回滚：

```powershell
py -3 installer.py rollback
```

Windows 计划任务指向稳定的 `app\bootstrap_supervisor.py`，不再绑定某个
具体版本路径。更新时保留现有自启状态，但不自动启动任务；完成后重启
任务使新版本接管：

```powershell
cd "$env:LOCALAPPDATA\codex-wechat\app"
.\codex-wechat.ps1 service-restart
```

版本目录不会自动删除。确认 `current` 稳定且不再需要 `previous` 后，可
手动清理未引用的旧版本目录。配置、`.state`、workspace 和日志始终位于
`app` 的上一级数据目录，不会被版本切换触碰。

只准备安装、不立即启动（自启选项仍单独选择）：

```powershell
.\install.ps1 -Autostart on -NoStart
```

任务允许电池供电、无运行时长限制、`IgnoreNew` 单实例并隐藏窗口。安装时由登录触发器的 Enabled 决定是否自启；关闭自启仍保留手动启动能力。非交互安装必须明确传 `-Autostart on` 或 `-Autostart off`，否则报错，不等待输入。计划任务不叠加失败重试，异常子进程由 supervisor 在 5 分钟/5 次失败上限内退避恢复，正常退出或配置错误 78 不重开。

## 从旧名称迁移

若已安装旧名 `agent-wechat` 的预览版，先在旧项目执行 `uninstall.ps1`，保留数据；确认旧任务停止后，将 `%LOCALAPPDATA%\agent-wechat` 重命名为 `%LOCALAPPDATA%\codex-wechat`，再在新项目运行 `install.ps1`。不要同时运行新旧任务，也不要覆盖已有的新数据目录。

## 日常命令

```powershell
.\codex-wechat.ps1 login            # 交互终端扫码
.\codex-wechat.ps1 start            # 前台运行，便于调试
.\codex-wechat.ps1 status           # 保存状态 + 计划任务状态
.\codex-wechat.ps1 service-start
.\codex-wechat.ps1 service-stop
.\codex-wechat.ps1 service-restart
.\codex-wechat.ps1 service-status
.\codex-wechat.ps1 autostart
.\codex-wechat.ps1 autostart on
.\codex-wechat.ps1 autostart off
.\codex-wechat.ps1 logs
.\codex-wechat.ps1 logs -Follow
```

微信 `/settings` 显示系统实际自启状态，`/settings autostart on|off` 即时更改，无需重启。只修改登录触发器，不停当前任务，不禁用整个任务；自启不是 config.json 中的第二份布尔值。

`service-restart` 会停止并重新启动计划任务，适合应用代码升级。微信内的
`/restart` 不会重启同一个计划任务：它让当前桥接以退出码 75 结束，由
仍存活的 supervisor 启动新的桥接子进程，避免 ScheduledTask 的
`IgnoreNew` 与旧进程互相等待。

前台 `start` 与后台任务共用状态目录的单实例锁。后台服务运行时先停止
后台服务，再启动前台实例。

计划任务记录稳定 bootstrap、数据路径和安装根目录，不记录某个具体版本的
程序或 `.venv`。版本切换不需要重写任务；移动整个安装根目录后仍需重新运行
安装器。

## 数据、配置与日志

默认路径：

```text
%LOCALAPPDATA%\codex-wechat\config.json
%LOCALAPPDATA%\codex-wechat\.state\
%LOCALAPPDATA%\codex-wechat\workspace\
%LOCALAPPDATA%\codex-wechat\logs\service.log
```

`service.log` 超过 5 MiB 时在 supervisor 下一次启动时轮换为
`service.log.1`。日志可能包含微信消息摘要、异常和后端状态，不应发给
不受信任的人。

## Codex 后端与桌面会话

默认只启用普通 Codex，通过其原生 `app-server` stdio 接口运行。认证、模型、工具和权限沿用当前用户的 Codex 配置；微信常规指令、已有空闲会话、steer/queue 和附件逻辑与 Linux 共用。工作中桌面会话的接管不属于默认模式的保证。

共享模式需显式设置，仍是待 Windows 实机确认的实验选项：

```json
{
  "backends": {
    "codex": {"sharedDaemon": true}
  }
}
```

它使用原生 `app-server proxy`，不开放网络监听。连接失败不会静默改为独立模式。恢复默认模式请设为 `false`，然后重启服务。

不要求安装 codex-deepseek。以后添加其他后端时，在 `backends` 中提供相应名称、Windows 可用的 `command` 参数数组和可选 `env` / `threadOptions`。例如原生 Codex 的独立配置目录：

```json
{
  "backends": {
    "codex": {},
    "deepseek": {
      "command": ["codex", "app-server"],
      "env": {"CODEX_HOME": "C:/Users/your-name/codex-deepseek-home"}
    }
  }
}
```

示例只保留接入接口；该目录的提供商配置、认证和任何 DeepSeek 代理须另行准备，不会把 Linux 的 codex-deepseek 启动器或你的密钥复制过去。启动器若是脚本，需在 command 中明确指定解释器，不自动通过 shell 执行 `.cmd` / `.bat` / `.ps1`。

修改后端启动参数后使用 `service-restart`。提示、切片等热配置仍在下次使用时重载。

Windows 数据继承当前用户目录权限，不把 POSIX `chmod` 当作 ACL 保证。不要把凭据或配置放进共享目录。后台托管的进程清理机制及其边界见 [维护契约](ARCHITECTURE.md)。

## 卸载

```powershell
.\uninstall.ps1
```

默认只移除 `CodexWeChat` 计划任务，保留版本目录、稳定入口和
`%LOCALAPPDATA%\codex-wechat` 数据。确认不再需要凭据、待办和历史后，
可显式清除数据：

```powershell
.\uninstall.ps1 -PurgeData
```

脚本只允许删除 `%LOCALAPPDATA%\codex-wechat`，不会删除项目目录。

## 尚未在真实 Windows 联调的项目

当前 Linux 固定副本离线回归执行 130 项，129 通过、1 项原生 Windows 文件锁检查跳过；PowerShell 7.6.6 脚本语法检查通过。这些不是 Windows 实机验收。Windows 上可先运行专门的离线用例，不会登录微信或调用模型：

```powershell
$app = "$env:LOCALAPPDATA\codex-wechat\app"
$release = (Get-Content "$app\current" -TotalCount 1).Trim()
& "$app\releases\$release\.venv\Scripts\python.exe" `
    -m unittest discover -s "$app\releases\$release\tests" `
    -p "test_windows*.py" -v
```

主套件还有 POSIX 权限等 Linux 专用断言，不应直接把其在 Windows 的全部结果作为平台验收。真实安装后应逐项检查扫码、重登录自启、文字/图片/文件收发、会话切换和 `/restart`；请使用独立测试会话，不操作正在工作的桌面会话。

以下路径目前只按官方接口和离线 mock 设计，不能当作已通过：

- Windows Terminal / PowerShell 中真实二维码显示和手机扫码；
- 计划任务在域策略、企业 AppLocker、已有 Job Object 限制下的实际行为；
- 原生 Codex exe 自动发现、DeepSeek 启动器和桌面密钥环；
- 真实 Windows 下停止任务时 Job Object 的清理时序；
- 真实微信收发、共享桌面会话和附件 CDN。

如果公司策略禁止 Interactive 计划任务、隐藏任务或 Job Object 分配，
需要由设备管理员调整策略；本安装器不会绕过限制。

## 依据

- [部署 Windows app 与 Codex CLI](https://learn.chatgpt.com/docs/enterprise/windows-deployment)
- [New-ScheduledTaskSettingsSet](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)
- [New-ScheduledTaskPrincipal](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal)
- [Python asyncio 的 Windows 平台限制](https://docs.python.org/3.14/library/asyncio-platforms.html)
