#requires -Version 5.1
# Version-aware desktop entry. Raw args preserve native CLI flags and prompts.
$ErrorActionPreference='Stop'
$forward=@($args)
$action=if($forward.Count){[string]$forward[0]}else{'app'}
if($forward.Count -gt 1){$forward=@($forward[1..($forward.Count-1)])}else{$forward=@()}
if($action -notin @('app','cli','ensure')){throw 'Usage: codex-desktop.ps1 app | cli [Codex arguments] | ensure'}
if($action -ne 'cli' -and $forward.Count){throw 'Only cli accepts additional arguments.'}
$installRoot=if($env:CODEX_WECHAT_INSTALL_ROOT){[IO.Path]::GetFullPath($env:CODEX_WECHAT_INSTALL_ROOT)}elseif((Split-Path -Leaf (Split-Path -Parent $PSScriptRoot)) -eq 'releases'){Split-Path -Parent (Split-Path -Parent $PSScriptRoot)}else{$PSScriptRoot}
$project=$installRoot
$current=Join-Path $installRoot 'current'
if(Test-Path -LiteralPath $current){
    $item=Get-Item -LiteralPath $current -Force
    if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Invalid current version pointer.'}
    $identifier=(Get-Content -LiteralPath $current -Raw -Encoding UTF8).Trim()
    if($identifier -notmatch '^[a-f0-9]{16}$'){throw 'Invalid current version identifier.'}
    $project=Join-Path $installRoot ('releases\'+$identifier)
}
$script=Join-Path $project ('windows\'+@{app='Start-App.ps1';cli='Start-Cli.ps1';ensure='Start-SharedBackend.ps1'}[$action])
if(-not(Test-Path -LiteralPath $script -PathType Leaf)){throw ('Desktop integration file missing: '+$script)}
$keys=@('CODEX_APP_SERVER_WS_URL','CODEX_THREAD_ID','CODEX_SESSION_ID','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')
$saved=@{}
foreach($key in $keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
$code=0
try {
    foreach($key in @('CODEX_THREAD_ID','CODEX_SESSION_ID')){[Environment]::SetEnvironmentVariable($key,$null,'Process')}
    if($action -eq 'cli'){
        & $script @forward
        $code=$LASTEXITCODE
    } else {
        & $script
        $code=$LASTEXITCODE
    }
} finally {
    foreach($key in $keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
}
exit $code
