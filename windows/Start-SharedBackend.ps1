#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Url,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$uri = [Uri]$Url
if ($uri.Scheme -ne 'ws' -or $uri.Host -notin @('127.0.0.1','localhost','[::1]','::1') -or
        $uri.Port -lt 1 -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.UserInfo -or $uri.Fragment) {
    throw 'Only a loopback WebSocket endpoint is allowed.'
}
if (-not [IO.Path]::IsPathRooted($Executable) -or [IO.Path]::GetExtension($Executable) -ne '.exe' -or
        -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw 'An existing native exe is required.' }
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
$recordPath = Join-Path $RuntimeRoot 'shared-backend-process.json'
$hash = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($RuntimeRoot.ToLowerInvariant()))).Replace('-','') }
finally { $hash.Dispose() }
$mutex = New-Object Threading.Mutex -ArgumentList $false,('Local\CodexWechatBackend-' + $key)
$held = $false
try {
    try { $held=$mutex.WaitOne(20000) } catch [Threading.AbandonedMutexException] { $held=$true }
    if (-not $held) { throw 'Another backend startup is still running.' }
    $process = $null
    if (Test-Path -LiteralPath $recordPath) {
        $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.pid) -ErrorAction SilentlyContinue
        if ($candidate) {
            if (-not $candidate.ExecutablePath -or $candidate.ExecutablePath -ne $record.executable -or
                    $record.creationTimeUtc -ne $candidate.CreationDate.ToUniversalTime().ToString('o') -or
                    $record.url -ne $Url) { throw 'Recorded backend identity no longer matches.' }
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $uri.Port -ErrorAction SilentlyContinue)
            if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -notin @('127.0.0.1','::1') -or
                    $listeners[0].OwningProcess -ne $candidate.ProcessId) { throw 'Recorded backend does not own the loopback listener.' }
            $process = Get-Process -Id $candidate.ProcessId -ErrorAction Stop
        }
    }
    if (-not $process) {
        if (@(Get-NetTCPConnection -State Listen -LocalPort $uri.Port -ErrorAction SilentlyContinue).Count) {
            throw 'Port already occupied; refusing to attach to an unknown server.'
        }
        # Network environment is inherited from the caller's proxy launcher.
        # This component never discovers, stores, or changes proxy settings.
        $process = Start-Process -FilePath $Executable -ArgumentList @('app-server','--listen',$Url) -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $RuntimeRoot 'backend.stdout.log') `
            -RedirectStandardError (Join-Path $RuntimeRoot 'backend.stderr.log')
        $metadata = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $process.Id)
        if (-not $metadata.CreationDate) { throw 'Cannot record backend creation time.' }
        $record = @{pid=$process.Id;url=$Url;executable=$Executable;creationTimeUtc=$metadata.CreationDate.ToUniversalTime().ToString('o')}
        $temporary = $recordPath + '.tmp'
        [IO.File]::WriteAllText($temporary, ($record|ConvertTo-Json), (New-Object Text.UTF8Encoding -ArgumentList $false))
        Move-Item -LiteralPath $temporary -Destination $recordPath -Force
    }
    $deadline = (Get-Date).AddSeconds(15)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) { throw 'Shared backend exited; read backend.stderr.log.' }
        try {
            $request = [Net.HttpWebRequest]::Create(('http://' + $uri.Authority + '/readyz'))
            $request.Proxy = $null
            $request.Timeout = 700
            $response = $request.GetResponse()
            try { $ready=([int]$response.StatusCode -eq 200) } finally { $response.Close() }
            if ($ready) { break }
        } catch {}
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready) { throw 'Shared backend did not become ready.' }
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
