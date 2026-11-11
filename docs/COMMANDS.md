# 命令入口与语义

微信聊天命令、别名、用途和数值设置只在
[command_specs.py](../codex_wechat/command_specs.py) 与
[commands.py](../codex_wechat/commands.py) 中定义。
[生成参考](REFERENCE.generated.md) 从这些代码结构渲染，不再手工维护第二份命令表。
终端 CLI action 的完整 choices 和参数由 [app.py](../codex_wechat/app.py) 的
`argparse` 定义；本文生命周期段只解释这些 action 的用户语义。

## 公开入口

| | Linux | Windows |
| --- | --- | --- |
| 主入口 | `~/.local/bin/codex-wechat` | `%USERPROFILE%\.local\bin\codex-wechat.cmd` |
| 源码安装入口 | `install.sh` | `install.ps1` |

Windows `.cmd` 只转交 stable `codex-wechat.ps1`，再进入 Python。`codex-desktop` /
`codex-desktop.ps1` 是内部共享后台适配器，不通过 `codex-wechat` 暴露。安装器不修改 PATH；
只有目标目录已存在且在 PATH 中时才创建短入口。
Linux 使用 `-h` / `--help`，Windows PowerShell 使用 `-h` / `-Help` 查看入口用法。

## 生命周期

- 无参数默认执行 `status`。
- `start` 前台启动；后台服务运行时拒绝。
- `service-start` 启动已注册后台；前台桥接持锁时拒绝。未绑定时在当前终端扫码。
- `stop` 停止后台；前台桥接用 `Ctrl+C`。
- `service-restart` 等待服务稳定运行，超时才报错。
- `uninstall` 默认保留用户数据；只有显式 `--purge-data` / `-PurgeData` 才清数据。Windows
  仅在卸载器明确报告任务不属于当前安装或共享后台仍有客户端时，才按提示使用 `-Force`。

`install`、`update` 和 `menu` 是 stable 入口转交 `installer.py` 的安装器动作，
不会作为微信聊天命令处理。网络 bootstrap 完成安装后会保存公开更新来源；此后
`codex-wechat update` 从官方 GitHub tar 归档获取最新源码，由 uv 构建并安装 wheel，不需要
宿主 Python 或 Git。
没有保存来源时，重跑同一个网络 bootstrap，
或从本地源码更新：Linux 使用 `update --source <目录>`，Windows 使用
`update -Source <目录>`。更新不会自动重启正在运行的桥接；交互式更新完成后会询问是否立即
重启，默认回车会执行 `service-restart`，`--non-interactive` 不会询问或重启。

`start` 与 `service-start` 的互斥判断只在 Python 层实现。Linux 使用实际 `euid == 0`；
Windows 使用管理员令牌。除只读 `status`、`logs` 和显式 `uninstall` 外，写操作要求普通用户。

## 微信命令

默认 `/help` 只显示常用命令，`/help all` 显示完整命令面。短别名和完整名称走同一路由；
完整列表由生成参考维护。`/version` 是本地命令，后端未连接时也可用。

已移除：

- `service-stop` / `service-status`：改用 `stop` / `status`。
- `codex-wechat desktop` / `codex-wechat cli`：普通 CLI 直接使用 `codex` 或后端 launcher。
- `autostart on|off`：只接受 `true|false`。

## 投递状态

服务端接受请求但没有返回消息 ID 时，结果是投递未知。桥接暂停当前运行时出站，保留积压和
暂停期间新增的输出；不自动重放，也不派生失败通知。下一条被接受的入站消息恢复发送，
`/discard` 可主动清空待发队列。

不保留 `staleOutbox` 自动隔离状态，因为恢复发送不能证明旧队列已经过时。完整运行语义见
[ARCHITECTURE](ARCHITECTURE.md#输出附件与恢复)，当前保护参数见
[生成参考](REFERENCE.generated.md#出站保护)。

## 版本与卸载

stable 入口通过 `.installation.json` 选择安装到独立环境的完整 release，并使用 uv 创建并
托管的 `.bootstrap-venv` 运行。Linux 和 Windows 都在加载 release 之前分派 `uninstall`，
所以 release 不完整时仍可按同一安装布局移除服务和入口。release ID、数据根和平台参数见
[PLATFORMS](PLATFORMS.md)，完整安装流程见 [INSTALL](INSTALL.md)。
