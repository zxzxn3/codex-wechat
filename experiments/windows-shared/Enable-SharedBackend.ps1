[CmdletBinding()]
param([switch]$Disable, [string]$Source)
$ErrorActionPreference='Stop'
$configPath=Join-Path $env:LOCALAPPDATA 'codex-wechat\config.json'
$runtimeRoot=Join-Path $env:LOCALAPPDATA 'codex-wechat\shared'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$settingsPath=Join-Path $runtimeRoot 'shared-backend.json'
if(-not $Disable){
    if(-not $Source -and (Test-Path -LiteralPath $settingsPath)){
        $previous=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8|ConvertFrom-Json
        $Source=$previous.source
    }
    if(-not $Source){throw 'First activation requires an explicit -Source.'}
    & (Join-Path $PSScriptRoot 'Repair-Wechat.ps1') -Source $Source -RequireShared
}
$config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8|ConvertFrom-Json
$backend=$config.backends.codex
if($Disable){
    $backend.PSObject.Properties.Remove('websocketUrl')
    $backend.PSObject.Properties.Remove('websocketStartupCommand')
    if(Test-Path -LiteralPath $settingsPath){Move-Item -LiteralPath $settingsPath -Destination ($settingsPath+'.disabled') -Force}
} else {
    if(Test-Path -LiteralPath $settingsPath){$settings=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8|ConvertFrom-Json}
    else {$settings=[pscustomobject]@{url='ws://127.0.0.1:45231';proxy=$backend.env.HTTPS_PROXY}}
    $settings|Add-Member -MemberType NoteProperty -Name source -Value ([IO.Path]::GetFullPath($Source)) -Force
    $backup=$configPath+'.before-shared-backend'
    if(-not(Test-Path -LiteralPath $backup)){Copy-Item -LiteralPath $configPath -Destination $backup}
    $backend|Add-Member -MemberType NoteProperty -Name websocketUrl -Value $settings.url -Force
    $command=@("$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe",'-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $env:LOCALAPPDATA 'codex-wechat\app\codex-desktop.ps1'),'ensure')
    $backend|Add-Member -MemberType NoteProperty -Name websocketStartupCommand -Value $command -Force
    [IO.File]::WriteAllText($settingsPath,($settings|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
}
$temp=$configPath+'.tmp-'+[Guid]::NewGuid().ToString('N')
try {
    [IO.File]::WriteAllText($temp,($config|ConvertTo-Json -Depth 50),(New-Object Text.UTF8Encoding($false)))
    [IO.File]::Replace($temp,$configPath,[System.Management.Automation.Language.NullString]::Value)
} finally {if(Test-Path -LiteralPath $temp){Remove-Item -LiteralPath $temp -Force}}
if($Disable){Write-Host 'Shared routing disabled for future starts. The existing backend was not stopped.'}
else {
    Write-Host 'Shared routing configured; no current Codex process was stopped.'
    Write-Host 'Fully quit the old Codex App, then launch Codex from the launcher entry.'
    Write-Host 'Start the WeChat bridge and retry /switch after the App reopens.'
}