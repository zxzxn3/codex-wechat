# 命令词表与平台不对称

本文记录 `codex-wechat` 的**用户可见命令面**在两个平台上的实际差异，以及由此暴露的四个待修问题。
运行契约见 [ARCHITECTURE](ARCHITECTURE.md)，能力边界见 [CAPABILITIES](CAPABILITIES.md)。

核对基线：2026-11-05 Windows 侧修复之后的 `main`（行号按该快照校正，之后的改动可能让它失效），
Windows 11 + Python 3.12.10 实机核对，Linux 侧依据同一份代码的 `systemctl` 分支。

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

Windows 上提权运行会被硬拒绝：`codex_wechat.py` 与 `installer.py` 在启动时检查
`IsUserAnAdmin()`，命中就报「请以普通用户身份运行，不要使用管理员终端。」（退出码 78 / 1）。
`install`、`update`、`start`、`service-*` 全在内；只有 `uninstall.ps1` 不过闸门，
因为清理历史上由管理员注册的任务确实需要提权。理由见 [WINDOWS](WINDOWS.md) 的
「不要在管理员终端里运行」。

## 命令词表

`✅` 表示该词被入口接受，`❌` 表示入口直接拒绝（Linux 为 argparse 的 `invalid choice`，Windows 为
`ValidateSet` 校验失败）。

| 命令 | Linux | Windows | 归类 |
|---|---|---|---|
| `start` | ✅ | ✅ | 对称（但互斥检查方式不同，见下节） |
| `stop` | ✅ | ✅ | 对称：Windows 的 `stop` 也转发到同一个 Python 实现 |
| `status` | ✅ | ✅ | 同一份 JSON，`service` 段见下文（两个平台字段一致） |
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
| `cli` | ✅ `codex-wechat cli` | ❌ 只能 `codex-desktop.ps1` | **待定，见下节**（默认配置下与直接跑 `codex` 等价） |
| `desktop` | ✅ `codex-wechat desktop` | ❌ 只能 `codex-desktop.ps1` | **建议删除**：无独立语义，见下节 |
| `status -Json` | ❌ | ❌ | 两个平台都拒绝：`status` 本来就输出 JSON，`-Json` 只对 `autostart` / `service-status` 有效 |

Python 层接受的动作只有六个加两个服务词（`codex_wechat.py:892`）：

```
login  start  stop  status  rebind  logs  service-start  service-restart
```

`service-stop`、`service-status`、`autostart`、`uninstall` 只存在于 `codex-wechat.ps1`
（`ValidateSet` 见 `codex-wechat.ps1:5`）。`cli` / `desktop` 只存在于 Linux 的 shell 入口。

### 只是"加词"而非"补实现"的三项

| 缺的词 | 底层已有的实现 |
|---|---|
| `service-stop` | `service_adapter.stop_and_wait()`（`service_adapter.py:103`），Linux 分支已用 `systemctl --no-block stop` |
| `service-status` | `service_adapter.status()`（`service_adapter.py:75`），Linux 分支已用 `systemctl show --property=LoadState/ActiveState/FragmentPath` 并校验 `FragmentPath` 属于本安装目录 |
| `autostart on\|off` | `autostart.set_enabled()`，Linux 分支已用 `systemctl enable/disable <UNIT>` 并回读确认 |

`uninstall` 没有对应实现：`uninstall.ps1` 是纯 PowerShell，Linux 侧需要新写
（停止并禁用用户服务、删除 unit、删除数据目录）。

## 同一命令的行为差异

### `login` 的守卫（2026-11-05 起两个平台一致）

| 检查 | Linux | Windows |
|---|---|---|
| 必须交互终端 | ✅ `codex_wechat.require_login_allowed()` | ✅ 同一处（ps1 另有一道前置检查） |
| 后台服务必须已停 | ✅ 同上 | ✅ 同上 |

这两道检查原先只写在 `codex-wechat.ps1` 的 `login` 分支里，Linux 的 `codex-wechat login` 会直接
进入 `wechat._login()`（`wechat.py:214`），没有任何守卫。现在统一下沉到 Python 层，写法对齐
`rebind.py:51`；`start` / `service-start` 内部的自动登录不经过这道闸门，因此未绑定时仍能弹二维码。

### 前台与后台的互斥检测方向不一致

| | Linux | Windows |
|---|---|---|
| 后台在运行时执行 `start` | 无前置检查 | `codex-wechat.ps1:256` 预检计划任务，立即拒绝 |
| 前台在运行时执行 `service-start` | 无前置检查 | **无前置检查** |
| 冲突时的实际结果 | `startup_store()`（`codex_wechat.py:692`）等待状态锁最多 30 秒后报错 | 服务进程立即失败退出 |

