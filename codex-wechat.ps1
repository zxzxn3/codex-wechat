#requires -Version 5.1
[CmdletBinding()]
param(
    [Alias('h')]
    [switch]$Help,
    [Parameter(Position = 0)]
    [ValidateSet('login', 'rebind', 'start', 'stop', 'status', 'service-start',
                 'service-restart', 'autostart', 'logs', 'uninstall',
                 'update', 'install', 'menu')]
    [string]$Command = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('true', 'false')]
    [string]$Value,
    [switch]$Follow,
    [switch]$Service,
    [switch]$PurgeData,
    [switch]$Force,
    [string]$Source,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [switch]$StopActive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Host 'Usage: codex-wechat <command> [options]'
    Write-Host 'Commands: login, start, stop, status, service-start, service-restart, autostart, logs, uninstall, update, install, menu'
    exit 0
}

function Test-Elevated {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

if ($Command -notin @('status', 'logs', 'uninstall') -and
        (Test-Elevated) -and $env:CODEX_WECHAT_ALLOW_ROOT -ne '1') {
    [Console]::Error.WriteLine('请以普通用户身份运行，不要使用 root/sudo 或管理员终端。')
    exit 78
}

$InstallRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} else {
    $PSScriptRoot
}

if ($Force -and $Command -ne 'uninstall') {
    throw '-Force is only valid with the uninstall command'
}

if ($Command -in @('update', 'install', 'menu')) {
    if ($PSBoundParameters.ContainsKey('Value') -or $Follow -or $Service -or $PurgeData -or
            $StopActive) {
        throw 'installer commands do not accept service or chat control parameters'
    }
    $Installer = Join-Path $InstallRoot 'installer.py'
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "stable installer missing: $Installer; run the installer from new source first"
    }
    $UpdatePython = Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $UpdatePython -PathType Leaf)) {
        throw 'Stable Python runtime is missing; rerun bootstrap as this user.'
    }
    $InstallerArguments = @($Installer, $Command, '--dir', $InstallRoot)
    if ($Source) { $InstallerArguments += @('--source', $Source) }
    if ($DryRun) { $InstallerArguments += '--dry-run' }
    if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    & $UpdatePython @InstallerArguments
    exit $LASTEXITCODE
}

if ($Source -or $DryRun -or $NonInteractive) {
    throw '-Source, -DryRun and -NonInteractive are only valid with installer commands'
}
if ($PSBoundParameters.ContainsKey('Value') -and $Command -ne 'autostart') {
    throw '-Value is only valid with the autostart command'
}
if ($Follow -or $Service) {
    if ($Command -ne 'logs') {
        throw '-Follow and -Service are only valid with the logs command'
    }
}
if ($StopActive -and $Command -ne 'rebind') {
    throw '-StopActive is only valid with the rebind command'
}
if ($PurgeData -and $Command -ne 'uninstall') {
    throw '-PurgeData is only valid with the uninstall command'
}

if ($Command -eq 'uninstall') {
    $Uninstaller = Join-Path $InstallRoot 'uninstall.ps1'
    if (-not (Test-Path -LiteralPath $Uninstaller -PathType Leaf)) {
        throw "stable uninstall helper is missing: $Uninstaller; run the installer from new source first"
    }
    # Bind by name: an array splat is positional and skips switches, so
    # @('-PurgeData') would be treated as a stray positional value.
    $UninstallerArguments = @{}
    if ($PurgeData) { $UninstallerArguments.PurgeData = $true }
    if ($Force) { $UninstallerArguments.Force = $true }
    $env:CODEX_WECHAT_INSTALL_ROOT = $InstallRoot
    $global:LASTEXITCODE = 0
    & $Uninstaller @UninstallerArguments
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    exit 0
}

$env:CODEX_WECHAT_INSTALL_ROOT = $InstallRoot
$EntryPython = Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $EntryPython -PathType Leaf)) {
    throw 'Stable Python runtime is missing; rerun bootstrap as this user.'
}
$Arguments = @(
    '-P', '-m', 'codex_wechat.launcher',
    '--install-root', $InstallRoot,
    '--module', 'codex_wechat',
    '--', $Command
)
if ($Command -eq 'logs') {
    if ($Follow) { $Arguments += '--follow' }
    if ($Service) { $Arguments += '--service' }
}
if ($Command -eq 'rebind' -and $StopActive) {
    $Arguments += '--stop-active'
}
if ($Command -eq 'autostart' -and $PSBoundParameters.ContainsKey('Value')) {
    $Arguments += $Value
}
$PreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = if ($PreviousPythonPath) {
        "$InstallRoot;$PreviousPythonPath"
    } else {
        $InstallRoot
    }
    & $EntryPython -u @Arguments
    $Result = $LASTEXITCODE
} finally {
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
}
exit $Result
