# 命令词表与平台不对称

本文记录 `codex-wechat` 的**用户可见命令面**在两个平台上的实际差异，以及由此暴露的三个待修问题。
运行契约见 [ARCHITECTURE](ARCHITECTURE.md)，能力边界见 [CAPABILITIES](CAPABILITIES.md)。

核对基线：`main` @ `0b44360`，Windows 11 + Python 3.12.10 实机核对，Linux 侧依据同一份代码的
`systemctl` 分支。文档中的行号以该提交为准，后续改动可能失效。

## 入口

| | Linux | Windows |
|---|---|---|
| 主入口 | `codex-wechat`（安装时软链到 `~/.local/bin`，**在 PATH**） | `%LOCALAPPDATA%\codex-wechat\app\codex-wechat.ps1`（**不在 PATH**） |
| CLI/App 入口 | `codex-desktop`（安装根，不在 PATH） | `codex-desktop.ps1`（安装根，不在 PATH） |
| 安装入口 | `install.sh` | `install.ps1` |
| 卸载入口 | 无 | `uninstall.ps1 [-PurgeData]` |

Windows 的主入口不在 PATH 是安装器的有意选择（`installer.install_command_link()` 在 Windows 直接
返回），代价是所有文档都必须写全路径。

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

Python 层接受的动作只有六个加两个服务词（`codex_wechat.py:820`）：

```
login  start  stop  status  rebind  logs  service-start  service-restart
```

`service-stop`、`service-status`、`autostart`、`uninstall` 只存在于 `codex-wechat.ps1`
（`ValidateSet` 见 `codex-wechat.ps1:5`）。`cli` / `desktop` 只存在于 Linux 的 shell 入口。

### 只是"加词"而非"补实现"的三项

| 缺的词 | 底层已有的实现 |
|---|---|
| `service-stop` | `service_adapter.stop_and_wait()`（`service_adapter.py:84`），Linux 分支已用 `systemctl --no-block stop` |
| `service-status` | `service_adapter.status()`（`service_adapter.py:49`），Linux 分支已用 `systemctl show --property=LoadState/ActiveState/FragmentPath` 并校验 `FragmentPath` 属于本安装目录 |
| `autostart on\|off` | `autostart.set_enabled()`，Linux 分支已用 `systemctl enable/disable <UNIT>` 并回读确认 |

`uninstall` 没有对应实现：`uninstall.ps1` 是纯 PowerShell，Linux 侧需要新写
（停止并禁用用户服务、删除 unit、删除数据目录）。

## 同一命令的行为差异

### `login` 的守卫只在 Windows 存在

| 检查 | Linux | Windows |
|---|---|---|
| 必须交互终端 | ❌ | ✅ `codex-wechat.ps1:216` |
| 后台服务必须已停 | ❌ | ✅ 同上 |

原因是这两道检查写在 `codex-wechat.ps1` 的 `login` 分支里，而 Linux 的 `codex-wechat login` 直接进入
`wechat._login()`（`wechat.py:214`），没有对应逻辑。对比 `rebind` 就做对了：`rebind.py:51` 在
Python 层检查 `isatty`，并先调用 `adapter.status()` 拒绝外部服务。

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
| 起点 | `codex_wechat.py:795` | `codex-wechat.ps1:260` |
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

### 1. Windows `service-start` 可能假成功（严重）

链路：

1. `codex-wechat.ps1:260` → `Start-ScheduledTask` → `Wait-TaskRunning` → 打印「后台服务已启动；终端可以关闭。」
2. 若前台 `start` 仍持有状态锁，supervisor 拉起的 `codex_wechat.py start` 在
   `startup_store()` 处拿到 `RuntimeError`（`storage.py:49`），进程非零退出。
3. ps1 已经打印成功并 `exit 0`，任务状态早已回到 Ready。

Linux 不受影响：`start_background_service()` 在 `service_adapter.start()` 之后会轮询 `status()`，
失败时抛「后台服务启动请求已提交，但未能确认运行状态」（`service_adapter.py:103`）。

`docs/WINDOWS.md` 的待验证清单里已有一条对应的验收项（"`service-start` 在 15 秒内失败，而不是
打印成功后留下未运行的服务"），当前代码不满足。

修复方向二选一：

- 让 `codex-wechat.ps1 service-start` 转发到 `codex_wechat.py service-start`，把校验收拢到一处；
- 保留 ps1 实现，但在 `Wait-TaskRunning` 之后追加一次 `service-status -Json` 复查，`running` 为 false
  时报错并返回非零。

### 2. `login` 缺少 Python 层守卫（中等）

Linux 上可以：

- 在后台服务运行时执行 `codex-wechat login`，覆盖 `account.json`，而正在运行的桥仍持有旧 token；
- 在非 TTY 环境下执行，二维码被打印进管道。

修复方向：把 `codex-wechat.ps1:216` 的两道检查下沉进 `wechat._login()` 或 `codex_wechat.py` 的
`login` 分支，按 `rebind.py:51` 的写法检查 `isatty`，并在登录前确认后台服务未运行。

### 3. `service-start` 与 `service-restart` 的失败语义不一致（低）

未注册服务时的实测结果：

| 命令 | 异常类型 | 退出码 | 文案 |
|---|---|---|---|
| `service-start` | `ConfigurationError` | 78 | 尚未注册后台服务，请重新运行安装器。 |
| `service-restart` | `ValueError` | 1 | 本项目尚未注册后台服务，请重新运行安装器 |

同一类前置条件应对应同一类失败：`service_adapter.restart()`（`service_adapter.py:123`）应和
`start()` 一致地抛出同类异常。

## 只记录、不建议现在处理

- `make_private()` 去掉了"目录已存在就返回"的早退，Windows 上每次构造 `Store` 都会跑一次 `icacls`
  （每次 CLI 调用多一个子进程）。好处是能自愈被提权破坏的 ACL，属于有意的取舍。
- Windows 的 `status` 比 Linux 多打印一行 `task=…`，未做统一。
- `docs/WINDOWS.md` 与本文有少量重叠：前者面向 Windows 用户的操作与排障，本文只对照命令面。
