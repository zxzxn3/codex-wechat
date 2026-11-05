#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PurgeData,
    [switch]$Force,
    [string]$DataRoot
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
$StableServiceScript = Join-Path $InstallRoot 'bootstrap_supervisor.py'
$ExpectedActionPrefixes = @(
    ('"' + $StableServiceScript + '" supervise')
)
$ExpectedActionExecutables = @(
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\pythonw.exe'),
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe')
)
$DataRoot = if ($DataRoot) {
    [IO.Path]::GetFullPath($DataRoot)
} elseif ($env:CODEX_WECHAT_DATA_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_DATA_ROOT)
} else {
    Join-Path $env:LOCALAPPDATA 'codex-wechat'
}
$DataPaths = @('config.json', '.state', 'workspace', 'logs', 'shared')

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

function Remove-DataPath([string]$Name) {
    $path = Join-Path $DataRoot $Name
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) {
        return
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Remove-Item -LiteralPath $path -Force
    } else {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

function Assert-NoForegroundBridge {
    # The bridge holds the state lock. FileShare.None fails while any bridge
    # process still owns that lock; an unheld stale file opens normally.
    $lock = Join-Path $DataRoot '.state\lock'
    if (-not (Test-Path -LiteralPath $lock -PathType Leaf)) {
        return
    }
    $stream = $null
    try {
        $stream = [IO.File]::Open(
            $lock,
            [IO.FileMode]::Open,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None)
    } catch {
        Fail 'foreground bridge is still running; stop it with Ctrl+C before uninstalling'
    } finally {
        if ($stream) {
            $stream.Dispose()
        }
    }
}

function Stop-SharedBackends {
    # A shared backend keeps `shared/<id>/backend.std*.log` open, so the data
    # directory can only be purged after it exits. It is deliberately shared
    # with other clients, which is why this runs only for -PurgeData.
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
        $port = 0
        try {
            $port = ([Uri][string]$value.url).Port
        } catch {
            $port = 0
        }
        if ($port -gt 0 -and -not $Force) {
            # A loopback connection appears twice; drop the backend's own row
            # so the report names the clients that would be interrupted.
            $attached = @(@(
                Get-NetTCPConnection -State Established -LocalPort $port -ErrorAction SilentlyContinue
                Get-NetTCPConnection -State Established -RemotePort $port -ErrorAction SilentlyContinue
            ) | Where-Object { $_.OwningProcess -ne $id })
            if ($attached.Count -gt 0) {
                $owners = @($attached | ForEach-Object {
                    $owner = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
                    if ($owner) { "$($owner.ProcessName)($($owner.Id))" } else { "pid $($_.OwningProcess)" }
                }) -join ', '
                Fail "shared backend $id still has attached clients: $owners; close them first or pass -Force"
            }
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
    $actions = @($task.Actions)
    $owned = $false
    if ($actions.Count -eq 1) {
        $execute = [string]$actions[0].Execute
        $actionText = [string]$actions[0].Arguments
        if ($ExpectedActionExecutables -contains $execute) {
            foreach ($prefix in $ExpectedActionPrefixes) {
                if ($actionText.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
                        ($actionText.Length -eq $prefix.Length -or
                         [char]::IsWhiteSpace($actionText[$prefix.Length])) {
                    $owned = $true
                    break
                }
            }
        }
    }
    if (-not $owned -and -not $Force) {
        Fail "task '$TaskName' is not owned by this installation; use -Force to remove it anyway"
    }
    if ($task.State -in @('Running', 'Queued')) {
        Stop-ScheduledTask -TaskName $TaskName
        Wait-TaskStopped
        Write-Host "stopped scheduled task: $TaskName"
    }
} else {
    Write-Host "scheduled task is not installed: $TaskName"
}

Assert-NoForegroundBridge

# Data removal cannot be undone, so it runs while the scheduled task is still
# registered: a failure leaves a working installation, not a half-uninstalled
# one. The task is unregistered only once the irreversible step has succeeded.
if ($PurgeData) {
    $resolvedUser = [IO.Path]::GetFullPath($env:USERPROFILE)
    $resolvedLocal = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
    $resolvedRoot = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
    $driveRoot = [IO.Path]::GetPathRoot($resolvedRoot).TrimEnd('\')
    if ($resolvedRoot -eq $driveRoot -or $resolvedRoot -eq $resolvedUser -or
            $resolvedRoot -eq $resolvedLocal -or
            $resolvedUser.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Fail "refusing to purge an overly broad data path: $DataRoot"
    }
    if (Test-Path $DataRoot) {
        $dataItem = Get-Item -LiteralPath $DataRoot -Force
        if ((-not $dataItem.PSIsContainer) -or
                ((($dataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))) {
            Fail "refusing to purge non-directory or reparse-point data path: $DataRoot"
        }
        Stop-SharedBackends
        foreach ($name in $DataPaths) {
            Remove-DataPath $name
        }
        Write-Host "removed user data under: $DataRoot"
    }
} else {
    Write-Host "data retained: $DataRoot (use -PurgeData only when credentials and state are no longer needed)"
    Write-Host 'a shared Codex backend under the data root is left running'
}
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "removed scheduled task: $TaskName"
}
$Shim = Join-Path $env:USERPROFILE '.local\bin\codex-wechat.cmd'
if (Test-Path -LiteralPath $Shim -PathType Leaf) {
    $shimText = Get-Content -LiteralPath $Shim -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($shimText -and $shimText.Contains('rem codex-wechat command shim')) {
        Remove-Item -LiteralPath $Shim -Force
        Write-Host "removed command shim: $Shim"
    } else {
        Write-Warning "left $Shim in place: it is not this project's command shim"
    }
}
Write-Host 'project files and the virtual environment were not removed'
