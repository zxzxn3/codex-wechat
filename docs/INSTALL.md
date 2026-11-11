# 安装、更新与卸载

本文是安装生命周期的单一说明。命令入口见 [COMMANDS](COMMANDS.md)，平台操作与支持面见
[PLATFORMS](PLATFORMS.md)，运行期边界见 [ARCHITECTURE](ARCHITECTURE.md)。

## 前提

- Python 3.11+。
- 已安装并登录 Codex；独立 CLI 或已注册的 App 内置 Codex 均可。
- Git；公开安装入口用它 clone 源码，运行时网络测速和部分网络操作仍使用 `curl`。
- Linux 一行安装还需要 `curl`；Windows 一行安装使用 PowerShell `Invoke-WebRequest`。
- Linux 可选 systemd 用户服务；没有 `systemctl --user` 时安装器会跳过服务注册，仍可使用前台
  `start`。
- 二维码优先使用 `qrencode`；缺少时按安装器提示安装轻量 Python 回退。

网络 bootstrap 会检查 Python 3.11+、Git 和 Linux 的 `curl`；本地 `install.sh` / `install.ps1`
只检查 Python。缺少依赖时入口不会自动修改系统，而是打印当前包管理器或 `winget` 的安装
命令。安装依赖后需要重开终端，再重跑原命令。Ubuntu 22.04 的官方 `python3` 仍低于 3.11，
重复安装 `python3` 不会满足要求，需要先获得发行版认可的 Python 3.11+。常用命令：

```bash
# Arch / CachyOS
sudo pacman -S --needed python git curl

# Debian / Ubuntu
sudo apt-get install -y python3 git curl

# Fedora
sudo dnf install -y python3 git curl
```

```powershell
# Windows
winget install --id Python.Python.3.13 -e
winget install --id Git.Git -e
```

## 首次安装

Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/zxzxn3/codex-wechat/main/bootstrap.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/zxzxn3/codex-wechat/main/bootstrap.ps1 | iex
```

也可以直接下载并分发仓库中的 `bootstrap.ps1`。接收方在原文件所在目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

入口从公开的 GitHub raw 地址取得 bootstrap。bootstrap 下载安装器，再由安装器把公开仓库
clone 到临时目录。重跑同一入口即可安装或更新。

也可以直接 clone 后运行源码入口：

```bash
# Linux
git clone https://github.com/zxzxn3/codex-wechat.git
cd codex-wechat
bash install.sh
```

```powershell
# Windows PowerShell
git clone https://github.com/zxzxn3/codex-wechat.git
Set-Location codex-wechat
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

也可以直接把源码目录或源码压缩包交给使用者，在源码目录运行同一个本地入口：

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

桥接后端会自动读取项目自带的 `skills/`。如需把这些 skill 安装给其他 Codex agent，
可从安装器的 `components` 入口单独安装；这不影响主安装。

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

再次运行同一个网络入口或本地入口即可更新已有安装。公开网络入口会保存公开仓库作为可复用
更新来源，也可以继续重跑同一个网络入口。更新会保留配置、登录、待办、工作目录、日志和已有
的登录自启状态，不会自动重启正在运行的桥接；更新完成后，交互式安装器会询问是否立即重启，
默认回车执行 `service-restart`，`--non-interactive` 不会询问或重启。新版本先进入独立
release 并完成检查和运行时准备，随后原子提交 `.installation.json`。平台服务集成和入口
可读性检查发生在提交之后；这些后续步骤失败不会回滚已提交版本。项目不提供安装回滚命令。

每个版本有独立的 Python 环境。更新会清理不再使用的旧 release，但当前 release 和正在运行的
release 通过安装布局中的 release ID 和 `active-release` 租约保留。安装器不初始化账户、
不默认开启自启、不自动重启业务，也不提供安装回滚。

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

卸载只依赖 `.installation.json`；release 不完整时仍可移除服务和入口。
Linux stable `codex_wechat/uninstall.py` 与 Windows stable `uninstall.ps1` 是内部实现，正常用户不应直接调用；
它们只在维护者排查损坏入口时使用。

