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
    Fail "计划任务 '$TaskName' 未在 $TimeoutSeconds 秒内停止"
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
        Fail '前台桥接仍在运行，请先按 Ctrl+C 退出后再卸载。'
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
            Write-Warning "跳过共享后台 ${id}: 记录的身份已不匹配"
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
                Fail "共享后台 $id 仍有已连接的客户端: $owners；请先关闭它们或使用 -Force"
            }
        }
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Process -Id $id -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {
            Start-Sleep -Milliseconds 250
        }
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            Fail "共享后台 $id 未停止"
        }
        Write-Host "已停止共享后台: $id"
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
                         [char]::IsWhiteSpace($actionText[$prefix.Length]))) {
                    $owned = $true
                    break
                }
            }
        }
    }
    if (-not $owned -and -not $Force) {
        Fail "计划任务 '$TaskName' 不属于当前安装；如仍要删除请使用 -Force"
    }
    if ($task.State -in @('Running', 'Queued')) {
        Stop-ScheduledTask -TaskName $TaskName
        Wait-TaskStopped
        Write-Host "已停止计划任务: $TaskName"
    }
} else {
    Write-Host "未安装计划任务: $TaskName"
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
        Fail "拒绝清理范围过大的数据目录: $DataRoot"
    }
    if (Test-Path $DataRoot) {
        $dataItem = Get-Item -LiteralPath $DataRoot -Force
        if ((-not $dataItem.PSIsContainer) -or
                ((($dataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))) {
            Fail "拒绝清理非目录或重解析点数据目录: $DataRoot"
        }
        Stop-SharedBackends
        foreach ($name in $DataPaths) {
            Remove-DataPath $name
        }
        Write-Host "已删除数据目录中的用户数据: $DataRoot"
    }
} else {
    Write-Host "用户数据已保留: $DataRoot（仅在不再需要凭据和状态时使用 -PurgeData）"
    Write-Host '数据目录下的共享 Codex 后台仍在运行'
}
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除计划任务: $TaskName"
}
$Shim = Join-Path $env:USERPROFILE '.local\bin\codex-wechat.cmd'
if (Test-Path -LiteralPath $Shim -PathType Leaf) {
    $shimText = Get-Content -LiteralPath $Shim -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($shimText -and $shimText.Contains('rem codex-wechat command shim')) {
        # When the public entry was used, this file is the batch script cmd.exe
        # is still reading: deleting it now makes cmd fail with "The batch file
        # cannot be found" and report a non-zero exit for a successful uninstall.
        # Hand the deletion to a detached helper that waits for us to exit.
        # -ArgumentList must be one string: an array is quoted per element, and
        # cmd /c then swallows the whole command as a single quoted token.
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList ('/c ping -n 3 127.0.0.1 >nul & del /f /q "' + $Shim + '"') `
            -WindowStyle Hidden | Out-Null
        Write-Host "已移除命令入口: $Shim"
    } else {
        Write-Warning "保留 $Shim：它不是本项目的命令入口"
    }
}
Write-Host '项目文件和虚拟环境未删除'
