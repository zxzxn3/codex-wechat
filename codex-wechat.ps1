#requires -Version 5.1
[CmdletBinding()]
param(
    [Alias('h')]
    [switch]$Help,
    [Parameter(Position = 0)]
    [ValidateSet('login', 'rebind', 'start', 'stop', 'status', 'service-start',
                 'service-restart', 'autostart', 'logs', 'uninstall',
                 'update', 'install', 'menu', 'components')]
    [string]$Command = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('true', 'false')]
    [string]$Value,
    [switch]$Follow,
    [switch]$Service,
    [switch]$PurgeData,
    [switch]$Force,
    [string]$Repo,
    [string]$Ref,
    [string]$Source,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [switch]$StopActive,
    [string]$Component,
    [string]$SkillsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Host 'Usage: codex-wechat <command> [options]'
    Write-Host 'Commands: login, start, stop, status, service-start, service-restart, autostart, logs, uninstall, update, install, menu, components'
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

function Invoke-IsolatedPython {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [AllowNull()][string]$SearchPath,
        [switch]$IgnoreEnvironment,
        [Parameter(Mandatory = $true)][object[]]$Arguments,
        [Parameter(Mandatory = $true)][ref]$ExitCode
    )
    $previousPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
    $previousHome = [Environment]::GetEnvironmentVariable('PYTHONHOME', 'Process')
    try {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
        if ($IgnoreEnvironment) {
            & $Python -I @Arguments
        } else {
            $env:PYTHONPATH = $SearchPath
            & $Python -s -P @Arguments
        }
        $ExitCode.Value = $LASTEXITCODE
    } finally {
        if ($null -eq $previousPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONPATH = $previousPath
        }
        if ($null -eq $previousHome) {
            Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONHOME = $previousHome
        }
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

if ($Command -in @('update', 'install', 'menu', 'components')) {
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
        $PythonCommand = @(Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue) |
            Where-Object {
                (Test-Path -LiteralPath $_.Source -PathType Leaf) -and
                (Get-Item -LiteralPath $_.Source).Length -gt 0
            } | Select-Object -First 1
        if (-not $PythonCommand) { throw 'Python is unavailable; run py -3 installer.py from new source' }
        $UpdatePython = $PythonCommand.Source
    }
    $InstallerArguments = @($Installer, $Command, '--dir', $InstallRoot)
    if ($Repo) { $InstallerArguments += @('--repo', $Repo) }
    if ($Ref) { $InstallerArguments += @('--ref', $Ref) }
    if ($Source) { $InstallerArguments += @('--source', $Source) }
    if ($DryRun) { $InstallerArguments += '--dry-run' }
    if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    if ($Component) { $InstallerArguments += @('--component', $Component) }
    if ($SkillsDir) { $InstallerArguments += @('--skills-dir', $SkillsDir) }
    $installerExit = 0
    Invoke-IsolatedPython -Python $UpdatePython -IgnoreEnvironment `
        -Arguments $InstallerArguments -ExitCode ([ref]$installerExit)
    exit $installerExit
}

if ($Repo -or $Ref -or $Source -or $DryRun -or $NonInteractive -or $Component -or $SkillsDir) {
    throw '-Repo, -Ref, -Source, -DryRun, -NonInteractive, -Component and -SkillsDir are only valid with installer commands'
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
    $PythonCommand = @(Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue) |
        Where-Object {
            (Test-Path -LiteralPath $_.Source -PathType Leaf) -and
            (Get-Item -LiteralPath $_.Source).Length -gt 0
        } | Select-Object -First 1
    if (-not $PythonCommand) { throw 'Python runtime is unavailable.' }
    $EntryPython = $PythonCommand.Source
}
$Arguments = @(
    '-m', 'codex_wechat.launcher',
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
$Result = 0
Invoke-IsolatedPython -Python $EntryPython -SearchPath $InstallRoot `
    -Arguments $Arguments -ExitCode ([ref]$Result)
exit $Result
