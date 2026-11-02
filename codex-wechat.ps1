#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('login', 'start', 'status', 'service-start', 'service-stop',
                 'service-restart', 'service-status', 'autostart', 'logs', 'uninstall', 'update', 'install', 'menu', 'components', 'app', 'shared-backend')]
    [string]$Command = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('on', 'off')]
    [string]$Value,
    [switch]$Follow,
    [switch]$Rebind,
    [switch]$PurgeData,
    [switch]$Json,
    [string]$Repo,
    [string]$Ref,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [string]$Component,
    [string]$SkillsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Rebind -and $Command -ne 'login') { throw '-Rebind is only valid with login' }
$TaskName = 'CodexWeChat'
$ScriptRoot = $PSScriptRoot
$InstallRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} elseif ((Split-Path -Leaf (Split-Path -Parent $ScriptRoot)) -eq 'releases') {
    Split-Path -Parent (Split-Path -Parent $ScriptRoot)
} else {
    $ScriptRoot
}
if ($Command -in @('app', 'shared-backend')) {
    if ($PSBoundParameters.Count -gt 1) { throw 'Desktop commands do not accept other bridge parameters' }
    $DesktopAction = if ($Command -eq 'app') { 'app' } else { 'ensure' }
    & (Join-Path $InstallRoot 'codex-desktop.ps1') $DesktopAction
    exit $LASTEXITCODE
}
if ($Command -in @('update', 'install', 'menu', 'components')) {
    if ($PSBoundParameters.ContainsKey('Value') -or $Follow -or $PurgeData -or $Json) {
        throw 'installer commands do not accept -Value, -Follow, -PurgeData or -Json'
    }
    $Installer = Join-Path $InstallRoot 'installer.py'
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "stable installer missing: $Installer; run the installer from new source first"
    }
    $UpdatePython = Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $UpdatePython -PathType Leaf)) {
        $PythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
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
if ($Repo -or $Ref -or $DryRun -or $NonInteractive -or $Component -or $SkillsDir) { throw '-Repo, -Ref and -DryRun are only valid with installer commands' }
$Project = $ScriptRoot
$CurrentFile = Join-Path $InstallRoot 'current'
$CurrentItem = Get-Item -LiteralPath $CurrentFile -Force -ErrorAction SilentlyContinue
if ($null -ne $CurrentItem) {
    if ($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "codex-wechat.ps1: current must not be a reparse point: $CurrentFile"
    }
    if ($CurrentItem.PSIsContainer) {
        throw "codex-wechat.ps1: current must be a file: $CurrentFile"
    }
    $ReleaseId = (Get-Content -LiteralPath $CurrentFile -TotalCount 1).Trim()
    if (-not $ReleaseId -or $ReleaseId -notmatch '^[A-Za-z0-9._-]+$') {
        throw "codex-wechat.ps1: invalid release id in $CurrentFile"
    }
    $Project = Join-Path $InstallRoot "releases\$ReleaseId"
}
if (-not (Test-Path -LiteralPath (Join-Path $Project 'codex_wechat.py') -PathType Leaf)) {
    throw "codex-wechat.ps1: active project files are missing: $Project"
}
$env:CODEX_WECHAT_INSTALL_ROOT = $InstallRoot
$DataRoot = Join-Path $env:LOCALAPPDATA 'codex-wechat'
$StateDir = Join-Path $DataRoot '.state'
$ConfigPath = Join-Path $DataRoot 'config.json'
$LogPath = Join-Path $DataRoot 'logs\service.log'
$VenvPython = Join-Path $Project '.venv\Scripts\python.exe'
$Agent = Join-Path $Project 'codex_wechat.py'
$ServiceScript = Join-Path $Project 'windows_service.py'
$StableServiceScript = Join-Path $InstallRoot 'bootstrap_supervisor.py'
$ExpectedActionPrefixes = @(
    ('"' + $StableServiceScript + '" supervise'),
    ('"' + $ServiceScript + '" supervise')
)

function Fail([string]$Message) {
    throw "codex-wechat.ps1: $Message"
}

if ($Command -ne 'autostart' -and
        ($PSBoundParameters.ContainsKey('Value') -or $Json.IsPresent)) {
    Fail '-Value and -Json are only valid with the autostart command'
}

function Get-InstalledTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Fail "scheduled task '$TaskName' is not installed; run .\install.ps1 first"
    }
    return $task
}

function Test-OwnedTask([object]$Task) {
    if (-not $Task) {
        return $false
    }
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) {
        return $false
    }
    $actionText = [string]$actions[0].Arguments
    foreach ($prefix in $ExpectedActionPrefixes) {
        if ($actionText.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            if ($actionText.Length -eq $prefix.Length -or
                    [char]::IsWhiteSpace($actionText[$prefix.Length])) {
                return $true
            }
        }
    }
    return $false
}

function Get-LogonTriggers([object]$Task) {
    return @($Task.Triggers | Where-Object {
        $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger'
    })
}

