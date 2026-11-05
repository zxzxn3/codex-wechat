# 命令词表与平台不对称

本文记录 `codex-wechat` 的**用户可见命令面**在两个平台上的实际差异，以及由此暴露的三个待修问题。
运行契约见 [ARCHITECTURE](ARCHITECTURE.md)，能力边界见 [CAPABILITIES](CAPABILITIES.md)。

核对基线：`main` @ `0b44360`（行号已按 2026-11-05 的修复提交校正），Windows 11 + Python 3.12.10 实机核对，Linux 侧依据同一份代码的
`systemctl` 分支。文档中的行号以该提交为准，后续改动可能失效。

## 入口

| | Linux | Windows |
|---|---|---|
| 主入口 | `codex-wechat`（安装时软链到 `~/.local/bin`，**在 PATH**） | `~/.local/bin/codex-wechat.cmd`（安装时生成，**前提是该目录已在 PATH 中**） |
| CLI/App 入口 | `codex-desktop`（安装根，不在 PATH） | `codex-desktop.ps1`（安装根，不在 PATH） |
| 安装入口 | `install.sh` | `install.ps1` |
| 卸载入口 | 无 | `uninstall.ps1 [-PurgeData]` |

Windows 的命令入口是 `%USERPROFILE%\.local\bin\codex-wechat.cmd`，和 Linux 的
`~/.local/bin/codex-wechat` 是同一个约定。差别在实现：Windows 无管理员权限不能建符号链接，
`.ps1` 又不在 `PATHEXT` 里且受执行策略限制，所以落地成一个内容固定的 `.cmd`
（`installer.windows_shim_text()`），内部用 `-ExecutionPolicy Bypass` 调稳定入口
`<安装根>\codex-wechat.ps1`。同名目录里**不能**再放 `.ps1`：同名时 PowerShell 优先选 `.ps1`，
会把执行策略问题带回来。

该目录不在 PATH 时安装器**降级而不修改 PATH**，只在输出的"命令入口"一行说明原因；
`uninstall.ps1` 只删除带 `rem codex-wechat command shim` 标记的那个文件，不会碰别人的文件。

## 命令词表

`✅` 表示该词被入口接受，`❌` 表示入口直接拒绝（Linux 为 argparse 的 `invalid choice`，Windows 为
`ValidateSet` 校验失败）。

| 命令 | Linux | Windows | 归类 |
|---|---|---|---|
| `start` | ✅ | ✅ | 对称（但互斥检查方式不同，见下节） |
| `stop` | ✅ | ✅ | 对称：Windows 的 `stop` 也转发到同一个 Python 实现 |
| `status` | ✅ | ✅ | Windows 额外打印一行任务状态 |
| `rebind` | ✅ | ✅ | 对称 |
| `logs` | ✅ `--follow` / `--service` | ✅ `-Follow` / `-Service` | 仅参数拼写不同，合理 |
| `login` | ✅ | ✅ | **守卫不同**，见下节 |
| `service-start` | ✅ | ✅ | **实现路径与校验不同**，见下节 |
| `service-restart` | ✅ | ✅ | 实现路径不同 |
| `service-stop` | ❌ | ✅ | **可合并**，底层已具备 |
| `service-status` | ❌ | ✅ | **可合并**，底层已具备 |
| `autostart on\|off` | ❌ | ✅ | **可合并**，底层已跨平台 |
| `uninstall [-PurgeData]` | ❌ | ✅ | **需新写** |
| `update` / `install` / `menu` / `components` | ✅ | ✅ | 对称，均转发同一个 `installer.py` |
| `cli` | ✅ `codex-wechat cli` | ❌ 只能 `codex-desktop.ps1` | **可合并** |
| `desktop` | ✅ `codex-wechat desktop` | ❌ 只能 `codex-desktop.ps1` | **可合并** |
| `status -Json` | ❌ | ✅ | 可合并 |

Python 层接受的动作只有六个加两个服务词（`codex_wechat.py:840`）：

```
login  start  stop  status  rebind  logs  service-start  service-restart
```

`service-stop`、`service-status`、`autostart`、`uninstall` 只存在于 `codex-wechat.ps1`
（`ValidateSet` 见 `codex-wechat.ps1:5`）。`cli` / `desktop` 只存在于 Linux 的 shell 入口。

### 只是"加词"而非"补实现"的三项

| 缺的词 | 底层已有的实现 |
|---|---|
| `service-stop` | `service_adapter.stop_and_wait()`（`service_adapter.py:87`），Linux 分支已用 `systemctl --no-block stop` |
| `service-status` | `service_adapter.status()`（`service_adapter.py:52`），Linux 分支已用 `systemctl show --property=LoadState/ActiveState/FragmentPath` 并校验 `FragmentPath` 属于本安装目录 |
| `autostart on\|off` | `autostart.set_enabled()`，Linux 分支已用 `systemctl enable/disable <UNIT>` 并回读确认 |

`uninstall` 没有对应实现：`uninstall.ps1` 是纯 PowerShell，Linux 侧需要新写
（停止并禁用用户服务、删除 unit、删除数据目录）。

## 同一命令的行为差异

### `login` 的守卫（2026-11-05 起两个平台一致）

| 检查 | Linux | Windows |
|---|---|---|
| 必须交互终端 | ✅ `codex_wechat.require_login_allowed()` | ✅ 同一处（ps1 另有一道前置检查） |
| 后台服务必须已停 | ✅ 同上 | ✅ 同上 |

这两道检查原先只写在 `codex-wechat.ps1:216` 的 `login` 分支里，Linux 的 `codex-wechat login` 会直接
进入 `wechat._login()`（`wechat.py:214`），没有任何守卫。现在统一下沉到 Python 层，写法对齐
`rebind.py:51`；`start` / `service-start` 内部的自动登录不经过这道闸门，因此未绑定时仍能弹二维码。

