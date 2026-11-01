#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$Force,
    [ValidateSet('on', 'off')]
    [string]$Autostart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'CodexWeChat'
$Project = $PSScriptRoot
$DataRoot = Join-Path $env:LOCALAPPDATA 'codex-wechat'
$StateDir = Join-Path $DataRoot '.state'
$ConfigPath = Join-Path $DataRoot 'config.json'
$LogDir = Join-Path $DataRoot 'logs'
$LogPath = Join-Path $LogDir 'service.log'
$Venv = Join-Path $Project '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvPythonw = Join-Path $Venv 'Scripts\pythonw.exe'
$serviceScript = Join-Path $Project 'windows_service.py'
$ExpectedActionPrefix = '"' + $serviceScript + '" supervise'
$script:Warnings = @()

function Fail([string]$Message) {
    throw "install.ps1: $Message"
}

function Add-Warning([string]$Message) {
    $script:Warnings += $Message
    Write-Warning $Message
}

function Quote-Arg([string]$Value) {
    if ($Value.Contains('"')) {
        Fail "paths containing a double quote are not supported"
    }
    return '"' + $Value + '"'
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

function Get-EffectiveAutostart([object]$Task) {
    $triggers = @(Get-LogonTriggers $Task)
    if ($triggers.Count -eq 0) {
        return $null
    }
    $triggerEnabled = @($triggers | Where-Object { [bool]$_.Enabled }).Count -gt 0
    return [bool]$Task.Settings.Enabled -and $triggerEnabled
}

function Find-Python {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $candidate = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                return [string]$candidate
            }
        } catch {
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    return $null
}

function Test-Python311([string]$Path) {
    if (-not $Path) {
        return $false
    }
    $exitCode = 1
    try {
        & $Path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null |
            Out-Null
        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 1
    }
    return $exitCode -eq 0
}

function Test-PythonDependencies([string]$Python) {
    if (-not $Python) {
        return $false
    }
    $check = @'
try:
    import cryptography
    import qrcode
    import websockets
    from websockets.asyncio.client import unix_connect
    crypto = tuple(int(part) for part in cryptography.__version__.split(".")[:2])
    websocket_major = int(websockets.__version__.split(".")[0])
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if crypto >= (38, 0) and 15 <= websocket_major < 18 else 1)
'@
    $exitCode = 1
    try {
        & $Python -c $check 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 1
    }
    return $exitCode -eq 0
}

function Get-NativeCodexState {
    $candidates = @()
    $standalone = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin\codex.exe'
    if (Test-Path -LiteralPath $standalone -PathType Leaf) {
        $candidates += $standalone
    }
    $command = Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($command) {
        $candidates += $command.Source
    }
    $candidates = @($candidates | Select-Object -Unique)
    foreach ($candidate in $candidates) {
        $exitCode = 1
        try {
            & $candidate --version 2>$null | Out-Null
            $exitCode = $LASTEXITCODE
        } catch {
            $exitCode = 1
        }
        if ($exitCode -eq 0) {
            return [pscustomobject]@{ Ready = $true; Path = $candidate }
        }
        Add-Warning "Codex CLI candidate could not run: $candidate"
    }
    Add-Warning 'Native Codex CLI (codex.exe) was not found or is not runnable. The desktop App alone is not accepted; install the Codex CLI, then rerun.'
    return [pscustomobject]@{ Ready = $false; Path = $null }
}

function Get-CurlState {
    $curl = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $curl) {
        Add-Warning 'curl.exe 8.4 or newer was not found; attachment and login stages will be skipped.'
        return [pscustomobject]@{ Ready = $false; Version = $null }
    }
    $versionLine = $null
    $exitCode = 1
    try {
        $versionLine = (& $curl.Source --version 2>$null | Select-Object -First 1)
        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 1
    }
    if ($exitCode -ne 0 -or -not $versionLine -or
            $versionLine -notmatch 'curl\s+(\d+)\.(\d+)') {
        Add-Warning "curl.exe could not be executed or its version could not be read: $($curl.Source)"
        return [pscustomobject]@{ Ready = $false; Version = $null }
    }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 8 -or ($major -eq 8 -and $minor -lt 4)) {
        Add-Warning "curl.exe 8.4 or newer is required; found $major.$minor at $($curl.Source)."
        return [pscustomobject]@{ Ready = $false; Version = "$major.$minor" }
    }
    return [pscustomobject]@{ Ready = $true; Version = "$major.$minor" }
}

