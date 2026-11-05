#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('login', 'rebind', 'start', 'stop', 'status', 'service-start', 'service-stop',
                 'service-restart', 'service-status', 'autostart', 'logs', 'uninstall', 'update', 'install', 'menu', 'components')]
    [string]$Command = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('on', 'off')]
    [string]$Value,
    [switch]$Follow,
    [switch]$Service,
    [switch]$PurgeData,
    [switch]$Json,
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

$TaskName = 'CodexWeChat'
$ScriptRoot = $PSScriptRoot
$InstallRoot = if ($env:CODEX_WECHAT_INSTALL_ROOT) {
    [IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)
} elseif ((Split-Path -Leaf (Split-Path -Parent $ScriptRoot)) -eq 'releases') {
    Split-Path -Parent (Split-Path -Parent $ScriptRoot)
} else {
    $ScriptRoot
}
if ($Command -in @('update', 'install', 'menu', 'components')) {
    if ($PSBoundParameters.ContainsKey('Value') -or $Follow -or $Service -or $PurgeData -or $Json -or
            $StopActive) {
        throw 'installer commands do not accept service or chat control parameters'
    }
    $Installer = Join-Path $InstallRoot 'installer.py'
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "stable installer missing: $Installer; run the installer from new source first"
    }
    $UpdatePython = Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $UpdatePython -PathType Leaf)) {
        # Get-Command returns every match; the WindowsApps alias is a 0-byte stub.
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
if ($Repo -or $Ref -or $DryRun -or $NonInteractive -or $Component -or $SkillsDir) { throw '-Repo, -Ref, -DryRun, -NonInteractive, -Component and -SkillsDir are only valid with installer commands' }
if (($StopActive) -and $Command -ne 'rebind') {
    throw '-StopActive are only valid with the rebind command'
}
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
    if (-not $ReleaseId -or $ReleaseId -notmatch '^[a-f0-9]{16}$') {
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
$LogRoot = Join-Path $DataRoot 'logs'
$BridgeLog = Join-Path $LogRoot 'bridge.jsonl'
$VenvPython = Join-Path $Project '.venv\Scripts\python.exe'
$Agent = Join-Path $Project 'codex_wechat.py'
$StableServiceScript = Join-Path $InstallRoot 'bootstrap_supervisor.py'
$ExpectedActionPrefixes = @(
    ('"' + $StableServiceScript + '" supervise')
)
$ExpectedActionExecutables = @(
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\pythonw.exe'),
    (Join-Path $InstallRoot '.bootstrap-venv\Scripts\python.exe')
)

function Fail([string]$Message) {
    throw "codex-wechat.ps1: $Message"
}

if ($Command -ne 'autostart' -and $PSBoundParameters.ContainsKey('Value')) {
    Fail '-Value is only valid with the autostart command'
}
if ($Command -ne 'logs' -and $Service) {
    Fail '-Service is only valid with the logs command'
}
if ($Command -notin @('autostart', 'service-status') -and $Json.IsPresent) {
    Fail '-Json is only valid with the autostart command or the service-status command'
}

function Get-InstalledTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Fail "scheduled task '$TaskName' is not installed; run .\install.ps1 first"
    }
    if (-not (Test-OwnedTask $task)) {
        Fail "scheduled task '$TaskName' is not owned by this project"
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
    if ($ExpectedActionExecutables -notcontains [string]$actions[0].Execute) {
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

function Wait-ServiceStable([int]$TimeoutSeconds = 15, [int]$SettleMilliseconds = 2000) {
    # The task reports Running as soon as the process is created, so a bridge
    # that dies on startup still looks started. Require it to stay Running.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $runningSince = $null
    while ((Get-Date) -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $state = if ($task) { [string]$task.State } else { 'not-installed' }
        if ($state -in @('Running', 'Queued')) {
            if (-not $runningSince) {
                $runningSince = Get-Date
            } elseif (((Get-Date) - $runningSince).TotalMilliseconds -ge $SettleMilliseconds) {
                return
            }
        } else {
            if ($runningSince) {
                $result = (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
                Fail "后台服务启动后立即退出（state=$state last_result=$result）；日志: $BridgeLog"
            }
            $runningSince = $null
        }
        Start-Sleep -Milliseconds 250
    }
    Fail "后台服务未在 $TimeoutSeconds 秒内稳定运行；日志: $BridgeLog"
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
    'rebind' {
        if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
            Fail 'WeChat rebind needs an interactive terminal; do not redirect stdin or stdout'
        }
        $rebindArguments = @($Agent, 'rebind', '--state-dir', $StateDir, '--config', $ConfigPath)
        if ($StopActive) { $rebindArguments += '--stop-active' }
        & $VenvPython -u @rebindArguments
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
    'stop' {
        & $VenvPython -u $Agent stop --state-dir $StateDir --config $ConfigPath
        exit $LASTEXITCODE
    }
    'status' {
        # Reading the task here is cheap: this process already loads the module
        # that the nested call would have to load again.
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $hostState = [ordered]@{
                installed = $true
                owned = [bool](Test-OwnedTask $task)
                running = [bool]($task.State -in @('Running', 'Queued'))
                state = [string]$task.State
                last_result = (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
            } | ConvertTo-Json -Compress
        } else {
            $hostState = '{"installed":false,"owned":false,"running":false,"state":"not-installed"}'
        }
        # Pass through the environment: PowerShell strips the quotes out of a
        # JSON string handed to a native command.
        $env:CODEX_WECHAT_HOST_STATE = $hostState
        & $VenvPython -u $Agent status --state-dir $StateDir --config $ConfigPath
        exit $LASTEXITCODE
    }
    'service-start' {
        $task = Get-InstalledTask
        if ($task.State -in @('Running', 'Queued')) {
            Write-Host '后台服务已在运行。'
            Write-Host "完整日志: $BridgeLog"
            exit 0
        }
        if (-not (Test-Path (Join-Path $StateDir 'account.json'))) {
            Write-Host '尚未绑定微信，请在当前终端扫码；绑定后会继续启动。'
            & $VenvPython -u $Agent login --state-dir $StateDir --config $ConfigPath --starting
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        Start-ScheduledTask -TaskName $TaskName
        Wait-ServiceStable
        Write-Host '后台服务已启动；终端可以关闭。'
        Write-Host "完整日志: $BridgeLog"
    }
    'service-stop' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host 'service is not installed'
            exit 0
        }
        if (-not (Test-OwnedTask $task)) {
            Fail "scheduled task '$TaskName' is not owned by this project"
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
        Wait-ServiceStable
        Write-Host '后台服务已重启。'
        Write-Host "完整日志: $BridgeLog"
    }
    'service-status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $installed = $null -ne $task
        $owned = $installed -and (Test-OwnedTask $task)
        $running = $installed -and $task.State -in @('Running', 'Queued')
        $taskState = if ($installed) { [string]$task.State } else { 'not-installed' }
        if ($Json) {
            $lastResult = $null
            if ($installed) {
                $lastResult = (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
            }
            [ordered]@{
                installed = [bool]$installed
                owned = [bool]$owned
                running = [bool]$running
                state = $taskState
                last_result = $lastResult
            } | ConvertTo-Json -Compress
            exit 0
        }
        if (-not $task) {
            Write-Host 'task=not-installed'
            exit 1
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "task=$($task.State)"
        Write-Host "last_run=$($info.LastRunTime)"
        Write-Host "last_result=$($info.LastTaskResult)"
        Write-Host "logs=$LogRoot"
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
        $logArguments = @($Agent, 'logs')
        if ($Follow) {
            $logArguments += '--follow'
        }
        if ($Service) {
            $logArguments += '--service'
        }
        & $VenvPython -u @logArguments
        exit $LASTEXITCODE
    }
    'uninstall' {
        & (Join-Path $Project 'uninstall.ps1') -PurgeData:$PurgeData
        exit 0
    }
}
