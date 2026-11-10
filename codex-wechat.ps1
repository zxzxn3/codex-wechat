#requires -Version 5.1
[CmdletBinding()]
param(
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
    [string]$Repo,
    [string]$Ref,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [switch]$StopActive,
    [string]$Component,
    [string]$SkillsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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

function Read-SavedDataRoot([string]$Root) {
    $Marker = Join-Path $Root '.data-root'
    $Item = Get-Item -LiteralPath $Marker -Force -ErrorAction SilentlyContinue
    if (-not $Item) { return $null }
    if ($Item.PSIsContainer -or
            (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "数据根标记不是普通文件: $Marker"
    }
    try {
        $Value = Get-Content -LiteralPath $Marker -Raw -Encoding UTF8 -ErrorAction Stop
    } catch {
        throw "无法读取数据根标记: $Marker"
    }
    $Value = ([string]$Value).TrimEnd([char[]]"`r`n")
    if ([string]::IsNullOrWhiteSpace($Value) -or
            $Value.Contains("`n") -or $Value.Contains("`r") -or
            -not [IO.Path]::IsPathRooted($Value)) {
        throw "数据根标记内容无效: $Marker"
    }
    return [IO.Path]::GetFullPath($Value)
}

$InstallerCommand = $Command -in @('update', 'install', 'menu', 'components')
$SavedDataRoot = if ($InstallerCommand -and $env:CODEX_WECHAT_DATA_ROOT) {
    $null
} else {
    Read-SavedDataRoot $InstallRoot
}
if ($SavedDataRoot) {
    if (-not $env:CODEX_WECHAT_DATA_ROOT) {
        $env:CODEX_WECHAT_DATA_ROOT = $SavedDataRoot
    } elseif (-not $InstallerCommand -and
            [IO.Path]::GetFullPath($env:CODEX_WECHAT_DATA_ROOT) -ne $SavedDataRoot) {
        throw 'CODEX_WECHAT_DATA_ROOT 与安装根 .data-root 不一致；Windows 已安装数据根不能自动切换，请清除环境变量'
    }
}
$InstalledCurrent = Join-Path $InstallRoot 'current'
if (-not $SavedDataRoot -and -not $InstallerCommand -and $Command -ne 'uninstall' -and
        (Test-Path -LiteralPath $InstalledCurrent)) {
    throw '已安装布局缺少数据根标记；请重新运行安装器。'
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
    if ($DryRun) { $InstallerArguments += '--dry-run' }
    if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    if ($Component) { $InstallerArguments += @('--component', $Component) }
    if ($SkillsDir) { $InstallerArguments += @('--skills-dir', $SkillsDir) }
    & $UpdatePython @InstallerArguments
    exit $LASTEXITCODE
}

if ($Repo -or $Ref -or $DryRun -or $NonInteractive -or $Component -or $SkillsDir) {
    throw '-Repo, -Ref, -DryRun, -NonInteractive, -Component and -SkillsDir are only valid with installer commands'
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
    $env:CODEX_WECHAT_INSTALL_ROOT = $InstallRoot
    $global:LASTEXITCODE = 0
    & $Uninstaller @UninstallerArguments
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    exit 0
}

$Project = $InstallRoot
$CurrentFile = Join-Path $InstallRoot 'current'
$CurrentItem = Get-Item -LiteralPath $CurrentFile -Force -ErrorAction SilentlyContinue
if ($null -eq $CurrentItem) {
    throw "codex-wechat.ps1: 当前版本指针 current 缺失: $CurrentFile"
} else {
    if ($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "codex-wechat.ps1: 当前版本指针 current 不能是符号链接或重解析点: $CurrentFile"
    }
    if ($CurrentItem.PSIsContainer) {
        throw "codex-wechat.ps1: 当前版本指针 current 必须是普通文件: $CurrentFile"
    }
    $ReleaseId = (Get-Content -LiteralPath $CurrentFile -TotalCount 1).Trim()
    if (-not $ReleaseId -or $ReleaseId -cnotmatch '^[a-f0-9]{16}$') {
        throw "codex-wechat.ps1: 当前版本指针 current 包含无效版本标识: $CurrentFile"
    }
    $Project = Join-Path $InstallRoot "releases\$ReleaseId"
}
if (-not (Test-Path -LiteralPath (Join-Path $Project 'codex_wechat\app.py') -PathType Leaf)) {
    throw "codex-wechat.ps1: 当前版本不完整: $Project`n请从新源码重新运行 update。"
}

$env:CODEX_WECHAT_INSTALL_ROOT = $InstallRoot
$DataRoot = if ($env:CODEX_WECHAT_DATA_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_DATA_ROOT)
} else {
    Join-Path $env:LOCALAPPDATA 'codex-wechat'
}
$StateDir = Join-Path $DataRoot '.state'
$ConfigPath = Join-Path $DataRoot 'config.json'
$VenvPython = Join-Path $Project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "Python environment not found at $VenvPython; run .\install.ps1 first"
}

$Arguments = @('-m', 'codex_wechat', $Command, '--state-dir', $StateDir, '--config', $ConfigPath)
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
        "$Project;$PreviousPythonPath"
    } else {
        $Project
    }
    & $VenvPython -u @Arguments
    $Result = $LASTEXITCODE
} finally {
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
}
exit $Result
