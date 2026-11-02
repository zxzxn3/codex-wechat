# Native executable discovery shared by the backend and terminal entry.
function Resolve-CodexExecutable([object]$Settings) {
    $configured=$Settings.PSObject.Properties['executable']
    if($configured -and $configured.Value){
        $path=[string]$configured.Value
        if(-not [IO.Path]::IsPathRooted($path) -or [IO.Path]::GetExtension($path) -ne '.exe' -or
            -not (Test-Path -LiteralPath $path -PathType Leaf)){
            throw 'Shared profile executable must point to an existing native .exe.'
        }
        return [IO.Path]::GetFullPath($path)
    }
    $native=Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if($native){return $native.Source}
    $standalone=Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin\codex.exe'
    if(Test-Path -LiteralPath $standalone -PathType Leaf){return $standalone}
    $package=Get-AppxPackage OpenAI.Codex | Select-Object -First 1
    if($package){
        $bundled=Join-Path $package.InstallLocation 'app\resources\codex.exe'
        if(Test-Path -LiteralPath $bundled -PathType Leaf){return $bundled}
    }
    throw 'Native codex.exe not found. Install Codex CLI or set executable in shared-backend.json.'
}
