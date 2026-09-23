# 皇室战争原生战斗复盘器

本地 Tauri + React 应用。中央窗口显示 Android 客户端渲染的原生战斗画面；左侧节点以引擎 tick 为时间轴，历史节点出牌会开启新分支并保留已有子分支。

## 当前实现

- 固定引擎源码：`vendor/clash-royale-battle-engine`，提交 `f92ccbecd6a1c9ad1fa3abaca54a51110607b6df`。
- Python 桥接服务：严格检查 APK、`libg`、探针和运行时指纹；提供双方出牌、原生暂停/播放/变速/步进、快照与动作重放。
- React v2 界面：敌方原生 HUD、竞技场、我方原生 HUD 三段裁切；双方圣水观测、节点树、草稿分支、备注和项目保存。
- v2 文件：`.cr-native-replay.json`。旧 `.cr-replay.json` v1 文件不转换。
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

本机已有固定探针、匹配 `libg` 和本地准备脚本 `scripts/prepare_native_runtime.ps1`。准备好的调试 APK 只保存在被忽略的 `.local-runtime/`，不会随仓库分发。脚本使用本机已有的重签名解包工程，构建时校验探针和游戏库哈希。

官方 Android Emulator 36.6.11 在此 x86_64 Windows 主机上无法启动 ARM64 AVD。真实战斗联调需要连接可运行 ARM64 应用的 Android 设备或兼容模拟器。应用会在运行时明确显示此错误；仅通过前端构建或模拟测试不能证明原生战斗可用。

详细设计和验收项见 [设计文档](docs/superpowers/specs/2026-09-22-native-engine-replay-design.md)。
