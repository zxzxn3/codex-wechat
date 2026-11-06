#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PreparedRuntime,
    [switch]$PreserveAutostart,
    [switch]$NoStart,
    [string]$InstallRoot,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [string]$Repo,
    [string]$Ref
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Public local entry delegates to the same installer as the network entry.
if (-not $PreparedRuntime) {
    $py = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
    $prefix = @('-3')
    if (-not $py) {
        $py = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
        $prefix = @()
    }
    if (-not $py) { throw 'Python 3.11+ is required to run the installer.' }
    $arguments = @((Join-Path $PSScriptRoot 'installer.py'), 'menu')
    if ($InstallRoot) { $arguments += @('--dir', $InstallRoot) }
    if ($DryRun) { $arguments += '--dry-run' }
    if ($NonInteractive) { $arguments += '--non-interactive' }
    if ($Repo) { $arguments += @('--repo', $Repo) }
    if ($Ref) { $arguments += @('--ref', $Ref) }
    & $py.Source @prefix -u @arguments
    exit $LASTEXITCODE
}
# Internal integration only: Python has finished downloads and runtime checks.
if (-not $InstallRoot -or (-not $env:LOCALAPPDATA -and -not $env:CODEX_WECHAT_DATA_ROOT)) {
    throw 'InstallRoot and a usable data root are required.'
}
if ($DryRun -or $NonInteractive -or $Repo -or $Ref) { throw 'Invalid internal integration arguments.' }
$TaskName = 'CodexWeChat'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
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
$SavedDataRoot = Read-SavedDataRoot $InstallRoot
if (-not $SavedDataRoot) {
    throw '数据根标记缺失；请重新运行安装器。'
}
if (-not $env:CODEX_WECHAT_DATA_ROOT) {
    $env:CODEX_WECHAT_DATA_ROOT = $SavedDataRoot
}
$DataRoot = if ($env:CODEX_WECHAT_DATA_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_DATA_ROOT)
} else {
    $SavedDataRoot
}
$ServiceScript = Join-Path $InstallRoot 'bootstrap_supervisor.py'
$Python = Join-Path $InstallRoot '.bootstrap-venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe'
}
$ExpectedActionExecutables = @(
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\pythonw.exe'),
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe')
)
foreach ($path in @($Python, $ServiceScript)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Prepared runtime missing: $path" }
}
foreach ($path in @($DataRoot, $InstallRoot)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($item -and ((-not $item.PSIsContainer) -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
        throw "Directory must not be a file or reparse point: $path"
    }
}
function Quote-Arg([string]$value) {
    if ($value.Contains('"')) { throw 'Paths containing double quotes are not supported.' }
    return '"' + $value + '"'
}
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$autostart = $false
if ($existing) {
    $prefixes = @(
        ('"' + $ServiceScript + '" supervise')
    )
    $owned = $false
    $actions = @($existing.Actions)
    if ($actions.Count -eq 1) {
        $execute = [string]$actions[0].Execute
        $text = [string]$actions[0].Arguments
        foreach ($prefix in $prefixes) {
            if ($ExpectedActionExecutables -contains $execute -and
                    $text.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
                    ($text.Length -eq $prefix.Length -or [char]::IsWhiteSpace($text[$prefix.Length]))) {
                $owned = $true
            }
        }
    }
    if (-not $owned) { throw "Task $TaskName belongs to another installation; it was not changed." }
    $triggers = @($existing.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' })
    $autostart = [bool]$existing.Settings.Enabled -and @($triggers | Where-Object { $_.Enabled }).Count -gt 0
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = @((Quote-Arg $ServiceScript), 'supervise',
    '--install-root', (Quote-Arg $InstallRoot)) -join ' '
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Enabled = $autostart
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -Hidden
if ($existing) {
    $settings.Enabled = [bool]$existing.Settings.Enabled
    # Re-registering needs write access to the task object, and a task created
    # by an elevated installer is owned by Administrators. Skip the write when
    # nothing changed so a normal user can still update the program.
    $current = @($existing.Actions)
    # Task Scheduler stores the account name without the machine/domain prefix.
    $currentUser = ([string]$existing.Principal.UserId -split '\\')[-1]
    $wantedUser = ($user -split '\\')[-1]
    $unchanged = ($current.Count -eq 1 -and
        [string]$current[0].Execute -eq [string]$action.Execute -and
        [string]$current[0].Arguments -eq [string]$action.Arguments -and
        [string]$current[0].WorkingDirectory -eq [string]$action.WorkingDirectory -and
        $currentUser -eq $wantedUser -and
        [string]$existing.Principal.LogonType -eq 'Interactive')
    if ($unchanged) {
        Write-Host 'Task unchanged; access rules preserved.'
    } else {
        try {
            Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
        } catch {
            throw ("Task $TaskName cannot be modified by this account (it was registered by an administrator). " +
                   "Delete it once from an elevated shell (schtasks /delete /tn `"$TaskName`" /f) " +
                   "and re-run the installer as this user.")
        }
    }
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
}
Write-Host 'Task prepared; autostart preserved (off on first install); service not started.'
