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
$python = Join-Path $installRoot '.bootstrap-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $native = @(Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue) |
        Where-Object {
            (Test-Path -LiteralPath $_.Source -PathType Leaf) -and
            (Get-Item -LiteralPath $_.Source).Length -gt 0
        } | Select-Object -First 1
    if (-not $native) { throw 'Python runtime is unavailable.' }
    $python = $native.Source
}
$before = [Environment]::GetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT','Process')
$beforePythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH','Process')
try {
    $env:CODEX_WECHAT_INSTALL_ROOT = $installRoot
    $env:PYTHONPATH = if ($beforePythonPath) {
        "$installRoot;$beforePythonPath"
    } else {
        $installRoot
    }
    & $python -u -m codex_wechat.launcher --install-root $installRoot `
        --module codex_wechat.desktop -- @args
    $result = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT',$before,'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH',$beforePythonPath,'Process')
}
exit $result
