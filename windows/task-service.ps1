#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'start', 'stop', 'autostart')]
    [string]$Action = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('true', 'false')]
    [string]$Value
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'CodexWeChat'
$InstallRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} elseif ((Split-Path -Leaf (Split-Path -Parent $PSScriptRoot)) -eq 'releases') {
    Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
} else {
    Split-Path -Parent $PSScriptRoot
}
$Supervisor = Join-Path $InstallRoot 'bootstrap_supervisor.py'
$ExpectedActionPrefix = '"' + $Supervisor + '" supervise'
$ExpectedExecutables = @(
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\pythonw.exe'),
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe')
)

function Fail([string]$Message) {
    [Console]::Error.WriteLine("windows/task-service.ps1: $Message")
    exit 78
}

function Test-OwnedTask([object]$Task) {
    if (-not $Task) {
        return $false
    }
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) {
        return $false
    }
    if ($ExpectedExecutables -notcontains [string]$actions[0].Execute) {
        return $false
    }
    $arguments = [string]$actions[0].Arguments
    if (-not $arguments.StartsWith($ExpectedActionPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    return ($arguments.Length -eq $ExpectedActionPrefix.Length -or
        [char]::IsWhiteSpace($arguments[$ExpectedActionPrefix.Length]))
}

function Get-InstalledTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Fail 'background service is not registered; rerun the installer'
    }
    if (-not (Test-OwnedTask $task)) {
        Fail "scheduled task '$TaskName' belongs to another installation"
    }
    return $task
}

function Get-LogonTriggers([object]$Task) {
    return @($Task.Triggers | Where-Object {
        $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger'
    })
}

function Write-TaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $installed = $null -ne $task
    $owned = $installed -and (Test-OwnedTask $task)
    $running = $installed -and $task.State -in @('Running', 'Queued')
    $state = if ($installed) { [string]$task.State } else { 'not-installed' }
    $lastResult = $null
    if ($installed) {
        $lastResult = (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
    }
    [ordered]@{
        installed = [bool]$installed
        owned = [bool]$owned
        running = [bool]$running
        state = $state
        last_result = $lastResult
    } | ConvertTo-Json -Compress
}

function Write-AutostartStatus([bool]$Enabled, [bool]$Installed = $true) {
    [ordered]@{ installed = $Installed; enabled = $Enabled } | ConvertTo-Json -Compress
}

switch ($Action) {
    'status' {
        Write-TaskStatus
    }
    'start' {
        $task = Get-InstalledTask
        if ($task.State -notin @('Running', 'Queued')) {
            Start-ScheduledTask -TaskName $TaskName
        }
    }
    'stop' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            return
        }
        if (-not (Test-OwnedTask $task)) {
            Fail "scheduled task '$TaskName' belongs to another installation"
        }
        if ($task.State -in @('Running', 'Queued')) {
            Stop-ScheduledTask -TaskName $TaskName
        }
    }
    'autostart' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            if (-not $PSBoundParameters.ContainsKey('Value')) {
                Write-AutostartStatus $false $false
                return
            }
            Fail 'background service is not registered; rerun the installer'
        }
        if (-not (Test-OwnedTask $task)) {
            Fail "scheduled task '$TaskName' belongs to another installation"
        }
        $triggers = Get-LogonTriggers $task
        if ($triggers.Count -eq 0) {
            Fail "scheduled task '$TaskName' has no logon trigger"
        }
        if (-not $PSBoundParameters.ContainsKey('Value')) {
            $enabled = [bool]$task.Settings.Enabled -and
                @($triggers | Where-Object { [bool]$_.Enabled }).Count -gt 0
            Write-AutostartStatus $enabled
            return
        }
        $enabled = $Value -eq 'true'
        if ($enabled -and -not [bool]$task.Settings.Enabled) {
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
            $task = Get-InstalledTask
            $triggers = Get-LogonTriggers $task
        }
        $allTriggers = @($task.Triggers)
        foreach ($trigger in $triggers) {
            $trigger.Enabled = $enabled
        }
        Set-ScheduledTask -TaskName $TaskName -Trigger $allTriggers | Out-Null
        $task = Get-InstalledTask
        $triggers = Get-LogonTriggers $task
        $actual = [bool]$task.Settings.Enabled -and
            @($triggers | Where-Object { [bool]$_.Enabled }).Count -gt 0
        Write-AutostartStatus $actual
    }
}
