# 开发与检查

本文是 codex-wechat 的开发入口；运行契约见 [ARCHITECTURE](ARCHITECTURE.md) 及其关联的
[CONFIGURATION](CONFIGURATION.md)、[DELIVERY](DELIVERY.md)、[DIAGNOSTICS](DIAGNOSTICS.md)，
能力边界和支持面见 [SUPPORT-MATRIX](SUPPORT-MATRIX.md)，命令词表与平台差异见
[COMMANDS](COMMANDS.md)，外部依据见 [SOURCES](SOURCES.md)。跨平台协作约定与已踩过的坑
（Windows 编码、`.ps1` BOM、提交姿势、测试隔离）见仓库根的 [AGENTS](../AGENTS.md)。

## 兼容策略

当前发布版本不承诺兼容旧开发版的安装布局、配置、持久状态、命令或内部实现；旧开发数据
按全新安装处理。

从当前公开版本起，后续版本必须兼容用户已部署的配置与持久状态、安装/升级/卸载路径、
用户可见命令与配置语义，以及有外部依赖的文件格式。内部模块和实现可以自由重构，不属于
兼容契约；上游 Codex/微信接口和当前版本化更新机制始终需要保持兼容。

## 离线测试

```bash
repo=$(pwd)
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git archive HEAD | tar -x -f - -C "$src"
cd "$src"
CODEX_WECHAT_INSTALL_ROOT="$src" CODEX_WECHAT_DATA_ROOT="$(mktemp -d)" \
XDG_CONFIG_HOME="$src/test-config" XDG_STATE_HOME="$src/test-state" \
PYTHONDONTWRITEBYTECODE=1 "$repo/.venv/bin/python" -m unittest discover -s tests -v
```

诊断日志默认写在 `runtime_paths().logs_dir/bridge.jsonl`。Linux 默认是
`$XDG_STATE_HOME/codex-wechat/logs`，Windows 保持数据根下的 `logs`。
`tests/support/helpers.py` 在导入时把
`diagnostics` 指向临时 sink，并在每次 `reset()` 后重新套用，所以直接跑测试不会把夹具事件
写进已安装实例正在使用的日志；上面的隔离目录仍然推荐，因为它同时挡住其它副作用。这里把源码
导出到没有 `.data-root` 的临时根，避免源码树本身是安装根时与新的 fail-closed 冲突。
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
[SUPPORT-MATRIX](SUPPORT-MATRIX.md) 中的两侧能力；文档、源码、离线结果与实机证据分别报告，
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

运行时依赖在 `requirements.txt` 中用兼容范围声明，安装器不锁死到单一版本；升级依赖后必须
跑完整离线测试。`curl-cffi` 当前要求 `>=0.17,<0.18`，0.17.0 已通过完整测试并包含安全修复。
`pywin32` 尚未在 Windows 实机复核，暂不收紧版本。开发工具在 `requirements-dev.txt` 中固定
版本，避免不同机器上的 coverage/pip-audit 结果漂移。安装器在 release 环境内用
`packaging` 的 PEP 440 `Version` / `SpecifierSet` 校验直接依赖；预发布、post 和本地版本
仍视为不稳定，不允许通过。默认用户数据目录由 `platformdirs.user_data_dir` 计算；stable
入口在 release 环境建立前缺少该包时，使用等价的 XDG / `LOCALAPPDATA` fallback。

更新运行时依赖使用 `.venv/bin/python -m pip install -r requirements.txt`；更新后需要重启
服务才会让新依赖生效，运行中的进程不会自动换包。

## 变更检查

- 改持久状态：同步恢复逻辑和恢复测试；发布版本变更时维护跨版本迁移与旧格式 fixture。
- 改协议字段：同步 SOURCES、ARCHITECTURE、诊断事件和失败边界。
- 改提示或配置：先读 [USER-MESSAGES](USER-MESSAGES.md)，保持 `prompts.py` 默认目录和
  英文 snake_case 字段；无效配置整组保留旧值。
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

注意：在克隆目录里直接跑 `.\codex-wechat.ps1 update` 会把这个克隆当成安装根，于是要求一个
"已保存的更新源"（报 `尚未配置更新来源`），随后即使启动服务也会被任务归属校验拒绝
（`scheduled task 'CodexWeChat' is not owned by this project`）。那两条报错都是预期行为——
一个克隆不是一次安装，安装/重启服务要用 `$app` 下的稳定入口。

## 重构边界

见 [COMPLEXITY](COMPLEXITY.md)：优先解决已观察到的边界问题，不按行数拆文件，不为跨平台
强行统一平台适配层。

安装器必须保持单文件可执行，网络 bootstrap 只下载 `installer.py`；运行期模块位于
`codex_wechat/`，包内使用相对导入。
`tests/install_and_release/test_release_layout.py` 会检查 stable payload
是否包含完整包文件，并验证脱离源码树的导入。

## 审查与发布退出标准

- P0/P1 必须修复；P2/P3 只有在支持矩阵内的主路径、数据损坏或安全边界上才阻断发布，
  其余进入有记录的 backlog，不追求“审查到零 findings”。
- 支持矩阵内的安装、更新、服务启动和卸载必须有对应层级的安装后烟测或明确实机证据；
  stable payload/import 烟测只覆盖布局与导入，不等于完整安装或服务验收；源码树测试、
  mock 和 Linux 上的 PowerShell 静态检查不能替代平台验收。
- 未在真实平台验证的能力只能标为“待验证/实验性”，不能写成已验收；无法保证的配置在安装
  或启动时 fail closed。
- 文档、命令表和 VALIDATION 必须与当前树同步；完成以上条件后停止继续穷举边缘分支。
