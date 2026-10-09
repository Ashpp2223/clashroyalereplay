param(
    [string]$Sdk = 'C:\Users\pp\AppData\Local\Android\Sdk',
    [int]$ApiLevel = 31,
    [string]$Name = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Name) { $Name = 'CR_Dev_API' + $ApiLevel }
if ($Name -notmatch '^[A-Za-z0-9_-]+$' -or $ApiLevel -lt 30) { throw 'Invalid development device configuration' }
$repo = Split-Path -Parent $PSScriptRoot
$avdDirectory = Join-Path $repo '.local-runtime\avd'
$deviceDirectory = Join-Path $avdDirectory ($Name + '.avd')
$systemDirectory = Join-Path $Sdk ('system-images\android-' + $ApiLevel + '\google_apis\x86_64')
foreach ($file in @('system.img','source.properties','kernel-ranchu')) {
    if (-not (Test-Path -LiteralPath (Join-Path $systemDirectory $file))) { throw "Missing Android development image file: $file" }
}
if (-not (Test-Path -LiteralPath (Join-Path $Sdk 'emulator\emulator.exe'))) { throw 'Android Emulator is not installed' }
New-Item -ItemType Directory -Path $deviceDirectory -Force | Out-Null
$configPath = Join-Path $deviceDirectory 'config.ini'
$text = ''
if (Test-Path -LiteralPath $configPath) { $text = [IO.File]::ReadAllText($configPath) }
# The official emulator reads this documented AVD configuration directly. It
# does not require a matching Android platform/JDK or a running Android Studio.
$settings = @{
    'avd.id'=$Name; 'avd.name'=$Name; 'avd.ini.encoding'='UTF-8'; 'abi.type'='x86_64';
    'image.sysdir.1'=($systemDirectory.Replace('\','/') + '/'); 'tag.id'='google_apis';
    'tag.display'='Google APIs'; 'PlayStore.enabled'='no'; 'hw.cpu.arch'='x86_64';
    'hw.lcd.width'='1080'; 'hw.lcd.height'='1920'; 'hw.lcd.density'='320';
    'hw.lcd.depth'='32'; 'hw.initialOrientation'='portrait';
    'hw.ramSize'='2048'; 'hw.cpu.ncore'='2'; 'hw.camera.front'='none'; 'hw.camera.back'='none';
    'hw.gpu.enabled'='yes'; 'hw.gpu.mode'='host'; 'hw.keyboard'='yes'; 'hw.mainKeys'='no';
    'disk.dataPartition.size'='10G'; 'disk.cachePartition'='yes'; 'disk.cachePartition.size'='66MB';
    'hw.audioInput'='no'; 'hw.audioOutput'='no'; 'hw.battery'='yes'; 'hw.gps'='no'
}
foreach ($entry in $settings.GetEnumerator()) {
    $pattern = '(?m)^' + [regex]::Escape($entry.Key) + '=.*$'
    if ([regex]::IsMatch($text, $pattern)) { $text = [regex]::Replace($text, $pattern, $entry.Key + '=' + $entry.Value) }
    else { $text += "`n" + $entry.Key + '=' + $entry.Value }
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($configPath, $text.Trim() + "`n", $utf8)
[IO.File]::WriteAllText((Join-Path $avdDirectory ($Name + '.ini')),
    "avd.ini.encoding=UTF-8`npath=$deviceDirectory`ntarget=android-$ApiLevel`n", $utf8)
@{ backend='android-sdk'; sdkRoot=$Sdk; avdName=$Name; avdHome=$avdDirectory; serial='emulator-5556'; gpu='host' } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repo '.local-runtime\runtime.json') -Encoding UTF8
Write-Output "Android API $ApiLevel Google APIs x86_64 development environment prepared; ARM64 support is verified after boot."
