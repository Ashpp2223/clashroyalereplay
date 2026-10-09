param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    $buildPython = Join-Path $repo '.local-runtime\package-env\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $Python -m venv .local-runtime/package-env
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create build environment' }
    }
    & $buildPython -m pip install -r bridge/requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install pinned packaging dependencies' }
    & npm.cmd run test:native
    if ($LASTEXITCODE -ne 0) { throw 'Native contract tests failed' }
    & $buildPython -m scripts.build_desktop_bridge
    if ($LASTEXITCODE -ne 0) { throw 'Bridge packaging failed' }
    & npm.cmd run tauri:build -- --bundles nsis
    if ($LASTEXITCODE -ne 0) { throw 'Desktop installer build failed' }
    $built = Get-ChildItem -LiteralPath src-tauri/target/release/bundle/nsis -Filter '*-setup.exe' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $built) { throw 'Installer was not produced' }
    $output = Join-Path $repo 'release\native-desktop'
    New-Item -ItemType Directory -Path $output -Force | Out-Null
    $target = Join-Path $output $built.Name
    Copy-Item -LiteralPath $built.FullName -Destination $target
    Copy-Item -LiteralPath docs/native-desktop-user-guide.md -Destination (Join-Path $output '使用说明.md')
    $digest = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    "$digest  $($built.Name)" | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ASCII
    Write-Output "installer=$target"
} finally { Pop-Location }
