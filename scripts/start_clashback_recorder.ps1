param(
    [string]$Runtime = (Join-Path (Split-Path -Parent $PSScriptRoot) '.local-runtime\clashback-160635001'),
    [string]$App = (Join-Path (Split-Path -Parent $PSScriptRoot) 'release\native-desktop\clash-replay-tree.exe')
)
$ErrorActionPreference = 'Stop'
$appPath = (Resolve-Path -LiteralPath $App).Path
$env:CR_CLASHBACK_ROOT = (Resolve-Path -LiteralPath $Runtime).Path
$env:CR_ENGINE_PROFILE = 'clashback-160635001'
Remove-Item Env:CR_DESKTOP_TEST_HOME, Env:WEBVIEW2_USER_DATA_FOLDER, Env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS -ErrorAction SilentlyContinue
Start-Process -FilePath $appPath -WorkingDirectory (Split-Path -Parent $appPath) -WindowStyle Normal
