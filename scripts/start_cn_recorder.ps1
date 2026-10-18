param(
    [string]$ResearchRoot = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'clashroyale1016'),
    [string]$App = (Join-Path (Split-Path -Parent $PSScriptRoot) 'release\native-desktop\clash-replay-tree.exe')
)
$ErrorActionPreference = 'Stop'
try {
    $researchPath = (Resolve-Path -LiteralPath $ResearchRoot).Path
    $appPath = (Resolve-Path -LiteralPath $App).Path
    foreach ($relative in @('analysis\recorder-profile.json', 'analysis\recorder_adapter.py',
                            '.runtime\runtime.json', '.runtime\content\verified.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $researchPath $relative) -PathType Leaf)) {
            throw "Missing verified CN runtime material: $relative"
        }
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $appPath
    $start.WorkingDirectory = Split-Path -Parent $appPath
    $start.UseShellExecute = $false
    $start.EnvironmentVariables['CR_ENGINE_PROFILE'] = 'cn-38.4.5'
    $start.EnvironmentVariables['CR_ENGINE_PORT'] = '26791'
    $start.EnvironmentVariables['CR_CAMP_RESEARCH_ROOT'] = $researchPath
    # Launch into the user's normal application data, even from a test shell.
    $start.EnvironmentVariables.Remove('CR_DESKTOP_TEST_HOME')
    $start.EnvironmentVariables.Remove('WEBVIEW2_USER_DATA_FOLDER')
    $start.EnvironmentVariables.Remove('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS')
    [System.Diagnostics.Process]::Start($start) | Out-Null
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'CN recorder startup') | Out-Null
    exit 1
}
