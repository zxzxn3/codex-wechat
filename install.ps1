#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PreparedRuntime,
    [string]$InstallRoot,
    [switch]$DryRun,
    [switch]$NonInteractive
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Public local entry delegates to the shared uv bootstrap.
if (-not $PreparedRuntime) {
    $Bootstrap = Join-Path $PSScriptRoot 'bootstrap.ps1'
    $arguments = @{ SourceDir = $PSScriptRoot }
    if ($InstallRoot) { $arguments.Dir = $InstallRoot }
    if ($DryRun) { $arguments.DryRun = $true }
    if ($NonInteractive) { $arguments.NonInteractive = $true }
    & $Bootstrap @arguments
    exit $LASTEXITCODE
}
# Internal integration only: Python has finished downloads and runtime checks.
if (-not $InstallRoot -or (-not $env:LOCALAPPDATA -and -not $env:CODEX_WECHAT_DATA_ROOT)) {
    throw 'InstallRoot and a usable data root are required.'
}
if ($DryRun -or $NonInteractive) { throw 'Invalid internal integration arguments.' }
$TaskName = 'CodexWeChat'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$LayoutPython = Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $LayoutPython -PathType Leaf)) {
    throw 'Stable Python runtime is missing; rerun bootstrap as this user.'
}
$PreviousPythonPath = $env:PYTHONPATH
$PreviousPythonHome = $env:PYTHONHOME
try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PYTHONPATH = $InstallRoot
    $LayoutJson = & $LayoutPython -s -P -m codex_wechat.install_layout show `
        --install-root $InstallRoot
    if ($LASTEXITCODE) { throw 'Installed layout is missing or invalid.' }
    $Layout = $LayoutJson | ConvertFrom-Json
} finally {
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
    if ($null -eq $PreviousPythonHome) {
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONHOME = $PreviousPythonHome
    }
}
$DataRoot = [IO.Path]::GetFullPath([string]$Layout.dataRoot)
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
$arguments = @('-I', (Quote-Arg $ServiceScript), 'supervise',
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
