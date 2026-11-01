#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$Ref = 'main',
    [string]$Dir,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [string]$Component,
    [string]$SkillsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoUri = [Uri]$Repo
$RepositoryPath = $RepoUri.AbsolutePath.Trim('/') -replace '\.git$', ''
if ($RepoUri.Scheme -ne 'https' -or $RepoUri.Authority -ne 'github.com' -or $RepoUri.UserInfo -or
        $RepoUri.Query -or $RepoUri.Fragment -or
        $RepositoryPath -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$' -or
        -not $Ref -or $Ref.StartsWith('-') -or $Ref -match '[\s\x00-\x1f]') {
    throw 'A public GitHub HTTPS repository and a branch or tag are required.'
}
if ($DryRun) {
    Write-Host "Dry run: no download or file changes. Source: $Repo ($Ref)"
    Write-Host 'Target directory checks are bypassed because of -DryRun; this is not an environment preflight.'
    Write-Host '1. Install / Update  2. Components  0. Exit'
    Write-Host 'The installer will show the plan; no login, new autostart or service start.'
    return
}

$PythonCommand = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
$PythonPrefix = @('-3')
if (-not $PythonCommand) {
    $PythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    $PythonPrefix = @()
}
if (-not $PythonCommand) { throw 'Python 3.11+ is required. Install it and rerun the same command.' }
& $PythonCommand.Source @PythonPrefix -c 'import sys; sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required. Install it and rerun the same command.' }
if (-not (Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue)) {
    throw 'Git is required. Install it and rerun the same command.'
}
if (-not $NonInteractive -and ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected)) {
    throw 'Installation needs an interactive terminal. Use -NonInteractive for conservative defaults, or -DryRun.'
}
$TaskTemporary = Join-Path ([IO.Path]::GetTempPath()) ('codex-wechat-bootstrap-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $TaskTemporary | Out-Null
try {
    $Installer = Join-Path $TaskTemporary 'installer.py'
    $InstallerUri = 'https://raw.githubusercontent.com/' + $RepositoryPath + '/' + [Uri]::EscapeDataString($Ref) + '/installer.py'
    Invoke-WebRequest -UseBasicParsing -Uri $InstallerUri -OutFile $Installer -TimeoutSec 120
    $InstallerArguments = @($Installer, 'bootstrap', '--repo', $Repo, '--ref', $Ref)
    if ($Component) {
        $InstallerArguments[1] = 'components'
        $InstallerArguments += @('--component', $Component)
    }
    if ($Dir) { $InstallerArguments += @('--dir', $Dir) }
    if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    if ($SkillsDir) { $InstallerArguments += @('--skills-dir', $SkillsDir) }
    & $PythonCommand.Source @PythonPrefix -u @InstallerArguments
    if ($LASTEXITCODE -ne 0) { throw "Installer failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item -LiteralPath $TaskTemporary -Recurse -Force
}