### 前台与后台的互斥检测方向不一致

| | Linux | Windows |
|---|---|---|
| 后台在运行时执行 `start` | 无前置检查 | `codex-wechat.ps1:236` 预检计划任务，立即拒绝 |
| 前台在运行时执行 `service-start` | 无前置检查 | **无前置检查** |
| 冲突时的实际结果 | `startup_store()`（`codex_wechat.py:669`）等待状态锁最多 30 秒后报错 | 服务进程立即失败退出 |

`startup_store()` 的等待分支只在 `action == "start"` 且非 Windows 时生效，因此两个平台的兜底行为不同。

### `service-start` 的事后校验只在 Linux 生效

| | Linux | Windows |
|---|---|---|
| 起点 | `codex_wechat.py:815` | `codex-wechat.ps1:260` |
| 启动 | `systemctl start` | `Start-ScheduledTask` |
| 事后校验 | 轮询 `service_adapter.status()` 最多 10 秒，失败抛错 | `Wait-TaskRunning`（`codex-wechat.ps1:196`），只看任务状态是否为 Running/Queued |

任务状态在进程刚拉起时就已是 Running，所以 Windows 这条路径无法发现"起来后立刻退出"的情况。

### 版本号校验两处不一致

| 文件 | 正则 |
|---|---|
| `codex-wechat.ps1:76` | `^[A-Za-z0-9._-]+$` |
| `codex-desktop.ps1:15` | `^[a-f0-9]{16}$` |

安装器生成的 release 标识是 16 位十六进制，`codex-desktop.ps1` 与之匹配；`codex-wechat.ps1` 的正则
明显更宽（`..` 也能通过，解析结果会退化为安装根）。

## 待修问题

### 1. ~~Windows `service-start` 可能假成功~~ 已修复（2026-11-05）

链路（修复前）：

1. `codex-wechat.ps1` 的 `service-start` → `Start-ScheduledTask` → `Wait-TaskRunning`
   → 打印「后台服务已启动；终端可以关闭。」
2. 若桥接起来后立即退出（前台仍持有状态锁、配置无效等都算），supervisor 记
   `bridge configuration failed; not restarting` 并退出。
3. ps1 已经打印成功并 `exit 0`，任务状态早已回到 Ready。

Linux 不受影响：`start_background_service()` 在 `service_adapter.start()` 之后会轮询 `status()`，
失败时抛「后台服务启动请求已提交，但未能确认运行状态」（`service_adapter.py:106`）。

**实机复现（修复前）**：把 `config.json` 改成 `{"backends":{"broken":{"label":"broken"}}}` 后
`codex-wechat service-start` → 4.8 秒打印「后台服务已启动；终端可以关闭。」、`exit 0`；
6 秒后 `task=Ready`、`last_result=78`、`service.log` 有
`supervisor: bridge configuration failed; not restarting`。

**修复**：`Wait-TaskRunning`（看到一次 Running 就返回）换成 `Wait-ServiceStable`——
要求任务持续 Running 满 2 秒才算成功；观察到 Running 之后又退出则直接失败，并带出
`state`、`last_result` 与日志路径。`service-start` 与 `service-restart` 都改用它。

**修复后同一条件**：6.8 秒内失败、退出码 1、
「后台服务启动后立即退出（state=Ready last_result=78）；日志: …\bridge.jsonl」；
恢复正常配置后 `service-start` 8.6 秒成功、`task=Running`，`service-restart` 同样正常。

原先考虑的另一种修法（让 ps1 转发到 Python 的 `service-start`）没有采用：那会让 ps1 的
`service-start` 多一次 PowerShell→Python→PowerShell 往返，而校验收敛到
`Wait-ServiceStable` 一处即可。

### 2. ~~`login` 缺少 Python 层守卫~~ 已修复（2026-11-05）

原先只有 `codex-wechat.ps1:216` 检查"交互终端 + 后台服务已停"，Linux 上可以：

- 在后台服务运行时执行 `codex-wechat login`，覆盖 `account.json`，而正在运行的桥仍持有旧 token；
- 在非 TTY 环境下执行，二维码被打印进管道。

现在 `codex_wechat.require_login_allowed()` 在取状态锁之前完成同样的检查，两个平台共用；
"服务属于别的安装"也会被翻译成 `ConfigurationError`（退出码 78）而不是裸 `ValueError`。
对应 `tests/test_service_cli.py` 的 `LoginGateTests`。

### 3. ~~`service-start` 与 `service-restart` 的失败语义不一致~~ 已修复（2026-11-05）

修复前，未注册服务时：`service-start` → `ConfigurationError` / 退出码 78 /
"尚未注册后台服务，请重新运行安装器。"；`service-restart` → `ValueError` / 退出码 1 /
"本项目尚未注册后台服务，请重新运行安装器"。

现在文案统一为 `service_adapter.NOT_INSTALLED`，`restart_background_service()` 把它翻译成
`ConfigurationError`。Windows 实机复核：

```
service-start    exit=78  尚未注册后台服务，请重新运行安装器。
service-restart  exit=78  尚未注册后台服务，请重新运行安装器。
```

## 只记录、不建议现在处理

- `make_private()` 去掉了"目录已存在就返回"的早退，Windows 上每次构造 `Store` 都会跑一次 `icacls`
  （每次 CLI 调用多一个子进程）。好处是能自愈被提权破坏的 ACL，属于有意的取舍。
- Windows 的 `status` 比 Linux 多打印一行 `task=…`，未做统一。
- `docs/WINDOWS.md` 与本文有少量重叠：前者面向 Windows 用户的操作与排障，本文只对照命令面。
