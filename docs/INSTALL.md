# 安装、更新与卸载

本文是安装生命周期的单一说明。命令参数见 [COMMANDS](COMMANDS.md)，平台差异见
[PLATFORM-DIFFERENCES](PLATFORM-DIFFERENCES.md)，运行期边界见 [ARCHITECTURE](ARCHITECTURE.md)。

## 前提

- Python 3.11+。
- 已安装并登录 Codex；独立 CLI 或已注册的 App 内置 Codex 均可。
- Git；私有安装入口用它认证 clone，运行时网络测速和部分网络操作仍使用 `curl`。
- Linux 一行安装还需要 `curl`；Windows 一行安装使用 PowerShell `Invoke-WebRequest`。
- Linux 可选 systemd 用户服务；没有 `systemctl --user` 时安装器会跳过服务注册，仍可使用前台
  `start`。
- 二维码优先使用 `qrencode`；缺少时按安装器提示安装轻量 Python 回退。

## 首次安装

仓库保持私有。fine-grained PAT 只选择 `zxzxn3/codex-wechat`，Repository permissions
仅授予 `Contents: Read-only`；`Metadata: Read-only` 由 GitHub 保留。PAT 直接写在命令中，
会进入 shell 历史。

Linux：

```bash
env CODEX_WECHAT_TOKEN='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' bash -o pipefail -c 'if [[ -z ${HTTPS_PROXY:-} && -z ${https_proxy:-} ]] && command -v gsettings >/dev/null; then mode=$(gsettings get org.gnome.system.proxy mode 2>/dev/null || true); mode=${mode#?}; mode=${mode%?}; if [[ $mode == manual ]]; then host=$(gsettings get org.gnome.system.proxy.http host 2>/dev/null || true); host=${host#?}; host=${host%?}; port=$(gsettings get org.gnome.system.proxy.http port 2>/dev/null || true); if [[ -n $host && $port =~ ^[0-9]+$ && $port -gt 0 ]]; then export HTTPS_PROXY="http://$host:$port" HTTP_PROXY="http://$host:$port"; fi; fi; fi; curl -fsSL -H "Authorization: Bearer $CODEX_WECHAT_TOKEN" -H "Accept: application/vnd.github.raw+json" "https://api.github.com/repos/zxzxn3/codex-wechat/contents/bootstrap.sh?ref=main" | bash'
```

Windows PowerShell：

```powershell
$env:CODEX_WECHAT_TOKEN='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'; $r = Invoke-WebRequest -UseBasicParsing -Headers @{Authorization="Bearer $env:CODEX_WECHAT_TOKEN"; Accept='application/vnd.github.raw+json'} 'https://api.github.com/repos/zxzxn3/codex-wechat/contents/bootstrap.ps1?ref=main'; $body = $r.Content; if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }; & ([scriptblock]::Create($body))
```

入口先从 GitHub Contents API 取得同仓库中的 bootstrap。bootstrap 使用 PAT 认证 clone
到临时目录，再调用本地安装器；认证头只放在临时 git config 和进程环境中，不进入 argv、
Git remote、`.update-source.json`、配置或日志。
重跑同一入口即可安装或更新。

也可以直接将完整源码目录或源码压缩包交给使用者，在源码目录运行本地入口：

```bash
# Linux
cd /path/to/codex-wechat
bash install.sh
```

```powershell
# Windows PowerShell
Set-Location C:\path\to\codex-wechat
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

`install.sh` / `install.ps1` 只适配平台并调用同一个 `installer.py`；也可以直接运行
`python3 installer.py install`，Windows 使用 `py -3 installer.py install`。源码包不应包含
`.venv`、`.state`、`config.json`、`logs`、`workspace`、`releases` 等本机运行数据。

安装器不会初始化微信账户、收集模型密钥或默认开启登录自启，也不会在未经确认时启动程序。
安装结束后的下一步以安装器输出为准；通常需要执行一次 `service-start` 并扫码。
交互式安装成功后，安装器会额外询问是否立即启动；直接回车即启动，输入 `n` 跳过。
`--non-interactive` 不会询问或自动启动。

图片/文件回传所需的附件说明 skill 可从安装器的 `components` 入口单独安装，不影响主安装。

## 第一次启动

Linux：

```bash
~/.local/share/codex-wechat/codex-wechat service-start
```

Windows：

```powershell
codex-wechat service-start
```

若没有生成 `%USERPROFILE%\.local\bin\codex-wechat.cmd`，用完整路径并显式绕过当前进程的
PowerShell 执行策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-start
```