function Get-AutostartTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Fail "scheduled task '$TaskName' is not installed; run .\install.ps1 first"
    }
    if (-not (Test-OwnedTask $task)) {
        Fail "scheduled task '$TaskName' is not owned by this project"
    }
    $triggers = @(Get-LogonTriggers $task)
    if ($triggers.Count -eq 0) {
        Fail "scheduled task '$TaskName' has no logon trigger"
    }
    return [pscustomobject]@{ Task = $task; LoginTriggers = $triggers }
}

function Get-AutostartEnabled([object]$Task, [object[]]$LoginTriggers) {
    $taskEnabled = [bool]$Task.Settings.Enabled
    $triggerEnabled = @($LoginTriggers | Where-Object { [bool]$_.Enabled }).Count -gt 0
    return $taskEnabled -and $triggerEnabled
}

function Write-AutostartResult([bool]$Enabled) {
    if ($Json) {
        [ordered]@{ enabled = $Enabled } | ConvertTo-Json -Compress
        return
    }
    Write-Host ("autostart: " + $(if ($Enabled) { 'on' } else { 'off' }))
}

function Wait-TaskStopped([int]$TimeoutSeconds = 15) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task -or $task.State -notin @('Running', 'Queued')) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    Fail "scheduled task '$TaskName' did not stop within $TimeoutSeconds seconds"
}

if (-not (Test-Path $VenvPython)) {
    Fail "Python environment not found at $VenvPython; run .\install.ps1 first"
}
if (-not (Test-Path $DataRoot)) {
    New-Item -ItemType Directory -Force -Path $DataRoot, $StateDir, (Join-Path $DataRoot 'logs') | Out-Null
}

switch ($Command) {
    'login' {
        if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
            Fail 'WeChat login needs an interactive terminal; do not redirect stdin or stdout'
        }
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task -and $task.State -in @('Running', 'Queued')) {
            Fail 'stop the background service before logging in again'
        }
        $LoginArguments = @('login', '--state-dir', $StateDir, '--config', $ConfigPath)
        if ($Rebind) { $LoginArguments += '--rebind' }
        & $VenvPython -u $Agent @LoginArguments
        exit $LASTEXITCODE
    }
    'start' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task -and $task.State -in @('Running', 'Queued')) {
            Fail 'the background service is running; stop it before starting a foreground bridge'
        }
        & $VenvPython -u $Agent start --state-dir $StateDir --config $ConfigPath
        exit $LASTEXITCODE
    }
    'status' {
        & $VenvPython -u $Agent status --state-dir $StateDir --config $ConfigPath
        $agentExit = $LASTEXITCODE
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            Write-Host "task=$($task.State) last_result=$($info.LastTaskResult)"
        } else {
            Write-Host 'task=not-installed'
        }
        exit $agentExit
    }
    'service-start' {
        $task = Get-InstalledTask
        if (-not (Test-Path (Join-Path $StateDir 'account.json'))) {
            Fail 'WeChat is not bound; run .\codex-wechat.ps1 start in an interactive terminal first'
        }
        if ($task.State -in @('Running', 'Queued')) {
            Write-Host 'service is already running'
            exit 0
        }
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "start requested; log: $LogPath"
    }
    'service-stop' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host 'service is not installed'
            exit 0
        }
        if ($task.State -notin @('Running', 'Queued')) {
            Write-Host "service is not running (state=$($task.State))"
            exit 0
        }
        Stop-ScheduledTask -TaskName $TaskName
        Wait-TaskStopped
        Write-Host 'service stopped'
    }
    'service-restart' {
        Get-InstalledTask | Out-Null
        $task = Get-ScheduledTask -TaskName $TaskName
        if ($task.State -in @('Running', 'Queued')) {
            Stop-ScheduledTask -TaskName $TaskName
            Wait-TaskStopped
        }
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "restart requested; log: $LogPath"
    }
    'service-status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host 'task=not-installed'
            exit 1
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "task=$($task.State)"
        Write-Host "last_run=$($info.LastRunTime)"
        Write-Host "last_result=$($info.LastTaskResult)"
        Write-Host "log=$LogPath"
    }
    'autostart' {
        $info = Get-AutostartTask
        if (-not $PSBoundParameters.ContainsKey('Value')) {
            Write-AutostartResult (Get-AutostartEnabled $info.Task $info.LoginTriggers)
            return
        }
        $enabled = $Value -eq 'on'
        if ($enabled -and -not [bool]$info.Task.Settings.Enabled) {
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
            $info = Get-AutostartTask
        }
        $triggers = @($info.Task.Triggers)
        foreach ($trigger in $info.LoginTriggers) {
            $trigger.Enabled = $enabled
        }
        Set-ScheduledTask -TaskName $TaskName -Trigger $triggers | Out-Null
        $info = Get-AutostartTask
        Write-AutostartResult (Get-AutostartEnabled $info.Task $info.LoginTriggers)
    }
    'logs' {
        if (-not (Test-Path $LogPath)) {
            Write-Host "log does not exist yet: $LogPath"
            exit 1
        }
        if ($Follow) {
            Get-Content -Path $LogPath -Tail 100 -Wait -Encoding UTF8
        } else {
            Get-Content -Path $LogPath -Tail 100 -Encoding UTF8
        }
    }
    'uninstall' {
        & (Join-Path $Project 'uninstall.ps1') -PurgeData:$PurgeData
        exit 0
    }
}
