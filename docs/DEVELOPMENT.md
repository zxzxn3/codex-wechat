# 开发与检查

本文是 codex-wechat 的开发入口；运行契约见 [ARCHITECTURE](ARCHITECTURE.md)，平台操作和
支持面见 [PLATFORMS](PLATFORMS.md)，命令入口见 [COMMANDS](COMMANDS.md)，外部依据见
[SOURCES](SOURCES.md)。跨平台协作约定与已踩过的坑
（Windows 编码、`.ps1` BOM、提交姿势、测试隔离）见仓库根的 [AGENTS](../AGENTS.md)。

## 兼容策略

当前发布版本不承诺兼容旧开发版的安装布局、配置、持久状态、命令或内部实现；旧开发数据
按全新安装处理。

从当前公开版本起，后续版本必须兼容用户已部署的配置与持久状态、安装/升级/卸载路径、
用户可见命令与配置语义，以及有外部依赖的文件格式。内部模块和实现可以自由重构，不属于
兼容契约；上游 Codex/微信接口和当前版本化更新机制始终需要保持兼容。

## 仓库与分发

`codex-wechat` 是独立 Git 仓库，默认位于 `~/.local/share/codex-wechat`，不属于 cfg
工作树。当前远端为 `https://github.com/zxzxn3/codex-wechat.git`；公开入口直接读取该仓库
`main` 分支的 bootstrap，不需要额外引导库、GitHub 登录或访问凭据。Windows 开发从同一远端
取得源码，切换环境前先同步，避免两份源码分叉。

网络 bootstrap 先以 uv 管理的 Python 运行下载到临时目录的安装器，再由安装器下载公开
GitHub tar 归档并执行安装；Linux、Windows 入口最终调用同一个 `installer.py`。宿主不需要
Python、pip 或 Git。依赖声明集中在 requirements 文件，二维码与附件 skill 只按需安装。
完整用户流程见 [INSTALL](INSTALL.md)。

新旧 release 按各自清单验证，不用新排除规则重新定义旧版身份。下载、构建与原子写临时文件
会清理；仍可能被进程使用的旧版本和已保存附件不作为临时垃圾自动删除。发布产物不得包含用户
账户、状态、代理、工作目录、日志或 `.venv`。

源码目录含 Git 元数据时，安装器从 `HEAD` 生成发布文件集合：未跟踪文件不会进入 release，
已跟踪但未提交的修改会被拒绝。这是发布可复现性边界，不是对工作树的通用打包。没有 `.git`
的源码快照（例如下载的 tar 包）按快照内全部非私有文件构建。

## 离线测试

```bash
repo=$(pwd)
sandbox=$(mktemp -d)
src="$sandbox/src"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$src"
git archive HEAD | tar -x -f - -C "$src"
cd "$src"
CODEX_WECHAT_INSTALL_ROOT="$src" CODEX_WECHAT_DATA_ROOT="$sandbox/data" \
XDG_CONFIG_HOME="$sandbox/config" XDG_STATE_HOME="$sandbox/state" \
PYTHONDONTWRITEBYTECODE=1 "$repo/.venv/bin/python" -m unittest discover -s tests -v
```

诊断日志默认写在 `runtime_paths().logs_dir/bridge.jsonl`。Linux 默认是
`$XDG_STATE_HOME/codex-wechat/logs`，Windows 保持数据根下的 `logs`。
`tests/support/helpers.py` 在导入时把
`diagnostics` 指向临时 sink，并在每次 `reset()` 后重新套用，所以直接跑测试不会把夹具事件
写进已安装实例正在使用的日志；上面的隔离目录仍然推荐，因为它同时挡住其它副作用。这里把源码
导出到没有 `.installation.json` 的临时根，避免源码树本身被识别为已安装布局。
Windows PowerShell 同样应使用源码副本，并设置 `CODEX_WECHAT_INSTALL_ROOT` 指向该副本：

