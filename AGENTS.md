# 协作约定（给编码代理与维护者）

本仓的主开发在 Linux，Windows 机器是验收环境。下面的每一条都来自实际踩过的坑，
不是风格偏好；改代码前先读一遍，能省掉一轮返工。

## 在本仓工作前

- 验证优先于阅读：结论要有命令输出、测试结果或日志为证。只读代码得出的判断，写进回答前
  先用一条命令确认（本仓已经出现过"读代码以为正确、实测行为相反"的情况）。
- 一次只改一个主题；每条结论都要能对应到一个可复现的检查。
- 改动同时要满足：`python -m unittest discover -s tests` 全绿 + `ruff check .` 干净。

## Windows 上的编码陷阱（最贵的一类）

### 不要把中文通过 stdin 喂给 `python -`

PowerShell 的 heredoc（`@'...'@ | python -`）会把中文按控制台代码页解码，
中文直接变成 `?`：

```powershell
# 错误：写入文件后是 self.assertIn("??????: ...")，断言永远失败/假通过
@'
print("程序文件仍在")
'@ | python -
```

- **改文件**：用编辑器工具直接写，不要经过管道。
- **校验中文**：脚本里的比较字符串用转义序列，避免依赖终端编码。

```python
needle = "\u7a0b\u5e8f\u6587\u4ef6\u4ecd\u5728"   # 程序文件仍在
assert needle in text
```

- 判断"文件里到底有没有这段中文"时，`grep` 命中的乱码输出不可信；用上面的转义比对，
  或直接读文件（`view`）看内容。

### 含中文的 `.ps1` 必须是 UTF-8 BOM

`install.ps1`、`uninstall.ps1`、`codex-wechat.ps1`、`codex-desktop.ps1` 都靠 BOM 让
PowerShell 5.1 正确解码中文。每次改完核对前三字节：

```powershell
$bytes = [IO.File]::ReadAllBytes((Join-Path (Get-Location) "uninstall.ps1"))
($bytes[0..2] -join ',') -eq '239,187,191'   # True
```

改完 `.ps1` 还要过一遍语法树，别等运行时才发现引号没闭合：

```powershell
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
```

### 写 `docs/*.md` 用显式 UTF-8

用 Python 写时始终 `write_text(..., encoding="utf-8")`，不要依赖默认编码，也不要让中文
经过 stdin。

## 提交与命令

- `git commit -m "多行消息"` 在本机 PowerShell 会被拆散成多个参数。用消息文件：

  ```powershell
  python - <<'PY' 写文件避免中文（见上一节），或直接用编辑器写 $env:TEMP\msg.txt
  git commit -F $env:TEMP\msg.txt
  ```

- 本机 PowerShell 是 5.1：**不支持** `&&`、`||`、`??`、`??=`、`?.`、`?[`。用 `;` 串联、
  用 `if ($?) { ... }` 做条件。
- 读取仓库状态时禁用分页器：`git --no-pager log`。

## 测试

- 单个文件：`python -m unittest discover -s tests -p "test_x.py"`。
  两种跑法都要能过：`discover -s tests` 与
  `-m unittest tests.<主题目录>.test_x`（`tests/__init__.py` 只是包标记）。
- 诊断日志已被隔离：`tests/support/helpers.py` 在导入时把 `diagnostics` 指向临时 sink，并在每次
  `reset()` 后重新套用。**不要**在模块级再调 `diagnostics.reset()` 之后不加回指，
  否则夹具事件会写进开发者真实的 `logs/bridge.jsonl`——那份文件是事故证据。
- 跨平台夹具不要硬编码 `/tmp` 或某个平台的默认目录：用 `tempfile`，需要模拟 Linux 分支时用
  `tests.support.helpers.xdg_user_data_dir`。
- 卸载/安装这类破坏性路径用 mock 与临时目录验证，**不要**在真机上跑真实卸载来"看看输出"。

## 文档

- 行为变更要同时更新 `docs/` 对应页（架构、命令、安装、配置、交付、诊断和验证最常用），
  并在 `VALIDATION.md` 里区分"已实机验证"与"仅离线单测"。
- 已实测的数字（测试数、耗时、日志行数）写进文档时要注明日期与平台。
