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
    throw 'Stable Python runtime is missing; rerun bootstrap as this user.'
}
$before = [Environment]::GetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT','Process')
$beforePythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH','Process')
$beforePythonHome = [Environment]::GetEnvironmentVariable('PYTHONHOME','Process')
try {
    $env:CODEX_WECHAT_INSTALL_ROOT = $installRoot
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PYTHONPATH = $installRoot
    & $python -s -P -u -m codex_wechat.launcher --install-root $installRoot `
        --module codex_wechat.desktop -- @args
    $result = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT',$before,'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH',$beforePythonPath,'Process')
    [Environment]::SetEnvironmentVariable('PYTHONHOME',$beforePythonHome,'Process')
}
exit $result