```powershell
$repo = (Get-Location).Path
$src = Join-Path $env:TEMP ("cw-tests-" + [guid]::NewGuid().ToString("N"))
$archive = Join-Path $env:TEMP ("cw-src-" + [guid]::NewGuid().ToString("N") + ".tar")
New-Item -ItemType Directory -Path $src | Out-Null
git archive --format=tar --output="$archive" HEAD
tar -xf $archive -C $src
Set-Location $src
$env:CODEX_WECHAT_INSTALL_ROOT = $src
$env:CODEX_WECHAT_DATA_ROOT = Join-Path $env:TEMP ("cw-tests-" + [guid]::NewGuid())
$env:PYTHONDONTWRITEBYTECODE = "1"
py -3 -m unittest discover -s tests -v
Set-Location $repo
Remove-Item -LiteralPath $archive, $src -Recurse -Force -ErrorAction SilentlyContinue
```

Windows、真实微信/CDN、App/daemon 联调不属于离线测试；mock 通过不能写成真实平台或端到端通过。
相关文档入口、版本和已确认约束应在派发测试时一并交接。

指定测试会话从固定源码副本执行。全部状态、配置、进程与 RPC 均使用临时目录或模拟，
不接入生产账户、服务、模型和微信。除测试通过外，独立核对
[PLATFORMS](PLATFORMS.md) 中的两侧支持面；文档、源码、离线结果与实机证据分别报告，
无法验证的组合不写成通过。协议来源见 [SOURCES](SOURCES.md)。

## 静态检查、覆盖率与依赖审计

在已有的运行时 `.venv` 中只安装开发工具；它们不会进入运行时安装：

```bash
cd ~/.local/share/codex-wechat
.venv/bin/python -m pip install -r requirements-dev.txt
```

新建开发环境时，先安装 `requirements.txt`，再安装 `requirements-dev.txt`。

`ruff`、`pyright`、`shellcheck` 仍由系统提供，版本以 `--version` 为准。一键检查：

```bash
scripts/check.sh
scripts/check.sh --audit   # 额外审计 requirements，需要网络
```

脚本依次运行 ruff、pyright、shellcheck 和带分支覆盖的完整单元测试，最后输出覆盖率。
`--audit` 再运行 pip-audit，检查 `requirements.txt` 和 `requirements-qr.txt` 的已知漏洞。
它审计的是声明版本；当前 `.venv` 是否已同步，可用
`.venv/bin/python -m pip_audit --local` 单独确认。

coverage 不是测试生成器，而是记录测试执行时哪些行和分支从未走到。不要把某个时点的覆盖率
写死成长期基线；先看报告定位错误恢复、附件失败和平台分支的缺口，不设硬性通过门槛。
需要逐行查看时运行
`.venv/bin/python -m coverage html`，产物 `htmlcov/` 已被忽略且不会进入发行包。

`pyproject.toml` 固定 ruff 的 Python 3.11 目标、行宽和规则集，并保存 coverage 的分支/排除
配置；`pyrightconfig.json` 覆盖 `codex_wechat/` 与 `tests/`，固定基础检查级别和 `.venv`。不运行
`ruff format` 大面积改样式。

Linux 上 `codex_wechat/windows_service.py` 的 pywin32 导入和 `.venv` 未包含的 `cryptography` 会产生
missing-import 警告；这些是平台/环境边界，不是代码错误。平台条件导入、第三方类型 stub、
动态 dict 推断的误报用定点 `# pyright: ignore[...]`，不全局关闭检查。

## 依赖版本

依赖版本只以 `requirements*.txt` 为准，不在文档复制版本范围。升级依赖后必须跑完整离线
测试；安装器会在 release 环境内校验声明的版本。默认运行时根由
`install_layout.default_roots()` 计算；已安装实例只读取 `.installation.json`，不根据当前
shell 的 XDG 或 `LOCALAPPDATA` 重新推断。

