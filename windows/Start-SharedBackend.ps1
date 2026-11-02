$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. (Join-Path $PSScriptRoot 'SharedPaths.ps1')
if(-not(Test-Path -LiteralPath (Join-Path $runtimeRoot 'shared-backend.json'))){throw 'Shared backend profile is missing; configure codex-wechat shared mode first.'}
$log=Join-Path $runtimeRoot 'shared-backend.log'
$mutex=New-Object Threading.Mutex -ArgumentList $false,'Local\CodexWechatSharedBackend'
$held=$false
try {
    try {$held=$mutex.WaitOne(20000)} catch [Threading.AbandonedMutexException] {$held=$true}
    if(-not $held){throw 'Another shared-backend startup is still running.'}
    $settings=Get-Content -LiteralPath (Join-Path $runtimeRoot 'shared-backend.json') -Raw -Encoding UTF8|ConvertFrom-Json
    $url=[Uri]$settings.url
    if($url.Scheme -ne 'ws' -or $url.Host -ne '127.0.0.1' -or $url.Port -lt 1 -or $url.AbsolutePath -ne '/' -or $url.Query -or $url.UserInfo){throw 'Only the configured IPv4 loopback endpoint is allowed.'}
    . (Join-Path $PSScriptRoot 'Resolve-CodexExecutable.ps1')
    $exe=Resolve-CodexExecutable $settings
    $recordPath=Join-Path $runtimeRoot 'shared-backend-process.json'
    $process=$null
    if(Test-Path -LiteralPath $recordPath){
        $record=Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8|ConvertFrom-Json
        $candidate=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$record.pid) -ErrorAction SilentlyContinue
        if($candidate){
            $created=$candidate.CreationDate.ToUniversalTime().ToString('o')
            if(-not $record.creationTimeUtc -or $record.creationTimeUtc -ne $created -or
                -not $record.executable -or $record.url -ne $settings.url){
                throw 'Recorded PID, creation time or endpoint no longer matches this setup.'
            }
            $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $url.Port -ErrorAction SilentlyContinue)
            if($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1' -or
                $listeners[0].OwningProcess -ne $candidate.ProcessId){
                throw 'The recorded process does not exclusively own the expected loopback listener.'
            }
            if($record.executable -ne $exe){
                Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0:o} Codex executable changed; keeping the verified running backend. Restart it explicitly when idle.' -f (Get-Date))
            }
            $process=Get-Process -Id $candidate.ProcessId -ErrorAction Stop
        }
    }
    if(-not $process){
        $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $url.Port -ErrorAction SilentlyContinue)
        if($listeners.Count){throw 'The configured port is already occupied; refusing to attach to an unknown server.'}
        # No inherited thread context; the shared backend owns its own connections.
        foreach($key in @('CODEX_THREAD_ID','CODEX_SESSION_ID')){[Environment]::SetEnvironmentVariable($key,$null,'Process')}
        if($settings.proxy){
        foreach($key in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')){[Environment]::SetEnvironmentVariable($key,$settings.proxy,'Process')}
        }
        $env:NO_PROXY='localhost,127.0.0.1,::1'
        $arguments=@('app-server','--listen',$settings.url)
        $process=Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $env:USERPROFILE -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'shared-backend.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'shared-backend.stderr.log')
        $metadata=Get-CimInstance Win32_Process -Filter ('ProcessId='+$process.Id)
        if(-not $metadata.CreationDate){throw 'Cannot record backend creation time.'}
        [IO.File]::WriteAllText($recordPath,(@{pid=$process.Id;url=$settings.url;executable=$exe;creationTimeUtc=$metadata.CreationDate.ToUniversalTime().ToString('o')}|ConvertTo-Json))
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0:o} Started PID={1}' -f (Get-Date),$process.Id)
    }
    $ready=$false
    $deadline=(Get-Date).AddSeconds(15)
    while((Get-Date) -lt $deadline){
        if($process.HasExited){throw 'Shared backend exited; read shared-backend.stderr.log.'}
        try {
            $request=[Net.HttpWebRequest]::Create(('http://127.0.0.1:'+ $url.Port + '/readyz'))
            $request.Proxy=$null
            $request.Timeout=700
            $response=$request.GetResponse()
            try {$ready=([int]$response.StatusCode -eq 200)} finally {$response.Close()}
            if($ready){break}
        } catch {}
        Start-Sleep -Milliseconds 200
    }
    if(-not $ready){throw 'Shared backend did not become ready within 15 seconds.'}
} catch {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0:o} ERROR {1}' -f (Get-Date),$_.Exception.Message)
    throw
} finally {
    if($held){$mutex.ReleaseMutex()}
    $mutex.Dispose()
}
exit 0
