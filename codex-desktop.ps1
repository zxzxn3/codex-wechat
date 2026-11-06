#requires -Version 5.1
# Stable dispatch only. Forward native CLI arguments without proxy manipulation.
$ErrorActionPreference = 'Stop'
if ($env:CODEX_WECHAT_ALLOW_ROOT -ne '1') {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            [Console]::Error.WriteLine('请以普通用户身份运行，不要使用 root/sudo 或管理员终端。')
            exit 78
        }
    } catch {
        # A locked-down shell refusing the token check must not block normal use.
    }
}
$installRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} elseif ((Split-Path -Leaf (Split-Path -Parent $PSScriptRoot)) -eq 'releases') {
    Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
} else { $PSScriptRoot }
$project = $installRoot
$current = Join-Path $installRoot 'current'
if (-not (Test-Path -LiteralPath $current)) {
    throw "codex-desktop.ps1: 当前版本指针 current 缺失: $current"
}
if (Test-Path -LiteralPath $current) {
    $item = Get-Item -LiteralPath $current -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "codex-desktop.ps1: 当前版本指针 current 不能是符号链接或重解析点: $current"
    }
    if ($item.PSIsContainer) {
        throw "codex-desktop.ps1: 当前版本指针 current 必须是普通文件: $current"
    }
    $identifier = (Get-Content -LiteralPath $current -Raw -Encoding UTF8).Trim()
    if ($identifier -cnotmatch '^[a-f0-9]{16}$') {
        throw "codex-desktop.ps1: 当前版本指针 current 包含无效版本标识: $current"
    }
    $project = Join-Path $installRoot ('releases\' + $identifier)
}
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $native = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $native) { throw 'Python runtime is unavailable.' }
    $python = $native.Source
}
$script = Join-Path $project 'codex_wechat\desktop.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    throw "codex-desktop.ps1: 当前版本不完整: $project`n请从新源码重新运行 update。"
}
$before = [Environment]::GetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT','Process')
$beforePythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH','Process')
try {
    $env:CODEX_WECHAT_INSTALL_ROOT = $installRoot
    $env:PYTHONPATH = if ($beforePythonPath) {
        "$project;$beforePythonPath"
    } else {
        $project
    }
    & $python -u -m codex_wechat.desktop @args
    $result = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT',$before,'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH',$beforePythonPath,'Process')
}
exit $result
