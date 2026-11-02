#requires -Version 5.1
# Stable dispatch only. Forward native CLI arguments without proxy manipulation.
$ErrorActionPreference = 'Stop'
$installRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} elseif ((Split-Path -Leaf (Split-Path -Parent $PSScriptRoot)) -eq 'releases') {
    Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
} else { $PSScriptRoot }
$project = $installRoot
$current = Join-Path $installRoot 'current'
if (Test-Path -LiteralPath $current) {
    $item = Get-Item -LiteralPath $current -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid current pointer.' }
    $identifier = (Get-Content -LiteralPath $current -Raw -Encoding UTF8).Trim()
    if ($identifier -notmatch '^[a-f0-9]{16}$') { throw 'Invalid current version.' }
    $project = Join-Path $installRoot ('releases\' + $identifier)
}
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $native = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $native) { throw 'Python runtime is unavailable.' }
    $python = $native.Source
}
$script = Join-Path $project 'desktop.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    # A failed update may have prepared a newer stable entry before activation.
    # An older App-capable release still owns its own compatible dispatcher.
    $legacy = Join-Path $project 'codex-desktop.ps1'
    if ($project -ne $installRoot -and (Test-Path -LiteralPath $legacy -PathType Leaf)) {
        & $legacy @args
        exit $LASTEXITCODE
    }
    throw 'Desktop entry is missing.'
}
$before = [Environment]::GetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT','Process')
try {
    $env:CODEX_WECHAT_INSTALL_ROOT = $installRoot
    & $python -u $script @args
    $result = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('CODEX_WECHAT_INSTALL_ROOT',$before,'Process')
}
exit $result
