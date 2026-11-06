# 开发与检查

本文是 codex-wechat 的开发入口；运行契约见 [ARCHITECTURE](ARCHITECTURE.md)，能力边界见
[CAPABILITIES](CAPABILITIES.md)，支持面见 [SUPPORT-MATRIX](SUPPORT-MATRIX.md)，命令词表与
平台差异见 [COMMANDS](COMMANDS.md)，外部依据见 [SOURCES](SOURCES.md)。

## 兼容策略

项目尚未首次公开发布。发布前不承诺兼容旧开发版的安装布局、配置、持久状态、命令或内部
实现；当前树不保留迁移层，旧开发数据按全新安装处理。

首次公开发布后，后续版本必须兼容用户已部署的配置与持久状态、安装/升级/卸载路径、用户
可见命令与配置语义，以及有外部依赖的文件格式。内部模块和实现可以自由重构，不属于兼容
契约；上游 Codex/微信接口和当前版本化更新机制始终需要保持兼容。

## 离线测试

```bash
repo=$(pwd)
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git archive HEAD | tar -x -f - -C "$src"
cd "$src"
CODEX_WECHAT_INSTALL_ROOT="$src" CODEX_WECHAT_DATA_ROOT="$(mktemp -d)" \
PYTHONDONTWRITEBYTECODE=1 "$repo/.venv/bin/python" -m unittest discover -s tests -v
```

诊断日志默认写在 `data_root()/logs/bridge.jsonl`，不隔离会把测试事件混进已安装实例
正在使用的日志，真实问题的排查会被测试噪音污染。`CODEX_WECHAT_DATA_ROOT` 指向临时
目录即可，测试本身不依赖该目录内容。这里同时把源码导出到没有 `.data-root` 的临时根，
避免源码树本身是安装根时与新的 fail-closed 冲突。Windows PowerShell 同样应使用源码副本，
并设置 `CODEX_WECHAT_INSTALL_ROOT` 指向该副本：

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
配置；`pyrightconfig.json` 覆盖顶层模块与 `tests/`，固定基础检查级别和 `.venv`。不运行
`ruff format` 大面积改样式。

Linux 上 `windows_service.py` 的 pywin32 导入和 `.venv` 未包含的 `cryptography` 会产生
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

- 改持久状态：同步恢复逻辑和恢复测试；首次公开发布后，再维护跨版本迁移与旧格式 fixture。
- 改协议字段：同步 SOURCES、ARCHITECTURE、诊断事件和失败边界。
- 改提示或配置：保持 `prompts.py` 默认目录和英文 snake_case 字段；无效配置整组保留旧值。
- 改 Windows/服务/安装：至少做静态入口检查；有实机时再跑真实回归。
- 改 `*.ps1`：`codex-wechat.ps1`、`codex-desktop.ps1`、`install.ps1` 与 `uninstall.ps1`
  含中文，必须保持 **UTF-8 with BOM**。
  Windows PowerShell 5.1 对无 BOM 的脚本按 ANSI 解码，去掉 BOM 会让整份文件解析失败
  （表现为一串无关的 "字符串缺少终止符"）。`windows/task-service.ps1` 是纯 ASCII，不需要
  BOM；用 .NET 的 `WriteAllText(..., UTF8Encoding($false))` 之类写回时特别容易漏掉这一点。
- 本项目是独立 Git 仓库；提交按主题拆分，默认使用英文 Conventional Commits。cfg 相关提交
  才使用中文并附 `Committed-by` footer。

改完安装器或入口脚本后，用本地克隆装一遍再验服务：

```powershell
$app = "$env:LOCALAPPDATA\codex-wechat\app"
& "$app\.bootstrap-venv\Scripts\python.exe" installer.py update --source . --dir $app --non-interactive
& "$app\codex-wechat.ps1" service-restart
```

注意：在克隆目录里直接跑 `.\codex-wechat.ps1 update` 会把这个克隆当成安装根，于是要求一个
"已保存的更新源"（报 `尚未配置更新来源`），随后即使启动服务也会被任务归属校验拒绝
（`scheduled task 'CodexWeChat' is not owned by this project`）。那两条报错都是预期行为——
一个克隆不是一次安装，安装/重启服务要用 `$app` 下的稳定入口。

## 重构边界

见 [COMPLEXITY](COMPLEXITY.md)：优先解决已观察到的边界问题，不按行数拆文件，不为跨平台
强行统一平台适配层。

安装器必须保持单文件可执行，网络 bootstrap 只下载 `installer.py`；新增顶层本地导入
会破坏该入口。`tests/test_release_layout.py` 会检查各平台 stable payload 的顶层本地
导入闭包，新增 stable 模块或其依赖时必须同步清单。

## 审查与发布退出标准

- P0/P1 必须修复；P2/P3 只有在支持矩阵内的主路径、数据损坏或安全边界上才阻断发布，
  其余进入有记录的 backlog，不追求“审查到零 findings”。
- 支持矩阵内的安装、更新、服务启动和卸载必须有对应层级的安装后烟测或明确实机证据；
  stable payload/import 烟测只覆盖布局与导入，不等于完整安装或服务验收；源码树测试、
  mock 和 Linux 上的 PowerShell 静态检查不能替代平台验收。
- 未在真实平台验证的能力只能标为“待验证/实验性”，不能写成已验收；无法保证的配置在安装
  或启动时 fail closed。
- 文档、命令表和 VALIDATION 必须与当前树同步；完成以上条件后停止继续穷举边缘分支。