`startup_store()` 的等待分支只在 `action == "start"` 且非 Windows 时生效，因此两个平台的兜底行为不同。
要不要给 Linux 也加一道"后台 unit 在跑就拒绝"的前置检查（对称于 ps1 的预检），由 Linux 侧决定；
Windows 那道预检在 ps1 里，Python 层没有对应实现，所以"统一"意味着把它下沉到 Python。

### `service-start` 的启动校验（2026-11-05 起两个平台都能发现"起来就退出"）

| | Linux | Windows |
|---|---|---|
| 起点 | `codex_wechat.py:867` 的 `start_background_service()` | `codex-wechat.ps1:296` 的 `service-start` 分支 |
| 启动 | `systemctl start`（`service_adapter.start()`，`service_adapter.py:122`） | `Start-ScheduledTask` |
| 事后校验 | 轮询 `status()` 最多 10 秒，超时抛「未能确认运行状态」 | `Wait-ServiceStable`（`codex-wechat.ps1:202`）要求 Running **持续 2 秒**；失败报「后台服务启动后立即退出（state=… last_result=…）」并 `exit 1` |
| 额外兜底 | — | Python 的 `service_adapter.start()` 在 Windows 上回调 ps1 的 `service-start`，因此前面那道校验之后还会再轮询 `status()` 10 秒 |

修复前 Windows 只看 `Wait-TaskRunning`（已删除），而任务状态在进程刚拉起时
就是 Running，所以"起来后立刻退出"会被当成成功；见待修问题 1。

### 版本号校验（2026-11-05 起三处一致）

| 文件 | 校验 |
|---|---|
| `codex-wechat.ps1:82` | `^[a-f0-9]{16}$` |
| `codex-desktop.ps1:15` | `^[a-f0-9]{16}$` |
| `bootstrap_supervisor.py` 的 `active_release()` | `RELEASE_ID.fullmatch()`，同一个 16 位十六进制 |

安装器写入 `current` 的唯一入口是 `_write_pointer()`（`installer.py:768`），值是
`release_identifier()` 的 `sha256(...)[:16]`，所以"16 位十六进制"就是这份文件的真实格式。

修复前 `codex-wechat.ps1` 用 `^[A-Za-z0-9._-]+$`、`bootstrap_supervisor` 用字符白名单，两处都能放过
**`..`**：`releases\..` 就是安装根，而安装根里有 `codex_wechat.py` / `windows_service.py`，
于是校验通过、静默地去跑稳定副本而不是某个 release。`../escape`（带斜杠）一直是被拒的，
所以测试只覆盖到那一种，漏掉了单独一个 `..`。现在三处都要求严格的 16 位十六进制，
`tests/test_bootstrap_supervisor.py` 补了 `..` 的用例，`tests/test_windows_service.py` 钉住了 ps1 的正则。

### `cli` / `desktop` 两个词的实质不同（2026-11-05 查证，去留待 Linux 侧定）

命令词表里这两行原写作"可合并"，查证后应当拆开——它们不是同一类东西。

**`cli` 有真实功能，但只在一种配置下不可替代。** `desktop.py:133-140`：

```python
spec, env = await ensure(args.backend, backend)        # 先确保后台就绪
command = spec.resolve(env, client=True)               # 原生 codex
command += ["--remote", spec.endpoint or "unix://", *forwarded]
return subprocess.call(command, env=env)               # 终端交给 TUI
```

`ensure()` 有两条路（`desktop.py:88`）：配置里有 `websocketUrl`（`codex_connection.py:40`，
只允许本机回环）时，桥自己拉一个带端口的 app-server，运行时目录在 `data_root()/shared/<hash>`；
否则走 `sharedDaemon`，即 `ensure_native(["codex", "app-server"])`。

- **`websocketUrl` 模式**：端点不是默认 socket，原生 `codex` 不知道该连哪里，必须显式
  `--remote ws://127.0.0.1:<port>`。这时 `cli` 不可替代。
- **默认（`sharedDaemon`）模式**：`spec.endpoint` 为空，拼出来是 `codex --remote unix://`，
  而 Codex CLI 默认就挂在共享 daemon 上（`codex --help` 里 `--no-daemon` 的说明是
  "Run without the shared background server, even if it is already running"），
  所以 `codex-wechat cli -- …` **等同于直接跑 `codex …`**。作者本人在 Linux 上一直直接开
  `codex` 而没有差异感，原因就在这里。

它还有一处刻意约束：用户自己传 `--remote` / `--remote-auth-token-env` / `--no-daemon` 会被
`ValueError` 拒绝（"此入口固定使用选定后台"）——它想当那个端点的唯一入口。

**`desktop` 没有自己的语义。** Linux 入口里它只是：