if ($env:OS -ne 'Windows_NT') {
    Fail 'this installer is for Windows only'
}
if ([Environment]::OSVersion.Version.Build -lt 22000) {
    Fail 'Windows 11 is required'
}
if (-not $env:LOCALAPPDATA) {
    Fail 'LOCALAPPDATA is not set'
}
if (-not (Test-Path (Join-Path $Project 'codex_wechat.py'))) {
    Fail 'codex_wechat.py is missing from the project directory'
}

# Keep the parameter contract strict even when dependencies are missing.
if (-not $PSBoundParameters.ContainsKey('Autostart')) {
    if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or
            -not [Environment]::UserInteractive) {
        Fail 'non-interactive install requires an explicit -Autostart on|off choice'
    }
}

if (Test-Path $DataRoot) {
    $dataItem = Get-Item -LiteralPath $DataRoot -Force
    if (-not $dataItem.PSIsContainer) {
        Fail "data path is not a directory: $DataRoot"
    }
    if (($dataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "data path must not be a reparse point: $DataRoot"
    }
}
New-Item -ItemType Directory -Force -Path $DataRoot, $StateDir, (Join-Path $DataRoot 'workspace'), $LogDir | Out-Null
if (-not (Test-Path $ConfigPath)) {
    $defaultConfig = [ordered]@{
        cwd = (Join-Path $DataRoot 'workspace')
        backends = [ordered]@{
            codex = [ordered]@{
                env = [ordered]@{}
                threadOptions = [ordered]@{}
            }
        }
    }
    $json = ($defaultConfig | ConvertTo-Json -Depth 8) + "`n"
    [IO.File]::WriteAllText($ConfigPath, $json, (New-Object Text.UTF8Encoding($false)))
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$ownedExisting = $existing -and (Test-OwnedTask $existing)
if ($existing -and -not $ownedExisting -and -not $Force) {
    Fail "scheduled task '$TaskName' exists but is not this installation; use -Force to replace it"
}

# Python is optional for preparation: its absence skips only the stages that
# need it, and never overwrites an existing environment or data directory.
$BootstrapPython = Find-Python
$bootstrapReady = $false
if ($BootstrapPython) {
    $bootstrapReady = Test-Python311 $BootstrapPython
}

$venvPythonReady = $false
if (Test-Path $VenvPython) {
    if (Test-Python311 $VenvPython) {
        $venvPythonReady = $true
    } else {
        Add-Warning "The existing .venv Python is not usable; remove .venv and rerun after installing Python 3.11+."
    }
} elseif (Test-Path $Venv) {
    $venvItem = Get-Item -LiteralPath $Venv -Force
    if (-not $venvItem.PSIsContainer) {
        Fail "path conflict: $Venv is not a directory"
    }
    Add-Warning "The existing .venv has no Scripts\python.exe; remove .venv and rerun after installing Python 3.11+."
} elseif ($bootstrapReady) {
    $venvCreated = $false
    try {
        & $BootstrapPython -m venv $Venv
        $venvCreated = ($LASTEXITCODE -eq 0)
    } catch {
        $venvCreated = $false
    }
    if (-not $venvCreated -or -not (Test-Path $VenvPython)) {
        Add-Warning "Could not create a usable .venv; pip, login and task registration will be skipped."
    } elseif (Test-Python311 $VenvPython) {
        $venvPythonReady = $true
    } else {
        Add-Warning "The new .venv Python is not 3.11+; remove .venv and rerun after installing a supported Python."
    }
} else {
    Add-Warning 'Python 3.11+ is unavailable; .venv creation, pip, login and task registration will be skipped.'
}

$pythonDepsReady = $false
if ($venvPythonReady) {
    $requirements = Join-Path $Project 'requirements.txt'
    if (Test-Path $requirements) {
        $pipOk = $false
        try {
            & $VenvPython -m pip install --disable-pip-version-check -r $requirements
            $pipOk = ($LASTEXITCODE -eq 0)
        } catch {
            $pipOk = $false
        }
        if (-not $pipOk) {
            Add-Warning "pip could not install requirements.txt; checking the packages already present in .venv."
        }
    } else {
        Add-Warning 'requirements.txt is missing; checking the packages already present in .venv.'
    }
    if (Test-PythonDependencies $VenvPython) {
        $pythonDepsReady = $true
    } else {
        Add-Warning "Python dependencies are missing or incompatible; run .venv\Scripts\python.exe -m pip install -r requirements.txt."
    }
}

$codexState = Get-NativeCodexState
$curlState = Get-CurlState
$runtimeReady = $venvPythonReady -and $pythonDepsReady -and $codexState.Ready -and $curlState.Ready

$canRegisterTask = $venvPythonReady
$autostartEnabled = $null
if ($PSBoundParameters.ContainsKey('Autostart')) {
    $autostartEnabled = $Autostart -eq 'on'
}
$taskRegistered = $false
$taskPreserved = $false
$foreignTaskPreserved = $false

if ($canRegisterTask) {
    if (-not $PSBoundParameters.ContainsKey('Autostart')) {
        $defaultAutostart = $false
        if ($ownedExisting) {
            $currentAutostart = Get-EffectiveAutostart $existing
            if ($null -ne $currentAutostart) {
                $defaultAutostart = [bool]$currentAutostart
            }
        }
        $prompt = if ($defaultAutostart) {
            'Enable automatic start at Windows sign-in? [Y/n]'
        } else {
            'Enable automatic start at Windows sign-in? [y/N]'
        }
        while ($true) {
            $answer = Read-Host $prompt
            if ([string]::IsNullOrWhiteSpace($answer)) {
                $autostartEnabled = $defaultAutostart
                break
            }
            if ($answer -match '^(?i:y|yes)$') {
                $autostartEnabled = $true
                break
            }
            if ($answer -match '^(?i:n|no)$') {
                $autostartEnabled = $false
                break
            }
            Write-Host 'Please answer yes or no.'
        }
    }

    $taskPython = $VenvPythonw
    if (-not (Test-Path $taskPython)) {
        $taskPython = $VenvPython
    }
    $arguments = @(
        (Quote-Arg $serviceScript),
        'supervise',
        '--project', (Quote-Arg $Project),
        '--state-dir', (Quote-Arg $StateDir),
        '--config', (Quote-Arg $ConfigPath),
        '--log', (Quote-Arg $LogPath)
    ) -join ' '

    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $taskPython -Argument $arguments -WorkingDirectory $Project
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
    $trigger.Enabled = [bool]$autostartEnabled
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -Hidden

    try {
        if ($ownedExisting) {
            Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings | Out-Null
        } else {
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Force | Out-Null
        }
        $installedTask = Get-ScheduledTask -TaskName $TaskName
        if (-not $installedTask) {
            Fail "scheduled task '$TaskName' could not be read after registration"
        }
        if (-not [bool]$installedTask.Settings.Enabled) {
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
        }
        $taskRegistered = $true
    } catch {
        Fail "could not register or update scheduled task '$TaskName': $($_.Exception.Message)"
    }
} else {
    if ($existing) {
        $taskPreserved = $true
        if ($ownedExisting) {
            Add-Warning "The existing scheduled task was preserved but not updated because no usable Python environment is available."
        } else {
            $foreignTaskPreserved = $true
            Add-Warning "The existing scheduled task is not owned by this project and was not replaced because no usable Python environment is available."
        }
    } else {
        Add-Warning "The scheduled task was not registered because no usable Python 3.11+ environment is available."
    }
    if ($PSBoundParameters.ContainsKey('Autostart')) {
        Add-Warning 'The autostart choice was not applied because the scheduled task was not registered or updated.'
    }
}

$accountPath = Join-Path $StateDir 'account.json'
$bound = Test-Path $accountPath
$loginState = if ($bound) { 'already bound' } else { 'not bound' }
if (-not $bound) {
    $interactive = -not ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or
        -not [Environment]::UserInteractive)
    if (-not $venvPythonReady) {
        $loginState = 'skipped (no usable Python)'
        Add-Warning 'WeChat login was skipped because no usable Python environment is available.'
    } elseif (-not $pythonDepsReady) {
        $loginState = 'skipped (Python dependencies missing)'
        Add-Warning 'WeChat login was skipped because Python dependencies are missing.'
    } elseif (-not $curlState.Ready) {
        $loginState = 'skipped (curl missing or too old)'
        Add-Warning 'WeChat login was skipped because curl.exe 8.4+ is unavailable.'
    } elseif (-not $interactive) {
        $loginState = 'skipped (needs an interactive terminal)'
        Add-Warning 'WeChat login was skipped because the terminal is not interactive; run .\codex-wechat.ps1 login manually.'
    } else {
        $loginTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($loginTask -and $loginTask.State -in @('Running', 'Queued')) {
            $loginState = 'skipped (service is running)'
            Add-Warning 'WeChat login was skipped because the service is running; stop it manually if a new login is required.'
        } else {
            Write-Host 'WeChat login is required. Scan the QR code with the phone now.'
            try {
                & $VenvPython -u (Join-Path $Project 'codex_wechat.py') login --state-dir $StateDir --config $ConfigPath
                $loginExit = $LASTEXITCODE
            } catch {
                $loginExit = 1
            }
            if ($loginExit -ne 0 -or -not (Test-Path $accountPath)) {
                Fail 'WeChat login did not complete; the scheduled task remains registered but the service was not started.'
            }
            $bound = $true
            $loginState = 'completed'
        }
    }
}

$startState = 'not started'
if ($taskRegistered) {
    $task = Get-ScheduledTask -TaskName $TaskName
    if (-not $task) {
        Fail "scheduled task '$TaskName' disappeared after registration"
    }
    if ($task.State -in @('Running', 'Queued')) {
        $startState = 'already running'
    } elseif ($NoStart) {
        $startState = 'not requested (-NoStart)'
    } elseif (-not $bound) {
        $startState = 'not started (WeChat not bound)'
    } elseif (-not $runtimeReady) {
        $startState = 'not started (dependencies missing)'
    } else {
        try {
            Start-ScheduledTask -TaskName $TaskName
            $startState = 'start request sent'
        } catch {
            Fail "could not request a service start: $($_.Exception.Message)"
        }
    }
} elseif ($taskPreserved) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -in @('Running', 'Queued')) {
        $startState = 'already running (existing task preserved)'
    } else {
        $startState = 'not started (task not updated; Python missing)'
    }
} else {
    $startState = 'not started (task not registered)'
}

