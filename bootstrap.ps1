#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Dir,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [string]$SourceDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$UvVersion = '0.13.7'
$UvPython = '3.11'
$RepositoryPath = 'zxzxn3/codex-wechat'
$SourceRef = 'main'

function Restore-EnvironmentValue([string]$Name, [AllowNull()][string]$Value) {
    if ($null -eq $Value) {
        Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
    } else {
        Set-Item "Env:$Name" $Value
    }
}

if ($DryRun -and -not $SourceDir) {
    Write-Host "Dry run: no download or file changes. Source: https://github.com/$RepositoryPath ($SourceRef)"
    Write-Host 'Target directory checks are bypassed because of -DryRun; this is not an environment preflight.'
    Write-Host '1. Install / Update  0. Exit'
    Write-Host 'The installer will show the plan; no login, new autostart or service start.'
    return
}

$TaskTemporary = Join-Path ([IO.Path]::GetTempPath()) ('codex-wechat-bootstrap-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $TaskTemporary | Out-Null
$SavedEnvironment = @{}
foreach ($name in @(
        'UV_CACHE_DIR', 'UV_MANAGED_PYTHON', 'UV_NO_CONFIG', 'UV_NO_MODIFY_PATH',
        'UV_NO_PROGRESS', 'UV_PYTHON', 'UV_PYTHON_INSTALL_BIN',
        'UV_PYTHON_INSTALL_DIR',
        'CODEX_WECHAT_BOOTSTRAP_UV', 'PYTHONPATH', 'PYTHONHOME')) {
    $SavedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $UvPath = (Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1).Source
    $UvVersionMatches = $false
    if ($UvPath) {
        $UvVersionOutput = [string](& $UvPath --version 2>$null)
        $UvVersionMatches = $UvVersionOutput -match
            ("^uv " + [regex]::Escape($UvVersion) + "(\s|$)")
    }
    if (-not $UvPath -or -not $UvVersionMatches) {
        $UvRoot = Join-Path $TaskTemporary 'uv'
        $UvInstaller = Join-Path $TaskTemporary 'uv-install.ps1'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 `
            -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $UvInstaller
        $env:UV_UNMANAGED_INSTALL = $UvRoot
        & $UvInstaller
        Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
        $UvPath = Join-Path $UvRoot 'uv.exe'
    }
    if (-not $UvPath -or -not (Test-Path -LiteralPath $UvPath -PathType Leaf)) {
        throw 'uv installation failed.'
    }

    $env:UV_CACHE_DIR = Join-Path $TaskTemporary 'cache'
    $env:UV_MANAGED_PYTHON = '1'
    $env:UV_NO_CONFIG = '1'
    $env:UV_NO_MODIFY_PATH = '1'
    $env:UV_NO_PROGRESS = '1'
    $env:UV_PYTHON = $UvPython
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $TaskTemporary 'python'
    $env:CODEX_WECHAT_BOOTSTRAP_UV = $UvPath

    & $UvPath python install $UvPython
    if ($LASTEXITCODE) { throw 'uv could not install Python 3.11.' }
    $PythonCommand = (& $UvPath python find --managed-python $UvPython).Trim()
    if ($LASTEXITCODE -or -not (Test-Path -LiteralPath $PythonCommand -PathType Leaf)) {
        throw 'uv-managed Python 3.11 is unavailable.'
    }

    if ($SourceDir) {
        $Installer = Join-Path $SourceDir 'installer.py'
        $InstallerArguments = @($Installer, 'menu', '--source', $SourceDir)
        if ($Dir) { $InstallerArguments += @('--dir', $Dir) }
        if ($DryRun) { $InstallerArguments += '--dry-run' }
        if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    } else {
        $Installer = Join-Path $TaskTemporary 'installer.py'
        $InstallerUri = 'https://raw.githubusercontent.com/' + $RepositoryPath + '/' +
            [Uri]::EscapeDataString($SourceRef) + '/installer.py'
        Invoke-WebRequest -UseBasicParsing -Uri $InstallerUri -OutFile $Installer -TimeoutSec 120
        $InstallerArguments = @($Installer, 'bootstrap')
        if ($Dir) { $InstallerArguments += @('--dir', $Dir) }
        if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    }
    & $PythonCommand -I -u @InstallerArguments
    if ($LASTEXITCODE) { throw "Installer failed with exit code $LASTEXITCODE" }
} finally {
    foreach ($name in $SavedEnvironment.Keys) {
        Restore-EnvironmentValue $name $SavedEnvironment[$name]
    }
    Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $TaskTemporary -Recurse -Force -ErrorAction SilentlyContinue
}