```bash
desktop) shift; exec "$INSTALL_ROOT/codex-desktop" "$@" ;;
```

而 `desktop.py` 的动作只有 `cli` / `ensure` / `setup`（`desktop.py:106`）。所以
`codex-wechat desktop setup` ≡ `codex-desktop setup`，`codex-wechat desktop` 单独跑则直接
argparse 报"缺少 action"。名字还误导：它听起来像"启动桌面 App"，但 App 启动入口是**刻意**
移除的（README 第 140 行"项目不再提供 App 启动命令或桌面入口覆盖"，WINDOWS.md 同款说明）。

**待定项：**

1. `desktop`：建议删掉。Linux 删那个 `case` 分支，Windows 不补。理由是无独立语义、名字误导、
   与 `cli` 重叠。
2. `cli`：去留取决于还要不要支持 `websocketUrl` 模式。
   - 保留（现状）：Linux 有、Windows 没有——词表不对称，但功能不对称也是真实的；
   - 两边都删：websocket 用户自己 `codex --remote ws://127.0.0.1:<port>`，词表就只剩
     `uninstall [-PurgeData]` 一项差异；
   - 或不删但补到 Windows（约 10 行，转发给 `codex-desktop.ps1 cli -- …`），只有在
     websocket 这条路确实有人用时才值得。
3. 无论选哪个，README 第 145 行 `codex-wechat cli -- resume` 与第 148 行
   `codex-desktop.ps1 cli -- resume` 都要跟着改。

## 待修问题

### 1. ~~Windows `service-start` 可能假成功~~ 已修复（2026-11-05）

链路（修复前）：

1. `codex-wechat.ps1` 的 `service-start` → `Start-ScheduledTask` → `Wait-TaskRunning`
   → 打印「后台服务已启动；终端可以关闭。」
2. 若桥接起来后立即退出（前台仍持有状态锁、配置无效等都算），supervisor 记
   `bridge configuration failed; not restarting` 并退出。
3. ps1 已经打印成功并 `exit 0`，任务状态早已回到 Ready。

Linux 不受影响：`start_background_service()` 在 `service_adapter.start()` 之后会轮询 `status()`，
失败时抛「后台服务启动请求已提交，但未能确认运行状态」（`service_adapter.py:122`）。

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

### 4. ~~未绑定时 `start` 与 `service-start` 的提示文案差一行~~ 已修复（2026-11-05，选"补提示"）

两个入口都能进入扫码闸门（2026-11-05 实测），但 `start` 会多打印一行
「尚未绑定微信，请在当前终端扫码；绑定后会继续启动。」：它走 `startup_account()`
（`codex_wechat.py:729`），而 Windows 的 `service-start` 直接调 `login --starting`
（`codex-wechat.ps1:309`）。

现在 ps1 的 `service-start` 在调用 `login --starting` 之前打印同一句
（`codex-wechat.ps1:309`），文案由 `tests/test_windows_service.py` 钉住。

没有选"让 ps1 走 Python 的 `start_background_service()`"：`service_adapter.start()`
在 Windows 上本来就是回调 ps1 的 `service-start`（`service_adapter.py:129`），
直接委托会形成 ps1 → Python → ps1 的循环，必须先给 ps1 加一个"只启动不校验绑定"的内部开关。
那件事与"命令面统一"是同一件工作，留到那时一起做。代价是这句话现在有两处副本，
改文案要同时改 `prompts.py` 与 ps1。

### 2. ~~`login` 缺少 Python 层守卫~~ 已修复（2026-11-05）

原先只有 `codex-wechat.ps1` 的 `login` 分支检查"交互终端 + 后台服务已停"，Linux 上可以：

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

### `status` 的输出与 `service` 段（2026-11-05 起两个平台同一份 JSON）

`codex-wechat status` 在两个平台都**直接输出一份 JSON**（所以 `-Json` 是多余的，两边都会拒绝）。
内容：`bound / auth / available / rebind_required / thread / backend / unfinished_at_last_save /
queued / unsent`，加上 2026-11-05 新增的 `service` 段——也就是"跑桥的那个宿主"现在的状态。

| 字段 | Windows（计划任务） | Linux（systemd --user unit） |
|---|---|---|
| `installed` | 任务是否存在 | unit 是否存在（`LoadState` + `FragmentPath` 校验归属） |
| `running` | 任务是否为 Running/Queued | `ActiveState` ∈ active/activating/reloading/deactivating |
| `state` | 任务的 `State`：`Running`/`Ready`/`Disabled` | `ActiveState`：`active`/`inactive`/`failed` |
| `last_result` | `LastTaskResult`：`267009` 运行中、`267014` 被停止、`0` 正常退出、`78` 配置/凭据错误 | `ExecMainStatus`：上次主进程退出码；从未跑过时为空并省略该字段 |

