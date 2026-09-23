param(
    [string]$InstrumentedDecoded = 'C:\MyProject\皇室战争逆向工程\analysis\nr_15.535.13\instrumented\decoded_v1',
    [string]$Java = 'C:\Users\pp\AppData\Local\Android\Jdk17\jdk-17.0.20+8\bin\java.exe',
    [string]$Keytool = 'C:\Users\pp\AppData\Local\Android\Jdk17\jdk-17.0.20+8\bin\keytool.exe',
    [string]$Apktool = 'C:\MyProject\皇室战争逆向工程\analysis\tools\android\apktool_3.0.2.jar',
    [string]$BuildTools = 'C:\Users\pp\AppData\Local\Android\Sdk\build-tools\36.0.0',
    [string]$InstallSerial = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$vendorProbe = Join-Path $repo 'vendor\clash-royale-battle-engine\prebuilt\libcrprobe.so'
$expectedProbe = '04FA085250598E20096DDE4EB4CE4EB7CEBE2B8408925C0C05E691E50047C8FE'
$expectedLibg = '110AA2B5CAC391C498645E072B0D88729428C2C2845E7E2737CA8EE979059783'
$sourceLibg = Join-Path $InstrumentedDecoded 'lib\arm64-v8a\libg.so'
foreach ($path in @($InstrumentedDecoded, $Java, $Keytool, $Apktool, $BuildTools, $vendorProbe, $sourceLibg)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing required input: $path" }
}
if ((Get-FileHash -LiteralPath $vendorProbe -Algorithm SHA256).Hash -ne $expectedProbe) {
    throw 'Pinned probe hash does not match'
}
if ((Get-FileHash -LiteralPath $sourceLibg -Algorithm SHA256).Hash -ne $expectedLibg) {
    throw 'Source libg hash does not match the pinned engine'
}

$localRoot = Join-Path $repo '.local-runtime'
$buildRoot = Join-Path $env:TEMP ('cr-native-build-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$decoded = Join-Path $buildRoot 'decoded'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
Copy-Item -LiteralPath $InstrumentedDecoded -Destination $decoded -Recurse

$gameApp = Join-Path $decoded 'smali\com\supercell\clashroyale\GameApp.smali'
$smali = [System.IO.File]::ReadAllText($gameApp)
$from = 'const-string v0, "frida-gadget"'
$to = 'const-string v0, "crprobe"'
if (-not $smali.Contains($from)) { throw 'Instrumented GameApp loader has changed' }
[System.IO.File]::WriteAllText($gameApp, $smali.Replace($from, $to))

$nativeLibs = Join-Path $decoded 'lib\arm64-v8a'
foreach ($name in @('libfrida-gadget.so', 'libfrida-gadget.config.so')) {
    $path = Join-Path $nativeLibs $name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
Copy-Item -LiteralPath $vendorProbe -Destination (Join-Path $nativeLibs 'libcrprobe.so')

$unsigned = Join-Path $buildRoot 'native-unsigned.apk'
$aligned = Join-Path $buildRoot 'native-aligned.apk'
$signed = Join-Path $buildRoot 'native-signed.apk'
$env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $Java)
$env:Path = (Join-Path $env:JAVA_HOME 'bin') + [System.IO.Path]::PathSeparator + $env:Path
& $Java -jar $Apktool b $decoded -o $unsigned
if ($LASTEXITCODE -ne 0) { throw 'apktool build failed' }
& (Join-Path $BuildTools 'zipalign.exe') -P 16 -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw 'zipalign failed' }

$keystoreRoot = Join-Path $env:LOCALAPPDATA 'cr-native-replay'
New-Item -ItemType Directory -Path $keystoreRoot -Force | Out-Null
$keystore = Join-Path $keystoreRoot 'native-debug.keystore'
if (-not (Test-Path -LiteralPath $keystore)) {
    & $Keytool -genkeypair -keystore $keystore -storepass android -keypass android `
      -alias native-replay -keyalg RSA -keysize 2048 -validity 3650 `
      -dname 'CN=Native Replay Local Debug,OU=Local,O=Local,C=CN' -noprompt
    if ($LASTEXITCODE -ne 0) { throw 'keytool failed' }
}
& (Join-Path $BuildTools 'apksigner.bat') sign --ks $keystore --ks-key-alias native-replay `
  --ks-pass pass:android --key-pass pass:android --out $signed $aligned
if ($LASTEXITCODE -ne 0) { throw 'APK signing failed' }
& (Join-Path $BuildTools 'apksigner.bat') verify --verbose $signed
if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed' }

$hash = (Get-FileHash -LiteralPath $signed -Algorithm SHA256).Hash.ToLowerInvariant()
$prepared = Join-Path $localRoot ('prepared-' + $hash.Substring(0, 16) + '.apk')
Copy-Item -LiteralPath $signed -Destination $prepared
$manifest = @{ apkSha256 = $hash; path = $prepared; source = 'local-signed-probe-build' }
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localRoot 'prepared.json') -Encoding UTF8
Write-Output "prepared_apk=$prepared"
Write-Output "prepared_apk_sha256=$hash"
if ($InstallSerial) {
    $adb = Join-Path (Split-Path -Parent $BuildTools) 'platform-tools\adb.exe'
    & $adb -s $InstallSerial install -r $prepared
    if ($LASTEXITCODE -ne 0) { throw 'APK installation failed' }
    Write-Output "installed_serial=$InstallSerial"
}
