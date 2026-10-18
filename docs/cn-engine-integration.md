# 国服 38.4.5 引擎接入

这是同机、相邻研究目录的可选配置。默认配置继续使用原有引擎；国服配置固定使用 `CR_Camp_Research_API31 / emulator-5572`、本地控制端口 26791 和已验证的离线素材，不连接账号。

## 0.4.1 本机打包版

双击 `release/native-desktop/皇室战争复盘器-国服38.4.5.lnk`。它调用 `scripts/start_cn_recorder.ps1`，启动同目录的 `clash-replay-tree.exe` 和冻结桥接；无需 Vite 或系统 Python。普通 EXE / 安装入口仍为原引擎。快捷方式只给新进程设置环境，不修改系统环境变量。

本机桌面“皇室战争复盘器”快捷方式也已切到这个入口。已经打开的旧窗口不会因改快捷方式而升级，需要关闭旧窗口后重开。0.4.1 还补齐精英冰法、精英电法：之前生成器沿用旧精英名单漏掉了两者，现改为读取新包 `characters/hero_form/*_hero_spell.toml` 中的实际 `HeroForm` 关联。69 种形态的原生消耗全部通过，两项新技能及快照／配置动作恢复另有专项报告。

0.4.1 的发行版新建页专项检查也已通过：亡灵巨人、觉醒雷电巨人、精英冰法、精英电法的图标均成功加载；同时携带四项内容的卡组可创建并保存，原生起手牌回读确认两种精英的 `form=2`。截图已查看。新建页先将卡牌加入卡组，再用已选卡下方的形态菜单选择觉醒或精英。

首次进入请选“新建存档”。“继续上次复盘”若指向旧引擎项目，会明确提示版本不匹配；用普通入口继续旧项目即可。