更新运行时依赖后需要重新运行安装器，让新 release 在 staging 中由 uv 建立环境；重启服务后
才会加载新依赖，运行中的进程不会自动换包。

## 变更检查

- 改持久状态：同步恢复逻辑和恢复测试；发布版本变更时维护跨版本迁移与旧格式 fixture。
- 改协议字段：同步 SOURCES、ARCHITECTURE、诊断事件和失败边界。
- 改提示或配置：先读本文的“用户可见文案”，保持 `prompts.py` 默认目录和英文 snake_case
  字段；无效配置整组保留旧值。
- 改 Windows/服务/安装：至少做静态入口检查；有实机时再跑真实回归。
- 改 `*.ps1`：`codex-wechat.ps1`、`codex-desktop.ps1`、`install.ps1` 与 `uninstall.ps1`
  含中文，必须保持 **UTF-8 with BOM**。
  Windows PowerShell 5.1 对无 BOM 的脚本按 ANSI 解码，去掉 BOM 会让整份文件解析失败
  （表现为一串无关的 "字符串缺少终止符"）。`windows/task-service.ps1` 是纯 ASCII，不需要
  BOM；用 .NET 的 `WriteAllText(..., UTF8Encoding($false))` 之类写回时特别容易漏掉这一点。
- 本项目是独立 Git 仓库；提交按主题拆分，默认使用英文 Conventional Commits。AI 参与按
  `AGENTS.md` 使用 Codex 或 Copilot 的原生 `Co-authored-by` 身份，并用 `AI-Model` 记录
  有可靠依据的实际模型；不再使用旧的自定义 footer。

README 的 12 小时活动图由 `scripts/churn_chart.py` 从完整 Git 历史和仓库中的 Python 文件生成。
`.github/workflows/update-activity-chart.yml` 每天定时或手动运行，仅在 SVG 内容变化时提交。
图表以 2 小时无提交作为会话断点，统计值会随仓库历史更新；不要手工编辑生成的 SVG。

改完安装器或入口脚本后，用本地克隆装一遍再验服务：

```powershell
$app = "$env:LOCALAPPDATA\codex-wechat\app"
& "$app\.bootstrap-venv\Scripts\python.exe" installer.py update --source . --dir $app --non-interactive
powershell -NoProfile -ExecutionPolicy Bypass -File "$app\codex-wechat.ps1" service-restart
```

注意：在克隆目录里直接跑 `.\codex-wechat.ps1 update` 会把这个克隆当成安装根；一个克隆不是
一次安装，安装/重启服务要用 `$app` 下的稳定入口。普通 stable 更新固定读取项目官方
GitHub `main`，不会读取或保存自定义仓库、分支或更新来源文件。

## 复杂度与重构边界

下表是按职责边界判断的维护成本，不是工时预算或代码占比。能力范围见
[PLATFORMS](PLATFORMS.md)，运行契约见 [ARCHITECTURE](ARCHITECTURE.md)。

| 部分 | 成本 | 主要负担 |
| --- | --- | --- |
| 桌面活动会话共享 | 高 | 多客户端、早到/迟到事件、请求归属、轮次竞争、App 内部接口变化 |
| 交付与重连 | 高 | 提交结果不确定、崩溃恢复、去重、输出顺序及部分失败 |
| 安装与更新 | 高 | 环境差异、版本构建、旧版清单、稳定入口和跨平台服务注册 |
| Windows 托管 | 中高 | 计划任务、进程/Job Object、单实例、退出码、通知及实机验证 |
| 媒体收发 | 中高 | CDN、加解密、快照、大小限制、文件权限和部分失败 |
| 重绑与认证 | 中 | 扫码/服务并发、同号保留交付、换号隔离、半完成状态 |
| 原生问题/审批 | 中 | 实际接收连接和回答归属、桌面与微信并发操作 |
| 命令、提示、切片和状态 | 低至中 | 主要复用原生协议、快照与纯函数；网络探测另有子进程 |

