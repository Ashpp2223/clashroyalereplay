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
    # Local CN edition uses the same executable and frozen bridge. The shortcut
    # selects the verified adjacent research environment for this child only.
    Copy-Item -LiteralPath src-tauri/target/release/clash-replay-tree.exe -Destination $output
    Copy-Item -LiteralPath src-tauri/resources/native-bridge -Destination $output -Recurse -Force
    $researchRoot = Join-Path (Split-Path -Parent $repo) 'clashroyale1016'
    if (Test-Path -LiteralPath (Join-Path $researchRoot 'analysis/recorder-profile.json')) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $output '皇室战争复盘器-国服38.4.5.lnk'))
        $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $repo 'scripts/start_cn_recorder.ps1') + '"'
        $shortcut.WorkingDirectory = $repo
        $shortcut.IconLocation = (Join-Path $output 'clash-replay-tree.exe') + ',0'
        $shortcut.Save()
        Copy-Item -LiteralPath docs/cn-engine-integration.md -Destination (Join-Path $output '国服版本说明.md')
        Copy-Item -LiteralPath docs/engine-upgrade-playbook.md -Destination (Join-Path $output '引擎升级知识库.md')
    }
    $digest = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksums = "$digest  $($built.Name)`n"
    foreach ($relative in @('clash-replay-tree.exe', 'native-bridge/native-bridge.exe')) {
        $fileDigest = (Get-FileHash -LiteralPath (Join-Path $output $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        $checksums += "$fileDigest  $relative`n"
    }
    [IO.File]::WriteAllText((Join-Path $output 'SHA256SUMS.txt'), $checksums, [Text.UTF8Encoding]::new($false))
    Write-Output "installer=$target"
} finally { Pop-Location }
