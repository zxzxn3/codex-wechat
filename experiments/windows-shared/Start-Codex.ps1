#requires -Version 5.1
$ErrorActionPreference='Stop'
$logDirectory=Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$log=Join-Path $logDirectory 'proxy-launcher.log'
$names=@('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')
$saved=@{}
foreach($name in $names){$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
try {
    $proxy=$env:HTTPS_PROXY
    if(-not $proxy){
        $system=Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        if(-not $system.ProxyEnable -or -not $system.ProxyServer){throw 'Enable the system proxy or provide HTTPS_PROXY.'}
        $proxy=[string]$system.ProxyServer
        if($proxy -match '='){
            $entries=@{}
            foreach($part in $proxy.Split(';')){$pair=$part.Split('=',2);if($pair.Count -eq 2){$entries[$pair[0]]=$pair[1]}}
            $proxy=if($entries['https']){$entries['https']}else{$entries['http']}
        }
        if(-not $proxy){throw 'No HTTP/HTTPS proxy endpoint is configured.'}
        if($proxy -notmatch '^https?://'){$proxy='http://'+$proxy}
    }
    foreach($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')){[Environment]::SetEnvironmentVariable($name,$proxy,'Process')}
    $env:NO_PROXY=(@('localhost','127.0.0.1','::1') + @($saved['NO_PROXY'] -split ',') | Where-Object {$_} | Select-Object -Unique) -join ','
    $entry=Join-Path $env:LOCALAPPDATA 'codex-wechat\app\codex-desktop.ps1'
    if(-not(Test-Path -LiteralPath $entry -PathType Leaf)){throw 'Update codex-wechat before using this launcher.'}
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0:o} Delegating to codex-wechat desktop entry.' -f (Get-Date))
    & $entry app
    if($LASTEXITCODE -ne 0){throw ('codex-wechat desktop entry failed, exit '+$LASTEXITCODE)}
} catch {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0:o} ERROR {1}' -f (Get-Date),$_.Exception.Message)
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(($_.Exception.Message+"`n`nLog: "+$log),'Codex proxy launcher') | Out-Null
    exit 1
} finally {
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
}