普通 CLI 和 App 共享大部分协议、连接与交付代码。App 内置 CLI 发现，以及接入桌面正在
使用的会话，是两件不同的事；后者承担更多状态边界。

- 单独去掉 Linux App 只能减少很薄的发现/入口代码；Windows App 仍需要公共共享逻辑。
- 去掉 Windows App 可减少一部分内部接口维护，但若仍支持 Windows CLI，托管、安装和进程
  层仍要保留。
- 去掉整个 Windows 平台、桌面活动会话共享或媒体能力才会明显减负，同时也有直接功能损失。
- 单独去掉一个后端名称、短命令或模板变量，收益很小。

重构优先解决已观察到的边界问题，不按行数拆文件，不为跨平台强行统一平台适配层。这些取舍
不代表已取消任何能力。

安装器必须保持单文件可执行，网络 bootstrap 只下载 `installer.py`；运行期模块位于
`codex_wechat/`，包内使用相对导入。
`tests/install_and_release/test_release_layout.py` 会检查 stable payload
是否包含完整包文件，并验证脱离源码树的导入。

## 用户可见文案

- 先回答用户要求的结果，再给可行动信息；成功回执不叙述队列、worker、writer 或重连实现。
- 可能重复执行、静默丢失、旧连接未清理、能力回落或需要桌面处理的风险必须保留。
- 设置成功显示生效后的当前值；失败给出下一步。参数形态不明显时，错误提示可以附一个例子。
- `/new`、`/switch` 只显示会话结果，不重复播报旧回合 detach 和旧输入输出作废。
- 生命周期通知优先于额外的“请求已提交”回执；默认文案只在 `prompts.py` 维护。

## 审查与发布退出标准

- P0/P1 必须修复；P2/P3 只有在支持矩阵内的主路径、数据损坏或安全边界上才阻断发布，
  其余进入有记录的 backlog，不追求“审查到零 findings”。
- 支持矩阵内的安装、更新、服务启动和卸载必须有对应层级的安装后烟测或明确实机证据；
  stable payload/import 烟测只覆盖布局与导入，不等于完整安装或服务验收；源码树测试、
  mock 和 Linux 上的 PowerShell 静态检查不能替代平台验收。
- 未在真实平台验证的能力只能标为“待验证/实验性”，不能写成已验收；无法保证的配置在安装
  或启动时 fail closed。
- 文档、生成参考和 VALIDATION 必须与当前树同步；完成以上条件后停止继续穷举边缘分支。

### 开源发布门

发布新版本或首次公开仓库时，维护者需要确认：

- `LICENSE` 为 Apache-2.0，`NOTICE`、`TRADEMARKS.md` 和品牌素材说明存在；mascot 与代码
  分开许可，并按 CC BY-NC-SA 4.0 标注来源和修改。
- README、安装文档和 bootstrap 使用无需认证的公开入口；截图、二维码、路径和终端输出已
  审阅，不含未获同意公开的个人信息。
- `.env`、`config.json`、`.state/`、`logs/`、`workspace/`、用户状态目录、`releases/`、
  私钥和证书从未进入任何提交。完整历史和提交邮箱已检查；已撤销的 PAT 继续保留在历史中。
- 代码、Logo、角色图片、字体、图标、传递依赖和 Windows 运行时许可证已复核；项目自身
  不携带字体文件。第三方 Actions 固定到提交 SHA，依赖审计无已知漏洞。
- 在隔离环境中完成安装、状态、更新、配置保留和卸载烟测，并重新 clone 干净副本运行完整
  测试、静态检查和安装烟测。

公开前还要验证公开 raw 地址、源码归档下载和更新源无需 GitHub 登录，检查 GitHub 许可证
识别、README 渲染、图片和 Actions 状态。公开后继续监控 secret scanning、issues 和安全
报告。
