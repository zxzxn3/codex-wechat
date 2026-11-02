$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'SharedPaths.ps1')
$logDirectory = Join-Path $runtimeRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logPath = Join-Path $logDirectory 'launcher.log'
if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 1MB) {
    Move-Item -LiteralPath $logPath -Destination ($logPath + '.previous') -Force
}
function Write-LaunchLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value ('{0:o} {1}' -f (Get-Date), $Message)
}
try {
    Write-LaunchLog "START launcher PID=$PID PowerShell=$($PSVersionTable.PSVersion)"
    $alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\chatgpt-desktop.exe'
    if (-not (Test-Path -LiteralPath $alias)) { throw 'Codex execution alias chatgpt-desktop.exe is unavailable.' }
    $sharedSettings=Join-Path $runtimeRoot 'shared-backend.json'
    $settings=$null
    if(Test-Path -LiteralPath $sharedSettings){$settings=Get-Content -LiteralPath $sharedSettings -Raw -Encoding UTF8|ConvertFrom-Json}
    if($settings){
        & (Join-Path $PSScriptRoot 'Start-SharedBackend.ps1')
        $settings=Get-Content -LiteralPath $sharedSettings -Raw -Encoding UTF8|ConvertFrom-Json
        $env:CODEX_APP_SERVER_WS_URL=$settings.url
        Write-LaunchLog ('SHARED BACKEND '+$settings.url)
    }
    Write-LaunchLog "ENTRY $alias"
    $existing = @(Get-Process ChatGPT -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*\OpenAI.Codex_*\app\ChatGPT.exe' })
    Write-LaunchLog "Existing Codex process count=$($existing.Count); an existing instance may retain its original environment."
    $run = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $logDirectory ($run + '.stdout.log')
    $stderr = Join-Path $logDirectory ($run + '.stderr.log')
    Write-LaunchLog "OUTPUT stdout=$stdout stderr=$stderr"
    $process = Start-Process -FilePath $alias -WorkingDirectory $env:USERPROFILE -WindowStyle Normal -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Write-LaunchLog "CREATED PID=$($process.Id)"
    if ($process.WaitForExit(2000)) {
        $process.Refresh()
        Write-LaunchLog "EXIT code=$($process.ExitCode) (may hand off to an existing app; this does not prove a window opened)"
    } else {
        Write-LaunchLog 'RUNNING after 2 seconds; window and network readiness not verified.'
    }
    $process.Dispose()
    # Keep only the latest five launch output pairs. Never enumerate unrelated files.
    Get-ChildItem -LiteralPath $logDirectory -File | Where-Object { $_.Name -match '^\d{8}-\d{6}-\d{3}\.(stdout|stderr)\.log$' } |
        Sort-Object Name -Descending | Select-Object -Skip 10 | Remove-Item -Force
    Write-LaunchLog 'END'
} catch {
    Write-LaunchLog ('ERROR ' + $_.Exception.ToString())
    Write-LaunchLog ('LOCATION ' + $_.InvocationInfo.PositionMessage)
    [IO.File]::WriteAllText((Join-Path $runtimeRoot 'last-error.txt'), $_.Exception.ToString())
    throw
}
exit 0