`--purge-data` / `-PurgeData` 只删除已知用户数据项，不递归删除自定义数据根目录。Linux
分别删除配置文件及其固定临时文件、状态目录和数据根下的 `workspace`；Windows 继续删除
统一数据根中的已知项，包括 `config.json.tmp`。
前台桥接仍持有状态锁时，卸载会拒绝继续；先按 `Ctrl+C` 退出。自定义数据目录通过
`CODEX_WECHAT_DATA_ROOT` 在首次安装时指定；卸载器没有第二套数据根参数。Linux purge 还会
停止工作目录属于当前数据根的共享 Codex 后台；任何一步无法确认时先保留数据。

卸载结束时会把程序文件、数据根以及 Linux 的配置和状态位置打印出来；需要在卸载后彻底删除，
由用户自己决定。

不带 purge 的卸载会保留 `.installation.json`：安装树仍在（卸载只移除服务与入口），所以
重新运行安装器会解析为 update，并继续使用原来的根。purge 处理完计划中的用户数据后才删除
布局，但程序树按设计仍在；重新安装前必须先手动删除安装根（Linux 默认
`~/.local/share/codex-wechat`，Windows 默认 `%LOCALAPPDATA%\codex-wechat\app`）。之后
安装器会按当时的 XDG/数据根环境建立新布局。

## 目录和配置

Linux 按 XDG 职责拆分：

| 内容 | 默认位置 |
|---|---|
| 程序 | `~/.local/share/codex-wechat/{.installation.json,releases,active-release}` |
| 配置 | `~/.config/codex-wechat/config.json` |
| 状态、日志和共享后台 | `~/.local/state/codex-wechat/{logs,shared}` |
| 工作区 | `~/.local/share/codex-wechat/workspace` |

Linux 安装根固定为 `~/.local/share/codex-wechat`；`installer.py --dir` 的其他路径会被拒绝。
安装器把平台、release ID、数据根、配置根和状态根作为一条记录原子写入
`.installation.json`。公开入口、systemd 和 Windows supervisor 都通过 `InstalledLayout`
读取这份契约；shell 入口本身不再解析安装状态。

`XDG_CONFIG_HOME`、`XDG_STATE_HOME` 和 `XDG_DATA_HOME` 在首次安装时决定路径根。普通更新
始终沿用已保存的根，不受当前 shell 中这些变量影响。项目不提供原地换根命令；需要搬迁时先
备份数据，再 purge、删除程序安装根，并从目标环境重新安装。`CODEX_WECHAT_DATA_ROOT` 只覆盖首次安装的数据根，
因此工作区随数据根移动，配置和状态仍遵循 XDG；与已保存布局冲突时 fail closed。
`CODEX_WECHAT_INSTALL_ROOT` 只用于稳定入口和源码副本的运行定位。配置根或状态根与程序安装根
重叠，或数据、配置、状态三个角色互相重叠时，安装和 purge 都会 fail closed；默认的
`$data_root/workspace` 是唯一允许位于安装根下的 purge 目标。purge 也会拒绝经过符号链接的
配置、状态、数据根或工作区路径，避免删除链接指向的外部目录；遇到这种布局时请先手动处理
链接。

Windows 保持原布局：程序位于 `%LOCALAPPDATA%\codex-wechat\app`，用户数据根为
`%LOCALAPPDATA%\codex-wechat`，配置、状态、工作区、日志和共享后台仍位于该数据根。
Windows purge 还会拒绝数据根本身及其已有父链中的任何重解析点。

若 `bridge.json` 无法解析或字段类型无效，正常启动会先把它移动到
`bridge.corrupt-*.json`，再以空状态启动。只读的 `codex-wechat status` 不会修改文件，而是
返回 `state=recovery_required` 和具体诊断；先检查该输出及隔离文件，再决定是否恢复其中的
未发送消息。更高 schema 版本的文件不会被自动移动或覆盖。人为构造到解析器或对象复制资源
上限的文件直接 fail fast，不属于损坏隔离承诺。

Linux 和 Windows 都只使用 `.installation.json`。当前版本不迁移 `current`、
`.runtime-roots`、`.data-root`、`.config-root`、`.state-root` 等旧布局；这些标记存在时
直接拒绝启动，避免混用两套根。

配置、状态、日志、共享后台和工作区各有独立职责，不复制供应商凭据或会话历史。热配置和提示
的运行边界见 [ARCHITECTURE](ARCHITECTURE.md#配置与提示)。
