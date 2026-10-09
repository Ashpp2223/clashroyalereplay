param(
    [string]$InputApk = 'C:\MyProject\皇室战争逆向工程\nr_15.535.13_release_ff1c6c29.apk',
    [string]$Python = 'python',
    [string]$Java = 'C:\Users\pp\AppData\Local\Android\Jdk17\jdk-17.0.20+8\bin\java.exe',
    [string]$Keytool = 'C:\Users\pp\AppData\Local\Android\Jdk17\jdk-17.0.20+8\bin\keytool.exe',
    [string]$Apktool = 'C:\MyProject\皇室战争逆向工程\analysis\tools\android\apktool_3.0.2.jar',
    [string]$BuildTools = 'C:\Users\pp\AppData\Local\Android\Sdk\build-tools\36.0.0',
    [switch]$UpdateOnly
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    $probe = Join-Path $repo 'vendor\clash-royale-battle-engine\prebuilt\libcrprobe.so'
    foreach ($path in @($InputApk, $Java, $Keytool, $Apktool, $BuildTools, $probe)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing required input: $path" }
    }
    & $Python -m scripts.prepare_probe_apk check-input --apk $InputApk
    if ($LASTEXITCODE -ne 0) { throw 'Original APK validation failed' }
    $localRoot = Join-Path $repo '.local-runtime'
    # aapt2 cannot reliably handle Chinese directory names on this Windows host.
    $buildRoot = Join-Path $env:TEMP ('cr-probe-build-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $decoded = Join-Path $buildRoot 'decoded'
    $framework = Join-Path $buildRoot 'framework'
    New-Item -ItemType Directory -Path $buildRoot,$framework -Force | Out-Null
    & $Java -jar $Apktool d $InputApk -p $framework -o $decoded
    if ($LASTEXITCODE -ne 0) { throw 'Pristine APK decode failed' }
    $modeArguments = @()
    if ($UpdateOnly) { $modeArguments = @('--update-only') }
    & $Python -m scripts.prepare_probe_apk patch --source $decoded @modeArguments
    if ($LASTEXITCODE -ne 0) { throw 'Pinned native bootstrap patch failed' }
    if (-not $UpdateOnly) {
        Copy-Item -LiteralPath $probe -Destination (Join-Path $decoded 'lib\arm64-v8a\libcrprobe.so')
    }
    $unsigned = Join-Path $buildRoot 'unsigned.apk'
    $aligned = Join-Path $buildRoot 'aligned.apk'
    $signed = Join-Path $buildRoot 'signed.apk'
    & $Java -jar $Apktool b $decoded -p $framework -o $unsigned
    if ($LASTEXITCODE -ne 0) { throw 'APK build failed' }
    & (Join-Path $BuildTools 'zipalign.exe') -P 16 -f 4 $unsigned $aligned
    if ($LASTEXITCODE -ne 0) { throw 'APK alignment failed' }
    $signing = Join-Path $localRoot 'signing'
    New-Item -ItemType Directory -Path $signing -Force | Out-Null
    $keystore = Join-Path $signing 'native-debug.keystore'
    if (-not (Test-Path -LiteralPath $keystore)) {
        & $Keytool -genkeypair -keystore $keystore -storepass android -keypass android `
            -alias native-replay -keyalg RSA -keysize 2048 -validity 3650 `
            -dname 'CN=Native Replay Local Debug,OU=Local,O=Local,C=CN' -noprompt
        if ($LASTEXITCODE -ne 0) { throw 'Local signing key creation failed' }
    }
    $env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $Java)
    & (Join-Path $BuildTools 'apksigner.bat') sign --ks $keystore --ks-key-alias native-replay `
        --ks-pass pass:android --key-pass pass:android --out $signed $aligned
    if ($LASTEXITCODE -ne 0) { throw 'APK signing failed' }
    & (Join-Path $BuildTools 'apksigner.bat') verify $signed
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed' }
    & $Python -m scripts.prepare_probe_apk verify --apk $signed --original-apk $InputApk @modeArguments
    if ($LASTEXITCODE -ne 0) { throw 'Output payload verification failed' }
    $hash = (Get-FileHash -LiteralPath $signed -Algorithm SHA256).Hash.ToLowerInvariant()
    $outputPrefix = 'prepared'
    if ($UpdateOnly) { $outputPrefix = 'update' }
    $prepared = Join-Path $localRoot ($outputPrefix + '-' + $hash.Substring(0, 16) + '.apk')
    Copy-Item -LiteralPath $signed -Destination $prepared
    Copy-Item -LiteralPath ($signed -replace '\.apk$', '.build.json') -Destination ($prepared -replace '\.apk$', '.build.json')
    $source = 'pristine-explicit-probe-bootstrap'
    if ($UpdateOnly) { $source = 'pristine-direct-libg-update' }
    @{ apkSha256 = $hash; path = $prepared; originalApk = $InputApk; source = $source } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localRoot ($outputPrefix + '.json')) -Encoding UTF8
    Write-Output "prepared_apk=$prepared"
    if ($UpdateOnly) {
        Write-Output 'Use bridge.install_runtime --update-apk to test in-game resource updates without a probe.'
    } else {
        Write-Output 'Install after the game resource update, using bridge.install_runtime --apk.'
    }
} finally {
    Pop-Location
}
