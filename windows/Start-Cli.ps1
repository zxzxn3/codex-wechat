#requires -Version 5.1
# No parameter binder: forward native Codex arguments (including -C/-m) unchanged.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'SharedPaths.ps1')
$forward=@($args)
foreach($argument in $forward){
    if($argument -match '^--(remote|remote-auth-token-env|no-daemon)(=|$)'){
        throw 'This entry uses the shared profile. Use native codex for another backend.'
    }
}
$settings=Get-Content -LiteralPath (Join-Path $runtimeRoot 'shared-backend.json') -Raw -Encoding UTF8 | ConvertFrom-Json
. (Join-Path $PSScriptRoot 'Resolve-CodexExecutable.ps1')
$keys=@('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','CODEX_THREAD_ID','CODEX_SESSION_ID')
$saved=@{}
foreach($key in $keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
$exitCode=1
try {
    # Reuse exactly the same startup/identity/readiness checks as App and WeChat.
    & (Join-Path $PSScriptRoot 'Start-SharedBackend.ps1')
    $record=Get-Content -LiteralPath (Join-Path $runtimeRoot 'shared-backend-process.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    # Prefer the verified running server's binary to avoid client/server version drift.
    $exe=if(Test-Path -LiteralPath $record.executable -PathType Leaf){$record.executable}else{Resolve-CodexExecutable $settings}
    foreach($key in @('CODEX_THREAD_ID','CODEX_SESSION_ID')){[Environment]::SetEnvironmentVariable($key,$null,'Process')}
    if($settings.proxy){
        foreach($key in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')){[Environment]::SetEnvironmentVariable($key,$settings.proxy,'Process')}
    }
    $env:NO_PROXY=(@('localhost','127.0.0.1','::1') + @($saved['NO_PROXY'] -split ',') | Where-Object {$_} | Select-Object -Unique) -join ','
    $invocation=@('--remote',[string]$settings.url) + $forward
    # Inherit the current terminal and directory; do not redirect the interactive TUI.
    & $exe @invocation
    $exitCode=$LASTEXITCODE
} catch {
    Add-Content -LiteralPath (Join-Path $runtimeRoot 'shared-cli.log') -Encoding UTF8 -Value ('{0:o} ERROR {1}' -f (Get-Date),$_.Exception.Message)
    throw
} finally {
    foreach($key in $keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
}
exit $exitCode
