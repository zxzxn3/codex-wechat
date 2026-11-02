[CmdletBinding()]
param([string]$Source, [switch]$RequireShared)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$resultPath=Join-Path $PSScriptRoot 'wechat-repair-result.json'
$result=[ordered]@{updated=$false;error=$null}
try {
    $profilePath=Join-Path $env:LOCALAPPDATA 'codex-wechat\shared\shared-backend.json'
    $profile=$null
    if(Test-Path -LiteralPath $profilePath){$profile=Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8|ConvertFrom-Json}
    if(-not $Source -and $profile){$Source=$profile.source}
    if(-not $Source){throw 'Specify -Source explicitly; this command never guesses an older checkout.'}
    $source=[IO.Path]::GetFullPath($Source)
    $installer=Join-Path $source 'installer.py'
    if(-not(Test-Path -LiteralPath $installer -PathType Leaf)){throw 'The selected source has no installer.py.'}
    if($RequireShared -or ($profile -and $profile.url)){
        $declarationPath=Join-Path $source 'runtime-capabilities.json'
        if(-not(Test-Path -LiteralPath $declarationPath -PathType Leaf)){throw 'Selected source does not declare shared WebSocket support.'}
        $capabilities=Get-Content -LiteralPath $declarationPath -Raw -Encoding UTF8|ConvertFrom-Json
        if($capabilities.schema -ne 1 -or 'loopback-websocket' -notin $capabilities.transports){throw 'Selected source cannot serve the current shared mode.'}
    }
    if($profile -and $profile.proxy){
        foreach($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')){[Environment]::SetEnvironmentVariable($name,$profile.proxy,'Process')}
        $env:NO_PROXY='localhost,127.0.0.1,::1'
    }
    $target=Join-Path $env:LOCALAPPDATA 'codex-wechat\app'
    $python=(Get-Command py.exe -CommandType Application -ErrorAction Stop).Source
    $arguments=@('-3',('"'+$installer+'"'),'update','--source',('"'+$source+'"'),'--dir',('"'+$target+'"'),'--non-interactive')
    Write-Host '正在通过安装器更新；保留运行配置和微信绑定...'
    $process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $source -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput (Join-Path $PSScriptRoot 'wechat-update.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'wechat-update.stderr.log')
    if($process.ExitCode -ne 0){throw ('Installer update failed, exit '+$process.ExitCode)}
    $result.updated=$true
    $result.source=$source
    $result.release=(Get-Content -LiteralPath (Join-Path $target 'current') -Raw).Trim()
} catch {$result.error=$_.Exception.Message}
[IO.File]::WriteAllText($resultPath,($result|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
if($result.error){
    Write-Host ('未完成: '+$result.error) -ForegroundColor Red
    Write-Host ('日志: '+(Join-Path $PSScriptRoot 'wechat-update.stderr.log'))
    exit 1
}
Write-Host ('更新完成。版本: '+$result.release) -ForegroundColor Green
Write-Host '没有修改运行配置，没有启动或重启程序。'