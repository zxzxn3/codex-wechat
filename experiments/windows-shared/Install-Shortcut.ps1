$ErrorActionPreference='Stop'
$dir=$PSScriptRoot
$ws=New-Object -ComObject WScript.Shell
$programs=[Environment]::GetFolderPath('Programs')
$path=Join-Path $programs 'Codex.lnk'
$script=Join-Path $dir 'Start-Codex.ps1'
$hostLog=Join-Path $dir 'launcher-host.log'
$command="& { & '"+$script.Replace("'","''")+"' } *> '"+$hostLog.Replace("'","''")+"'"
if(Test-Path -LiteralPath $path){
 $old=$ws.CreateShortcut($path)
 if($old.Arguments -notmatch 'CodexProxy'){throw 'Existing Codex shortcut is not owned by this proxy setup.'}
}
$link=$ws.CreateShortcut($path)
$link.TargetPath="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$link.Arguments='-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "'+$command+'"'
$link.WorkingDirectory=$env:USERPROFILE
$link.WindowStyle=7
$package=Get-AppxPackage OpenAI.Codex | Select-Object -First 1
$link.IconLocation=(Join-Path $package.InstallLocation 'app\ChatGPT.exe')+',0'
$link.Description='Codex via local proxy 127.0.0.1:7897'
$link.Save()
$duplicate=Join-Path $programs 'Codex Proxy.lnk'
if(Test-Path -LiteralPath $duplicate){
 $old=$ws.CreateShortcut($duplicate)
 if($old.Arguments -match 'CodexProxy'){Remove-Item -LiteralPath $duplicate -Force}
}
[IO.File]::WriteAllText((Join-Path $dir 'shortcut-installed.json'),(@{Shortcut=$path;Arguments=$link.Arguments;Created=(Get-Date).ToString('o')}|ConvertTo-Json))