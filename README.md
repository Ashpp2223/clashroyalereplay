# 皇室战争原生战斗复盘器

本地 Tauri + React 应用。中央窗口显示 Android 客户端渲染的原生战斗画面；左侧节点以引擎 tick 为时间轴，历史节点出牌会开启新分支并保留已有子分支。

## 标准卡组安装版

Windows 当前用户安装程序已接入独立桥接与视频解码器，应用运行无需系统 Python 或项目源代码。Android SDK、模拟器磁盘、游戏 APK 和更新资源仍由本机运行环境提供，不随安装包分发。

- 首次打开：点击“设置运行环境”，选择含 `runtime.json` 与 `prepared.json` 的已有运行环境目录。
- 默认项目保存在用户数据目录；再次打开应用时读取上次项目与节点。草稿日志按确认动作持续保存，并检查卡牌、动作顺序和引擎身份。
- 画面/连接异常时停止编辑并提供“恢复连接”；后台超过 5 秒收不到有效桌面请求时自动暂停。
- 正常退出通知桥接关闭；Windows 进程组兜底清理本应用启动的引擎子进程，共享 ADB 服务在进程组外启动。
- “导出诊断”仅包含运行版本、状态和视频统计，隐藏用户目录与会话口令。

```powershell
.\scripts\package_native_desktop.ps1
python -m scripts.verify_desktop_bridge --steady-seconds 190
```

安装程序、使用说明和校验值输出到 `release/native-desktop/`。使用步骤见 [标准版说明](docs/native-desktop-user-guide.md)。桌面窗口鼠标交互验收仍受工具应用权限拦截，尚未记为通过；后台和安装包检查与该项分别记录。

## 当前实现

- 战斗接口和探针基线：`vendor/clash-royale-battle-engine`，上游提交 `f92ccbecd6a1c9ad1fa3abaca54a51110607b6df`。本地复盘探针开放精简协议的身份校验和快照命令，使用 NDK r29 从源码重建；来源记录见 `prebuilt/replay-probe-build.json`。原版战斗规则由用户提供的 `libg.so` 执行。
- Python 桥接服务：严格检查 APK、`libg`、探针和运行时指纹；提供双方出牌、原生暂停/播放/变速/步进、快照与动作重放。
- React v2 界面：敌方原生 HUD、竞技场、我方原生 HUD 三段裁切；双方圣水观测、节点树、草稿分支、备注和项目保存。
- v2 文件：`.cr-native-replay.json`。旧 `.cr-replay.json` v1 文件不转换。
- 创建节点时先取得进程内原生状态快照；时间拖动优先从当前路线最近的快照续跑。快照句柄只在本次游戏进程中有效，不写入项目文件；重启后由动作日志重放并核对状态摘要。
- 未提交动作写入本机 journal；选择已保存项目时可恢复。
- 安装版首次使用向导：没有模拟器时，从 Google 官方源下载固定的最小 SDK 组件与 API 31 镜像（约 1.9 GB），核验归档、检测虚拟化、创建专用虚拟设备，再导入本地战斗资源包。无需 Android Studio。默认用户目录不可用于安卓组件时可更改安装位置；至少预留 16 GB。完整空环境战斗验证仍待通过，不能将组件安装通过当成战斗验收通过。
- 老版浅色蓝白界面、紧凑三栏与随可用高度缩放的原生画面。首次安装识别本机已有完整环境；环境选择支持带 UTF-8 BOM 的配置文件。

本地战斗资源包由用户已有的匹配游戏包与更新资源生成，不含账号或存档，也不随仓库或公开安装包分发：

```powershell
$prepared = (Get-Content .local-runtime\prepared.json -Raw | ConvertFrom-Json).path
python -m scripts.export_native_resources --apk $prepared --export .local-runtime/android-data --output .local-runtime/standard-battle-resources.zip
```

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

当前路线是 Windows 界面 + 一套 Python 桥接 + 一个 ARM64 兼容 Android 游戏进程。运行时不需要训练框架或 Frida；画面由游戏绘制。**标准卡组的真实引擎接口验收已通过**，覆盖双方出牌、分支差异、原生快照恢复、快照后缀重放、动作日志重放、游戏进程重启恢复、播放控制和原生画面。桌面完整交互、长时间稳定性及其他卡组/能力仍需后续验收。

`scripts/prepare_native_runtime.ps1` 从未修改的原版 APK 重新解包，添加探针的明确加载入口。构建会核对 `libg`、固定探针和全部 APK assets；临时构建使用英文路径以兼容 Windows aapt2。游戏文件和生成包只保存在被忽略的 `.local-runtime/`，不会随仓库分发。