$pending = (-not $bound) -or (-not $runtimeReady) -or (-not $taskRegistered)
Write-Host ''
if ($script:Warnings.Count -gt 0) {
    Write-Host 'Warnings:'
    foreach ($warning in $script:Warnings) {
        Write-Host (" - " + $warning)
    }
    Write-Host ''
}
if ($pending) {
    Write-Host 'Result: preparation incomplete; dependencies or login are pending.'
} elseif ($NoStart) {
    Write-Host 'Result: ready; start was not requested because -NoStart was used.'
} else {
    Write-Host 'Result: ready.'
}
if ($taskRegistered) {
    Write-Host "Task: registered ($TaskName)"
} elseif ($taskPreserved) {
    if ($foreignTaskPreserved) {
        Write-Host "Task: existing foreign task preserved; not updated ($TaskName)"
    } else {
        Write-Host "Task: existing task preserved; not updated ($TaskName)"
    }
} else {
    Write-Host 'Task: not registered (no usable Python)'
}
if ($taskRegistered) {
    Write-Host ("Autostart: " + $(if ($autostartEnabled) { 'on' } else { 'off' }))
} else {
    Write-Host 'Autostart: not applied'
}
Write-Host ("Login: " + $loginState)
Write-Host ("Start: " + $startState)
Write-Host ("Data: " + $DataRoot)
Write-Host ("Log:  " + $LogPath)
if ($pending) {
    $nextAutostart = 'on'
    if ($autostartEnabled -eq $false) {
        $nextAutostart = 'off'
    }
    Write-Host ("Next: fix the warnings, then rerun .\install.ps1 -Autostart " + $nextAutostart)
}
