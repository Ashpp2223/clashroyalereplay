# 皇室战争局面树复盘器

本项目是一个 Tauri + React + Vite 的本地桌面软件 MVP，不是单纯网页应用。

## 当前 MVP

- `snapshot` 是每个节点的唯一局面事实来源。
- `actions` 只是当前节点内部动作日志，不用于反推或强制重算局面。
- 创建子节点时复制父节点的 `snapshot` 和 `unitMarkers`，子节点 `actions` 和 `note` 为空。
- 费用差正数表示我方赚费，允许 1 位小数；出牌会自动增减，用户可以手动修正。
- 竞技场使用 18 x 32 的粗略格子坐标保存单位位置。
- JSON 工程文件扩展名为 `.cr-replay.json`，可重新导入恢复工程。

## 本地桌面运行

日常使用直接双击：

- `release\皇室战争局面树复盘器.exe`
- 桌面快捷方式 `皇室战争局面树复盘器`

开发时运行：

```bash
npm.cmd install
npm.cmd run dev
```

`npm.cmd run dev` 会启动 Tauri 桌面窗口。`npm.cmd run web:dev` 只用于浏览器里的前端预览。

## 打包安装包

```bash
npm.cmd run tauri:build
```

构建成功后会生成：

- 主程序：`src-tauri\target\release\clash-replay-tree.exe`
- Windows 安装包：`src-tauri\target\release\bundle\msi\皇室战争局面树复盘器_0.1.0_x64_zh-CN.msi`

如果当前 PowerShell 找不到 `cargo`，重新打开终端，或先执行：

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```
