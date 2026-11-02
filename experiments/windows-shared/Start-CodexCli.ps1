$ErrorActionPreference='Stop'
$entry=Join-Path $env:LOCALAPPDATA 'codex-wechat\app\codex-desktop.ps1'
& $entry cli @args
exit $LASTEXITCODE