注意 `running` 是归一化的布尔值（两个平台都填），`state` 保留宿主自己的原值便于排障。
宿主读不出来时（例如从克隆目录跑、任务不属于本安装）不报错，只是省略 `service` 段——
`status` 不能因为查不到宿主就整个失败。

Windows 侧的取值链：`codex-wechat.ps1 status` 在自己进程里读一次任务对象 →
`CODEX_WECHAT_HOST_STATE`（JSON）→ `codex_wechat.status_payload_with_service()` →
`service_adapter.parse_host_json()`（归属校验也在这里，与 `service-status -Json` 共用一份）。
环境变量缺省时退回 `service_adapter.status()`，那是 Linux 的路径。

## 只记录、不建议现在处理

- `/get` 的路径规则是"认本平台原生写法"，没有跨平台转换：Windows 上 `C:\x\a.txt` 与 `C:/x/a.txt`
  都行，但 Linux 风格的 `/home/x/a.txt` 在 Windows 上**不是绝对路径**（有 root 无 drive），会被拼到
  工作目录所在盘（`C:\work\proj` + `/home/x/a.txt` → `C:\home\x\a.txt`），通常就报「附件不存在或
  无法读取」。反过来 Linux 上 `C:\x\a.txt` 只是个带反斜杠的文件名。UNC、驱动器相对路径
  （`C:a.txt`）和 NTFS 数据流（`a.txt:stream`）在 Windows 上是明确拒绝的。
  不额外加"Linux 风格路径不被支持"的专门报错：路径是用户自己给的，落到不存在就报不存在，
  已经能定位问题，再加分支只是换一句话。
- ~~`/get` 的「附件已排队」提示总在**文件之后**到达~~ 已修复（2026-11-05）：`command_get()` 现在
  先 `prepare_asset()`（快照 + 本地校验），再回执，最后 `queue_asset()` 入队。这样本地错误
  （文件不存在、超限、凭据目录）仍然先报原因、不产生假回执，而回执本身赶在附件之前发出，
  不再被整份上传（15 MiB 约 12 秒）压在后面。
  剩下的只是**文案**（待定）：现在这句在文件之前到达，"已排队"字面上没错（它确实刚进队列），但用户随后
  要等十几秒，改成「正在发送附件…」更贴合实际观感；若出站队列正好被别的消息占着，它其实还没
  开始传，所以"上传中"有一点过于具体。改一行 `prompts.ATTACHMENT_QUEUED` 即可。
- Linux 不拒绝 root/sudo。Windows 已经硬拒绝提权（见 [WINDOWS](WINDOWS.md)「不要在管理员终端里运行」），
  但 `platform_support.is_elevated()` 在 POSIX 上恒为 `False`，所以 `sudo codex-wechat` 仍会把 release 装到
  `/root` 下、unit 注册给 root。要不要加 `os.geteuid() == 0` 的对应闸门由 Linux 侧决定
  （注意：某些场景下 sudo 装系统级 unit 可能是刻意的，所以不是照搬 Windows 就能用）。
- `make_private()` 去掉了"目录已存在就返回"的早退，Windows 上每次构造 `Store` 都会跑一次 `icacls`
  （每次 CLI 调用多一个子进程）。好处是能自愈被提权破坏的 ACL，属于有意的取舍。
- ~~Windows 的 `status` 比 Linux 多打印一行 `task=…`~~ 已统一（2026-11-05）：那行散装文本删掉了，
  任务状态与上次退出码并入 `status` 的 `service` 段，两个平台同一份 JSON、同一批字段
  （Linux 侧由 `systemctl show` 的 `ActiveState` / `ExecMainStatus` 填充）。
  Windows 的取值方式：外层 `codex-wechat.ps1` 在自己进程里读一次任务对象，用
  `CODEX_WECHAT_HOST_STATE` 环境变量（JSON）交给 Python，避免"ps1 里再起一个 ps1"。
  走环境变量而不是命令行参数，是因为 PowerShell 把含双引号的字符串交给原生 exe 时会吃掉引号，
  JSON 到 Python 那边已经坏了。
  实测 `status`：嵌套查询 3.26 秒 → 传环境变量 1.96 秒；剩下的约 0.6 秒是 PowerShell 自动加载
  `ScheduledTasks` 模块，再压下去要改用 COM（会把 `Test-OwnedTask` 的归属校验复制到 Python），
  不值得。微信端的 `/status` 完全不受影响，它是桥进程内的文本渲染，不查宿主。
- `docs/WINDOWS.md` 与本文有少量重叠：前者面向 Windows 用户的操作与排障，本文只对照命令面。
