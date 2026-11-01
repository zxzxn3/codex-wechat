#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('login', 'start', 'status', 'service-start', 'service-stop',
                 'service-restart', 'service-status', 'autostart', 'logs', 'uninstall')]
    [string]$Command = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('on', 'off')]
    [string]$Value,
    [switch]$Follow,
    [switch]$PurgeData,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'CodexWeChat'
$Project = $PSScriptRoot
$DataRoot = Join-Path $env:LOCALAPPDATA 'codex-wechat'
$StateDir = Join-Path $DataRoot '.state'
$ConfigPath = Join-Path $DataRoot 'config.json'
$LogPath = Join-Path $DataRoot 'logs\service.log'
$VenvPython = Join-Path $Project '.venv\Scripts\python.exe'
$Agent = Join-Path $Project 'codex_wechat.py'
$ServiceScript = Join-Path $Project 'windows_service.py'
$ExpectedActionPrefix = '"' + $ServiceScript + '" supervise'

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
    if (-not $actionText.StartsWith(
            $ExpectedActionPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    if ($actionText.Length -gt $ExpectedActionPrefix.Length -and
            -not [char]::IsWhiteSpace($actionText[$ExpectedActionPrefix.Length])) {
        return $false
    }
    return $true
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
        & $VenvPython -u $Agent login --state-dir $StateDir --config $ConfigPath
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
            Fail 'WeChat is not bound; run .\codex-wechat.ps1 login first'
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
