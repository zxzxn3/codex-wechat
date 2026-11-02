# One runtime location for App, CLI, and the WeChat backend startup hook.
$dataRoot=if($env:CODEX_WECHAT_DATA_ROOT){$env:CODEX_WECHAT_DATA_ROOT}else{Join-Path $env:LOCALAPPDATA 'codex-wechat'}
$runtimeRoot=Join-Path $dataRoot 'shared'