`bridge/install_runtime.py` 在替换游戏包前，校验并备份旧 APK、应用数据及完整更新资源清单。签名变化需要卸载时会还原数据、修正 UID 和 SELinux 标签，再核对资源；失败会回滚，备份和恢复记录保留在选定设备及本机。桥接启动会先检查资源，再使用这个安装流程；空实例不会直接安装探针包。

用户已选择官方 Android SDK 开发模拟器，并要求卸载 MuMu。MuMu 主程序、运行实例及安装记录已移除。卸载前导出了应用数据和 `15.535.86` 更新资源，652 个更新文件与设备端逐份一致，保存在 `.local-runtime/android-data/`。

`bridge/content_lock.json` 固定 82 份必需资源的哈希以及已核验的指纹。Android 会从 APK 读取 4 份未改动的配置，而不在更新目录重复存储；安装和启动会校验这个回退路径。桥接保存实际指纹作为项目身份。

开发环境由 `scripts/prepare_dev_emulator.ps1` 配置，使用 Google APIs x86_64 镜像提供 ARM64 应用支持。虚拟设备、日志和配置保存在 `.local-runtime/`；桥接负责无窗口启动及其创建进程的退出清理，使用 2 核、2 GiB 客体内存和本机图形后端。`.local-runtime/runtime.json` 记录 `sdkRoot`、`avdName`、`avdHome`、`serial` 和 `gpu`；`CR_ANDROID_SERIAL`、`CR_ADB` 可覆盖连接信息。

当前配置是 Android 12 / API 31 开发镜像，ARM64 支持、ADB 和资源迁移均已验证。此前 Mainloop 崩溃已定位到 4 份画面资源仍使用 `.update` 下载临时名：恢复经指纹 SHA-1 验证的正式路径后，内容加载通过。启动会检查并恢复已完整下载的临时文件，保留原文件及恢复记录；未完成、未知指纹或正式路径冲突时停止。

上游预编译探针返回 `cr-minimal.v1`，与仓库客户端的 v3 解码器不一致，且拒绝原生画面配置、身份校验与快照命令。已从同一基线源码重建精简 v3 探针并仅开放所需复盘命令，继续拒绝未支持的富遥测；运行时核验实际探针哈希。SDK 专用设备配置首次全屏提示已确认，避免遮挡原生画面。

2026-10-09 实测 12 项验收通过，原生画面另经人工检查。多轮节点快照恢复约 22～67 ms；进程内从开局重放到 tick 162 约 9.2～9.3 s；关闭并重新启动游戏进程后恢复该节点约 35.5～37 s（含启动和资源校验）。这些是本机标准卡组的测量，不能推算其他设备或更长对局。实际报告和截图在 `.local-runtime/native-runtime-check.*`。新探针还完成含法术的原生规则对局：tick 6101 终局，双方各 61 次出牌，法术共 39 次，最多同时观测 18 个实体；该长对局使用无画面步进模式，未验证整场画面流。验收中的画面模式与无画面模式各从新游戏进程开始，直接在已创建的画面对局中切换到无画面模式会超时。

## 运行时准备与真实验收

先准备支持 ARM64、允许 ADB root 的专用开发环境。已有经过校验的数据导出时，可迁移到空开发实例；导入不覆盖另一份游戏或不同资源，也不迁移与旧 Android 版本绑定的 ART/WebView 缓存。

仓库已包含本地重建探针及构建来源记录；普通启动无需安装 NDK。需要修改探针时，用 [官方 NDK r29](https://github.com/android/ndk/releases/tag/r29)重建，核对产物和来源记录，并更新 `bridge/runtime.py` 的固定探针哈希后重新准备 APK：

```powershell
python -m scripts.build_replay_probe --ndk 'C:\Users\pp\AppData\Local\Android\Sdk\ndk\29.0.14206865'
.\scripts\prepare_native_runtime.ps1
```

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

真实验收脚本通过实际 HTTP 接口检查 tick 90、双方出牌、分支状态差异、跨分支快照恢复、快照后缀重放、从种子与日志重放、游戏进程关闭重启后重放、原版播放/暂停/变速和画面，并通过同一原生引擎的无画面接口运行含法术对局到终局，共 12 项检查。报告及截图保存为 `.local-runtime/native-runtime-check.*`；只有全部通过才会报告 `passed`。截图需另行人工检查，图像头校验不能证明画面无遮挡。模拟测试或前端构建不证明原版战斗可用。

详细设计和验收项见 [设计文档](docs/superpowers/specs/2026-09-22-native-engine-replay-design.md)。
