#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PurgeData,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'CodexWeChat'
$Project = $PSScriptRoot
$InstallRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} elseif ((Split-Path -Leaf (Split-Path -Parent $Project)) -eq 'releases') {
    Split-Path -Parent (Split-Path -Parent $Project)
} else {
    $Project
}
$ServiceScript = Join-Path $Project 'windows_service.py'
$StableServiceScript = Join-Path $InstallRoot 'bootstrap_supervisor.py'
$ExpectedActionPrefixes = @(
    ('"' + $StableServiceScript + '" supervise'),
    ('"' + $ServiceScript + '" supervise'),
    ('"' + (Join-Path $InstallRoot 'windows_service.py') + '" supervise')
)
$DataRoot = Join-Path $env:LOCALAPPDATA 'codex-wechat'

function Fail([string]$Message) {
    throw "uninstall.ps1: $Message"
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

function Stop-SharedBackends {
    # The bridge keeps its app-server alive across client restarts, so it can
    # still hold the data directory open when the installation is removed.
    $sharedRoot = Join-Path $DataRoot 'shared'
    if (-not (Test-Path -LiteralPath $sharedRoot)) {
        return
    }
    $records = @(Get-ChildItem -LiteralPath $sharedRoot -Filter 'shared-backend-process.json' -Recurse -File -ErrorAction SilentlyContinue)
    foreach ($record in $records) {
        $value = $null
        try {
            $value = Get-Content -LiteralPath $record.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            continue
        }
        $id = 0
        if (-not $value -or -not [int]::TryParse([string]$value.pid, [ref]$id)) {
            continue
        }
        $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id) -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }
        if (-not $process.ExecutablePath -or $process.ExecutablePath -ne $value.executable -or
                $process.CreationDate.ToUniversalTime().ToString('o') -ne $value.creationTimeUtc) {
            Write-Warning "skipping shared backend ${id}: recorded identity no longer matches"
            continue
        }
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Process -Id $id -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {
            Start-Sleep -Milliseconds 250
        }
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            Fail "shared backend $id did not stop"
        }
        Write-Host "stopped shared backend: $id"
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $actionText = [string]$task.Actions[0].Arguments
    $owned = $false
    if (@($task.Actions).Count -eq 1) {
        foreach ($prefix in $ExpectedActionPrefixes) {
            if ($actionText.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                $owned = $true
                break
            }
        }
    }
    if (-not $owned -and -not $Force) {
        Fail "task '$TaskName' is not owned by this installation; use -Force to remove it anyway"
    }
    if ($task.State -in @('Running', 'Queued')) {
        Stop-ScheduledTask -TaskName $TaskName
        Wait-TaskStopped
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "removed scheduled task: $TaskName"
} else {
    Write-Host "scheduled task is not installed: $TaskName"
}

Stop-SharedBackends

if ($PurgeData) {
    $expectedLeaf = 'codex-wechat'
    $resolvedParent = [IO.Path]::GetFullPath((Split-Path -Parent $DataRoot))
    $resolvedLocal = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
    if (((Split-Path -Leaf $DataRoot) -ne $expectedLeaf) -or ($resolvedParent -ne $resolvedLocal)) {
        Fail "refusing to purge unexpected data path: $DataRoot"
    }
    if (Test-Path $DataRoot) {
        $dataItem = Get-Item -LiteralPath $DataRoot -Force
        if ((-not $dataItem.PSIsContainer) -or ((($dataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))) {
            Fail "refusing to purge non-directory or reparse-point data path: $DataRoot"
        }
        Remove-Item -LiteralPath $DataRoot -Recurse -Force
        Write-Host "removed data: $DataRoot"
    }
} else {
    Write-Host "data retained: $DataRoot (use -PurgeData only when credentials and state are no longer needed)"
}
Write-Host 'project files and the virtual environment were not removed'