未绑定时，`service-start` 在当前终端显示二维码；扫码后后台服务启动，终端可以关闭。
若服务注册被跳过，使用前台 `start`，保持终端运行。微信默认只处理扫码账号的私聊，群聊
不会响应。

服务已在可用时注册：

- Linux 注册 systemd 用户 unit；
- Windows 注册当前用户的 Scheduled Task；
- 两者都不自动开启登录自启；需要时在微信发送 `/settings autostart true`；
- 自启设置只改登录触发器，不启动、停止或接管当前桥接。

## 更新

再次运行同一个网络入口或本地入口即可更新已有安装。私有网络入口不会保存 PAT，也不会
自动写入可复用的更新来源；安装后若要使用 `codex-wechat update`，请显式传
`--source`（或重跑同一个网络入口）。更新会保留配置、登录、待办、工作目录、
日志和已有的登录自启状态，不重启正在运行的桥接；新版本先进入独立 release 并完成检查和
运行时准备，最后才切换 `current`。项目不提供安装回滚命令。

每个版本有独立的 Python 环境。更新会清理不再使用的旧 release，但当前 release 和正在运行的
release 通过 `current` / `active-release` 保留。详细阶段边界见 [ARCHITECTURE](ARCHITECTURE.md)
的安装段落。

## 卸载

正常用户只使用公开入口：

```bash
codex-wechat uninstall              # 保留配置、登录、待办和工作目录
codex-wechat uninstall --purge-data # Linux：同时删除已知用户数据
```

Windows 使用：

```powershell
codex-wechat uninstall              # 保留数据
codex-wechat uninstall -PurgeData   # 删除已知用户数据
```

卸载不依赖 `current` 指向的 release；版本指针损坏或 release 不完整时仍可移除服务和入口。
Linux stable `codex_wechat/uninstall.py` 与 Windows stable `uninstall.ps1` 是内部实现，正常用户不应直接调用；
它们只在维护者排查损坏入口时使用。

`--purge-data` / `-PurgeData` 只删除已知用户数据项，不递归删除自定义数据根目录。前台桥接仍
持有状态锁时，卸载会拒绝继续；先按 `Ctrl+C` 退出。自定义数据目录通过
`CODEX_WECHAT_DATA_ROOT` 指定，Windows 内部 helper 另外接受 `-DataRoot`。

卸载结束时会把两处残留打印出来：程序文件（安装树，含 `releases` 与 Python 环境）和数据文件
（数据根）；需要在卸载后彻底删除，由用户自己决定。

不带 purge 的卸载会保留 `.data-root`：安装树仍在（卸载只移除服务与入口），所以重新运行安装器
会解析为 update，并继续使用原来的数据根。想让重装改判到平台默认目录，删除该标记或显式设置
`CODEX_WECHAT_DATA_ROOT` 即可。

## 目录和配置

Linux 安装根固定为 `~/.local/share/codex-wechat`；`installer.py --dir` 的其他路径会被拒绝。
默认用户数据根由 `platformdirs.user_data_dir` 计算，通常也是
`~/.local/share/codex-wechat`。Windows 程序位于 `%LOCALAPPDATA%\codex-wechat\app`，用户数据根为
`%LOCALAPPDATA%\codex-wechat`。可用 `CODEX_WECHAT_DATA_ROOT` 分离数据根；
`CODEX_WECHAT_INSTALL_ROOT` 只用于稳定入口和源码副本的运行定位，不是 Linux 自定义安装目标。
安装器把当前数据根（包括默认根）写入安装根目录的 `.data-root`；公开入口、Linux systemd
服务和 Windows supervisor 都从同一个标记解析数据根，不把数据根复制进 unit/task action。
因此修改数据根只需通过安装器更新标记，不需要额外改服务配置。
项目尚未公开发布，不保留旧安装布局、旧 marker 或旧持久状态的迁移路径。

`current`、`active-release`、`releases/`、`.state/`、`logs/`、`workspace/`、`shared/` 和
`config.json` 各有独立职责，不复制供应商凭据或会话历史。配置与提示的细节见
[ARCHITECTURE](ARCHITECTURE.md#配置与资源)。
