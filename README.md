# 皇室战争原生战斗复盘器

本地 Tauri + React 应用。中央窗口显示 Android 客户端渲染的原生战斗画面；左侧节点以引擎 tick 为时间轴，历史节点出牌会开启新分支并保留已有子分支。

## 当前实现

- 固定战斗接口和探针源码：`vendor/clash-royale-battle-engine`，提交 `f92ccbecd6a1c9ad1fa3abaca54a51110607b6df`。原版战斗规则由用户提供的 `libg.so` 执行。
- Python 桥接服务：严格检查 APK、`libg`、探针和运行时指纹；提供双方出牌、原生暂停/播放/变速/步进、快照与动作重放。
- React v2 界面：敌方原生 HUD、竞技场、我方原生 HUD 三段裁切；双方圣水观测、节点树、草稿分支、备注和项目保存。
- v2 文件：`.cr-native-replay.json`。旧 `.cr-replay.json` v1 文件不转换。
- 创建节点时先取得进程内原生状态快照；时间拖动优先从当前路线最近的快照续跑。快照句柄只在本次游戏进程中有效，不写入项目文件；重启后由动作日志重放并核对状态摘要。
- 未提交动作写入本机 journal；选择已保存项目时可恢复。

## 开发运行

```powershell
npm.cmd install
python -m pip install -r bridge\requirements.txt
npm.cmd run dev
```

桥接服务使用本机 Python。可用 `CR_BRIDGE_PYTHON` 指定解释器。H.264 连续画面需要 `imageio-ffmpeg`；不可用时回退到 ADB 截图。本机已验证的解释器是 `C:\Anaconda\python.exe`。

```powershell
npm.cmd run build
npm.cmd run test:domain
npm.cmd run test:native
cargo check --manifest-path src-tauri\Cargo.toml
```

## 本机运行时状态

当前路线是 Windows 界面 + 一套 Python 桥接 + 一个 ARM64 兼容 Android 游戏进程。运行时不需要训练框架或 Frida；画面由游戏绘制。当前接口、节点、快照及持久动作日志的代码已实现，**真实战斗闭环仍需实际引擎验收**。

`scripts/prepare_native_runtime.ps1` 从未修改的原版 APK 重新解包，添加探针的明确加载入口。构建会核对 `libg`、固定探针和全部 APK assets；临时构建使用英文路径以兼容 Windows aapt2。游戏文件和生成包只保存在被忽略的 `.local-runtime/`，不会随仓库分发。

`bridge/install_runtime.py` 在替换游戏包前，校验并备份旧 APK、应用数据及完整更新资源清单。签名变化需要卸载时会还原数据、修正 UID 和 SELinux 标签，再核对资源；失败会回滚，备份和恢复记录保留在选定设备及本机。桥接启动会先检查资源，再使用这个安装流程；空实例不会直接安装探针包。

用户已选择官方 Android SDK 开发模拟器，并要求卸载 MuMu。MuMu 主程序、运行实例及安装记录已移除。卸载前导出了应用数据和 `15.535.86` 更新资源，652 个更新文件与设备端逐份一致，保存在 `.local-runtime/android-data/`。

`bridge/content_lock.json` 固定 82 份必需资源的哈希以及已核验的指纹。Android 会从 APK 读取 4 份未改动的配置，而不在更新目录重复存储；安装和启动会校验这个回退路径。桥接保存实际指纹作为项目身份。

开发环境由 `scripts/prepare_dev_emulator.ps1` 配置，使用 Google APIs x86_64 镜像提供 ARM64 应用支持。虚拟设备、日志和配置保存在 `.local-runtime/`；桥接负责无窗口启动及其创建进程的退出清理，使用 2 核、2 GiB 客体内存和本机图形后端。`.local-runtime/runtime.json` 记录 `sdkRoot`、`avdName`、`avdHome`、`serial` 和 `gpu`；`CR_ANDROID_SERIAL`、`CR_ADB` 可覆盖连接信息。

当前配置是 Android 12 / API 31 开发镜像，ARM64 支持、ADB 和资源迁移均已验证。Android 11 和 12 的实测都未进入战斗：API 30 的含探针及无探针对照包均在 Mainloop 出现 SIGSEGV；API 31 的探针包 SIGSEGV，无探针对照包 SIGILL。说明接入代码之外仍有运行兼容问题，具体原因尚未定位，不能宣称引擎可用。启动时会检测游戏退出并直接报告失败；崩溃日志在 `.local-runtime/native-startup-crash.log`，真实验收结果在 `.local-runtime/native-runtime-check.json`。

## 运行时准备与真实验收

先准备支持 ARM64、允许 ADB root 的专用开发环境。已有经过校验的数据导出时，可迁移到空开发实例；导入不覆盖另一份游戏或不同资源，也不迁移与旧 Android 版本绑定的 ART/WebView 缓存。

```powershell
.\scripts\prepare_dev_emulator.ps1 -ApiLevel 31
# 启动开发设备后，迁移已校验的数据及探针包：
python -m bridge.install_runtime --serial emulator-5556 --import-data .local-runtime/android-data --prepared-apk '.local-runtime/prepared-实际哈希.apk'
python -m bridge.install_runtime --serial emulator-5556 --check
python -m scripts.verify_native_runtime
```

无资源导出时，可先按 [FirstLight 的流程](https://github.com/Jaasssoooonnnnn/FirstLight_CR/blob/main/docs/getting_started.md)让游戏自然更新；其明确验证的运行环境是 MuMu Android 12。本机兼容更新包曾在卸载前取得 `15.535.86`，已核对其数据，不承诺任意开发镜像均能运行该游戏。

```powershell
# 原版安装：空实例不要求 root；替换已有安装必须能建立 root 备份。
python -m bridge.install_runtime --serial emulator-5556 --original-apk '原版游戏.apk'
# 在游戏内完成更新后，校验资源并构建探针包。
python -m bridge.install_runtime --serial emulator-5556 --check
.\scripts\prepare_native_runtime.ps1 -InputApk '原版游戏.apk'
$prepared = (Get-Content .local-runtime\prepared.json -Raw | ConvertFrom-Json).path
python -m bridge.install_runtime --serial emulator-5556 --apk $prepared
python -m scripts.verify_native_runtime
```

若本机输入包的外层加载器拒绝模拟器，可单独测试更新入口，仍须最终通过相同资源指纹校验：

```powershell
.\scripts\prepare_native_runtime.ps1 -InputApk '原版游戏.apk' -UpdateOnly
$update = (Get-Content .local-runtime\update.json -Raw | ConvertFrom-Json).path
python -m bridge.install_runtime --serial emulator-5556 --update-apk $update
# 打开游戏完成资源更新；未得到匹配资源时不能安装探针或宣称可用。
```

恢复先前安装可用 `python -m bridge.install_runtime --serial <原设备地址> --restore <安装记录.json>`。恢复前再次校验备份，设备不符或校验失败会停止。

真实验收脚本通过实际 HTTP 接口检查 tick 90、双方出牌、分支状态差异、跨分支快照恢复、快照后缀重放、从种子与日志重放、原版播放/暂停/变速和画面。报告及截图保存为 `.local-runtime/native-runtime-check.*`；只有全部通过才会报告 `passed`。模拟测试或前端构建不证明原版战斗可用。

详细设计和验收项见 [设计文档](docs/superpowers/specs/2026-09-22-native-engine-replay-design.md)。