本机版仍需保留记谱器项目、相邻 `C:\MyProject\clashroyale1016`、Android SDK 与已准备的研究设备。它不是独立迁移安装包。项目或研究目录搬家后重新运行打包脚本生成快捷方式，或向启动脚本传 `-App`、`-ResearchRoot`。打开存档必须选择与其规则版本相符的入口；旧存档不会自动迁移。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\package_native_desktop.ps1
node scripts/verify_cn_recorder.mjs --packaged
node scripts/verify_cn_recorder.mjs --packaged --playback-only
node scripts/verify_cn_recorder.mjs --packaged --terminal-reopen
node scripts/verify_cn_recorder.mjs --packaged --catalogue-only
```

发行版三段验收直接运行交付目录中的 EXE，报告在研究目录 `.runtime/recorder-cn-packaged-acceptance/`，与开发版验收分开。后续阶段检查上一阶段成功且应用、桥接和配置摘要未变。版本升级知识见 [升级知识库](engine-upgrade-playbook.md)。

2026-10-17 的 0.4.0 发行版三段验收均通过：新建、图标加载、厨师塔与 11 级配置、真实落点、80 次单 tick 步进、平行分支保存／切换、应用关闭重开、连续播放／暂停、相机缩放、终局保存与恢复。最终从磁盘动作日志恢复到 tick 6143、皇冠 `0:1`，摘要与关闭前完全一致；无页面错误。实际截图另已查看。领域与界面逻辑 43 项、Python 112 项测试通过，前端及 Tauri 发行构建通过。该结果针对本机交付目录中的程序，不是另机安装或所有卡牌交互的验收。

## 开发入口与契约

排查部署失败时优先使用桥接工具，不用鼠标操作。`scripts/verify_native_deployment.py` 读取存档作为只读输入，启动独占的打包桥接，在隔离目录记录暂停／播放两种部署的响应、耗时和画面读取；拒绝与已运行的桌面桥接竞争连接。先关闭复盘器，再运行：

```powershell
python -X utf8 scripts/verify_native_deployment.py --project "存档的完整路径.cr-native-replay.json" --owner 0 --card 26000042 --x 3500 --y 6500 --repeat 2
```

播放中切倍速时，原生 `speed generation tick multiplier` 会拒绝已经落后的帧号并返回 `stale_battle_state`。仅先读一次观测仍有竞态；桥接现通过原控制器锁依次确认暂停帧、设置速度、恢复播放。原先暂停的战斗保持暂停，失败时不重试变速或恢复请求，不重置局面。保留原生状态校验及存档身份。复现与验证复用同一工具：

```powershell
python -X utf8 scripts/verify_native_deployment.py --project "存档的完整路径.cr-native-replay.json" --restore-only --speed-check
```

它覆盖暂停与播放中的五档倍速，故意等待观测过期再变速，并检查播放保持、真实 tick 推进和暂停摘要不变。修复前后报告分别在 `.local-runtime/speed-before-fix/report.json`、`.local-runtime/speed-after-fix/report.json`。

节点连线通过 `/v1/playback` 回放一个有界时间段：请求携带已确认基点的 tick/摘要、该边的动作及终点 tick/摘要。复用桥接已有监控线程，按所选倍速分批推进原生逻辑 tick；同一控制器锁串行执行牌/技能，逐条验证收据，在终点核验摘要并精确停住。前端随后切入终点节点：没有分叉时调用原有 `/v1/resume` 从该局面继续推演，可出牌、单步和保存；多个子分支时暂停等待选边，真正终局保持停止。点击节点也会恢复该局面并立即继续推演。连线内部回放保持只读，若要改中间帧可创建子节点。暂停和变速重新锚定当前帧，改变局面或恢复其他节点会清除旧段计划；节点文件格式及引擎身份不变。

```powershell
node scripts/verify_native_frontend.mjs --edge-playback
python -X utf8 scripts/verify_native_deployment.py --project "存档的完整路径.cr-native-replay.json" --restore-only --edge-check
```

前端专项通过 10 组流程，包含 A 的 B/C 选择、B 的 D/E 等待、循环球、暂停与变速、时间条续播、中间帧创建独立分支，以及到达节点后继续推演、单步出牌和保存；测试使用应用函数和模拟接口，不操作鼠标。此前国服真实引擎接口验收在隔离目录创建两条不同落点的 AB/AC 区间，验证动作重放、暂停续播、2×/4×、精确端点和停止后摘要不变，另验证 AC 接续 CF。报告在 `.local-runtime/native-ux-check/edge-playback-frontend-report.json`、`.local-runtime/edge-playback-native-final/report.json` 和 `.local-runtime/edge-playback-flow-snapshots/report.json`。真实接口报告不等同于本次节点自动继续的完整桌面流程验收，用户存档只读。

卡牌必须在对应起手牌中，落点是原生世界坐标。报告保存在 `.local-runtime/deployment-check/report.json`，不保存连接口令。桥接诊断的 `lastFailure` 保留请求路径、错误类型和原始原因，后续自动暂停不会覆盖它；该记录不代表错误当前仍在发生。

仅验证旧存档恢复时使用 `--restore-only`，无需指定卡牌和落点。工具根据存档协议选择旧版或国服桥接；旧版运行配置复制到隔离目录，存档只读。恢复结果与保存的开局摘要逐字比对，并读取实际原生画面：

```powershell
python -X utf8 scripts/verify_native_deployment.py --project "旧存档的完整路径.cr-native-replay.json" --restore-only --output .local-runtime/legacy-archive-check
```

出牌后故障也会自动保存到运行目录的 `last-failure.json`（桥接错误）和 `last-client-failure.json`（请求、动作记录或画面解码错误）。关闭应用后仍保留，可在不连接、不改变当前局面的情况下读取：

```powershell
python -X utf8 scripts/verify_native_deployment.py --read-diagnostics
```

需要验证真实前端数据校验、Rust 草稿写入与桌面图片解码时，复用已有桌面测试工具：`node scripts/desktop_control.mjs --deployment-check "存档完整路径"`。它仅通过隔离测试会话的接口操作，不模拟鼠标或键盘，不修改原存档；该检查不等同于覆盖完整 React 界面生命周期。

2026-10-17 已定位“暂停一会儿，第一张牌就断线”：国服原生控制服务对客户端设置 20 秒接收超时，而桌面状态与暂停观测读取的是桥接缓存，不会维持原生连接。旧打包版等待 25 秒后出牌，稳定复现与用户诊断相同的 `/v1/play-card` → `WinError 10053`（`.local-runtime/idle-connection-before-fix/report.json`）。桥接现复用已有监控线程，每 5 秒在原有连接上发送只读 `status`；通过同一控制器锁串行化，忙碌时跳过，不推进战斗或重试出牌。连接确实失效时仍要求显式恢复。

使用部署工具的 `--idle-seconds 25` 复现等待后出牌场景；不能只用连续快速出牌作为连接稳定性的验证。修复后报告位于 `.local-runtime/idle-connection-after-fix/report.json`。

在本项目目录的 PowerShell 中启动调试版：

```powershell
$env:CR_ENGINE_PROFILE='cn-38.4.5'
$env:CR_CAMP_RESEARCH_ROOT='C:\MyProject\clashroyale1016'
$env:CR_ENGINE_PORT='26791'
$env:CR_BRIDGE_PYTHON='C:\Anaconda\python.exe'
npm run dev
```

研究目录需要保留 `analysis`、`.runtime` 和已安装的测试 APK。桥接读取 `analysis/recorder-profile.json`，检查适配器摘要、前后端配置一致性、实际安装 APK、设备运行目录、素材指纹、画面尺寸和方向。缺少这些材料会返回错误，不自动切回另一套规则。

当前配置包含 123 张基础卡、43 个觉醒及 26 个精英／英雄形态的原生消耗验证，以及四种 `169...` 塔兵。默认等级为 11，16 级塔兵配置也已验证。卡牌形态可配置与每个技能在所有战斗情形中均已覆盖是不同的验证范围；技能可用性取实际原生状态。

原生相机会在连续播放时改变缩放。每份画面观测携带其实际 `cropProfile`；暂停、步进和恢复等待开场横幅消失及后续渲染更新，再返回投影。前端按该观测换算点击位置，图片缓存键包含世界矩形和裁剪区域。画面投影不计入战斗摘要，所以相机变化不会篡改节点的战斗身份。

新存档记录国服 APK、libg、控制模块、素材、协议和裁剪模型身份。旧存档按旧目录校验，跨引擎打开不会覆盖旧摘要或被当成原局一致。快照句柄只在当前进程／局面中有效；应用重开后按初始配置、种子和动作日志恢复。

修改原生控制模块或适配器后，在研究目录重新验证并导出：

```powershell
C:\Anaconda\python.exe analysis/verify_native_control.py --recorder-contract
C:\Anaconda\python.exe analysis/verify_native_control.py --form-catalogue
C:\Anaconda\python.exe analysis/verify_native_control.py --new-heroes
C:\Anaconda\python.exe analysis/build_recorder_profile.py --export-recorder
```

生成器要求对应当前 APK 的画面、基础卡、形态、塔兵及控制器报告通过，校验源文件未变，并直接运行记谱器的 TypeScript 坐标函数。导出会更新本项目的 `src/data/cnEngineProfile.json`；静态表与研究端配置不一致时拒绝启动。

桌面验收使用独立目录，真实调用引擎和文件系统：

```powershell
$env:CR_BRIDGE_PYTHON='C:\Anaconda\python.exe'
npm run web:dev
# 在另一个本项目终端执行：
node scripts/verify_cn_recorder.mjs
node scripts/verify_cn_recorder.mjs --playback-only
node scripts/verify_cn_recorder.mjs --terminal-reopen
```

三条命令依次验证基础分支、连续播放与终局、终局关闭重开。后两条要求上一阶段已保存有效基线且应用和配置未变化；每次追加测试会先保留前一份报告。报告和截图在研究目录 `.runtime/recorder-cn-acceptance`，不会写入日常存档目录。调试版桌面文件需已构建；发行版使用上面的 `--packaged` 命令独立验收。

切回默认配置时清除当前终端中的 `CR_ENGINE_PROFILE`、`CR_ENGINE_PORT` 和 `CR_CAMP_RESEARCH_ROOT`，再重新启动应用。

技能图标由现有资源导出工具从本地素材包的 `ABILITY.IconSWF` / `IconExportName` 提取，并覆盖同版本更新资源，包含当前两种引擎使用的 26 个技能；费用来自游戏 `ManaCost`。生成结果在 `public/abilities` 和 `src/data/nativeAbilityIcons.json`，不修改引擎目录或存档身份。

```powershell
# 仅素材导出需要这个可选依赖，固定 MIT 解码器版本。
.local-runtime/package-env/Scripts/python.exe -m pip install https://github.com/obus-globus/sc5-parser/archive/9108080256a0df6cd772d81afdbc26047d79d78d.zip
.local-runtime/package-env/Scripts/python.exe -m scripts.export_native_resources --ability-pack ../clashroyale1016/analysis/resources/resources.pack --ability-update ../clashroyale1016/.runtime/content/update --output public/abilities
node scripts/verify_native_frontend.mjs --surface-layout
```

此游戏 SC5 导出名称索引直接指向字符串表（第 0 项为空）；固定解码器减一会导致图标错名或空导出，工具已按原始 ExportNames 表修正绑定。输出清单保留素材摘要和解码器来源。布局脚本直接挂载组件，验证双方技能位置、冷却和不可用状态及补牌时尺寸稳定，不启动引擎、不操作鼠标。
