#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Repo = 'https://github.com/zxzxn3/codex-wechat',
    [string]$Ref = 'main',
    [string]$Dir,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [string]$Component,
    [string]$SkillsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-DependencyHint([string]$Name, [string]$WingetId, [string]$Url) {
    return @"
$Name is required.
Install it with:
  winget install --id $WingetId -e
If winget is unavailable: $Url
Close and reopen this terminal, then rerun the same command.
"@
}

$RepoUri = [Uri]$Repo
$RepositoryPath = $RepoUri.AbsolutePath.Trim('/') -replace '\.git$', ''
if ($RepoUri.Scheme -ne 'https' -or $RepoUri.Authority -ne 'github.com' -or $RepoUri.UserInfo -or
        $RepoUri.Query -or $RepoUri.Fragment -or
        $RepositoryPath -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$' -or
        -not $Ref -or $Ref.StartsWith('-') -or $Ref -match '[\s\x00-\x1f]') {
    throw 'A GitHub HTTPS repository and a branch or tag are required.'
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
    $PythonCommand = @(Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue) |
        Where-Object {
            (Test-Path -LiteralPath $_.Source -PathType Leaf) -and
            (Get-Item -LiteralPath $_.Source).Length -gt 0
        } | Select-Object -First 1
    $PythonPrefix = @()
}
if (-not $PythonCommand) {
    throw (Get-DependencyHint 'Python 3.11+' 'Python.Python.3.13' 'https://www.python.org/downloads/windows/')
}
& $PythonCommand.Source @PythonPrefix -c 'import sys; sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) {
    throw (Get-DependencyHint 'Python 3.11+' 'Python.Python.3.13' 'https://www.python.org/downloads/windows/')
}
if (-not (Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue)) {
    throw (Get-DependencyHint 'Git' 'Git.Git' 'https://git-scm.com/downloads')
}
if (-not $NonInteractive -and ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected)) {
    throw 'Installation needs an interactive terminal. Use -NonInteractive for conservative defaults, or -DryRun.'
}
$TaskTemporary = Join-Path ([IO.Path]::GetTempPath()) ('codex-wechat-bootstrap-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $TaskTemporary | Out-Null
try {
    $Installer = Join-Path $TaskTemporary 'installer.py'
    $InstallerBaseUri = 'https://raw.githubusercontent.com/' + $RepositoryPath + '/' + [Uri]::EscapeDataString($Ref)
    $InstallerUri = $InstallerBaseUri + '/installer.py'
    Invoke-WebRequest -UseBasicParsing -Uri $InstallerUri -OutFile $Installer -TimeoutSec 120
    $InstallerArguments = @($Installer, 'bootstrap', '--repo', $Repo, '--ref', $Ref)
    if ($Component) {
        $InstallerArguments[1] = 'components'
        $InstallerArguments += @('--component', $Component)
        $InstallerArguments += @('--repo', $Repo, '--ref', $Ref)
    }
    if ($Dir) { $InstallerArguments += @('--dir', $Dir) }
    if ($NonInteractive) { $InstallerArguments += '--non-interactive' }
    if ($SkillsDir) { $InstallerArguments += @('--skills-dir', $SkillsDir) }
    & $PythonCommand.Source @PythonPrefix -u @InstallerArguments
    if ($LASTEXITCODE -ne 0) { throw "Installer failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item -LiteralPath $TaskTemporary -Recurse -Force
}
