# Windows 无 Android 原生战斗引擎接入计划

> 2026-10-09 路线调整：用户选择使用模拟器。本计划保留为 Windows 无 Android 宿主的研究记录，当前实施以 [Android/ADB 接入计划](2026-09-23-native-engine-replay.md) 和仓库实际桥接代码为准。Windows 宿主实验不再是当前交付路径。

日期：2026-10-03

## 目标与边界

让现有 Windows 桌面复盘器使用**当前锁定的原版 ARM64 `libg.so` 执行战斗规则**，而不是重写卡牌逻辑。最终运行时不依赖 Android 设备、模拟器、系统映像、ADB、APK 安装或 Android 游戏进程。**仍然需要原版游戏文件**：可以由用户提供原 APK 供本机离线提取，也可以直接提供从同一构建提取并校验过的 `libg.so`、所需配套库和匹配资源。APK 不需要在 Windows 上安装或运行；仅有 APK 也不保证包含后续下载的内容更新。第三方游戏文件只在本机导入、校验和使用，不提交到仓库或默认装进我们的安装包。

本机是 Windows x64。`libg.so` 是 AArch64 Android ELF，不能直接由 Windows `LoadLibrary` 加载；Windows 诊断器现已执行原版 ARM64 指令、装载两份 ELF，并走通构造器、JNI 注册、应用生命周期、客体线程调度及一段原版图形初始化。这里的“宿主”指我们自己的 Windows 进程及必要的兼容代码，不包含 Android 用户空间。**内容初始化和真实战斗尚未跑通，完整宿主的可行性仍待阶段 1 实证。**

FirstLight 的节点快照使用原版战斗状态序列化，返回的 `snapshot-create` 句柄标为 `scope: process`，持有指向当前游戏进程堆内存的包装对象；游戏进程退出后该句柄失效。它解决的是**引擎已经运行后的节点恢复**。本计划复用这一原版序列化入口；Windows 宿主先要完成原版 ARM64 游戏库的装载、初始化、线程与资源访问。关闭宿主后，仍保存种子和动作日志以重跑恢复；持久快照导出只有在完成跨进程格式与校验实证后才可替代这一路径。

## 查到的现状

| 来源 | 已有能力 | 对无 Android 目标的结论 |
| --- | --- | --- |
| [FirstLight CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR) | [原生探针](https://github.com/Jaasssoooonnnnn/FirstLight_CR/blob/main/native_runner/probe/cr_replay_probe.cpp)在原版 ARM64 游戏进程内调用战斗状态管理器，提供无画面/有画面执行、状态观测及进程内快照。 | 逆向了调用入口和数据结构；原版战斗逻辑仍由 `libg.so` 执行。其部署说明要求 Android。 |
| [Jason 的 battle engine](https://github.com/Jason-XII/clash-royale-battle-engine) | 用 `session-v1`、`configure`、`step`、`minimal-transition` 等命令把原版战斗引擎包装为 TCP API；[启动脚本](https://github.com/Jason-XII/clash-royale-battle-engine/blob/main/bootstrap_emulator.py)负责 Android/ADB/探针。 | 可复用接口语义和锁定版本资料，现有 Python 客户端本身不是 Windows 引擎。其高速基准也测于 Android 环境，不能外推到新宿主。 |
| [ak1NaN 的 Replay Studio `win` 分支](https://github.com/ak1NaN/ClashRoyale-replay-studio/tree/win) | [Windows 开发记录](https://github.com/ak1NaN/ClashRoyale-replay-studio/blob/win/docs/windows-development.md)与[设备代码](https://github.com/ak1NaN/ClashRoyale-replay-studio/blob/win/app/device.py)显示：Windows 控制端连接 MuMu 中的 Android 游戏，注入探针；[运行时](https://github.com/ak1NaN/ClashRoyale-replay-studio/blob/win/app/runtime.py)有节点快照缓存和重放调度。 | 快照/节点切换设计可参考，但它的 Windows 分支仍依赖 Android。该仓库未见明确开源许可，具体代码先只作设计参考。 |
| Jason 的其他仓库 | [cr-memory-reader](https://github.com/Jason-XII/cr-memory-reader)只读另一构建的游戏内存；[clash-royale-simulator](https://github.com/Jason-XII/clash-royale-simulator)是独立近似模拟；[sc-decoder](https://github.com/Jason-XII/sc-decoder)处理图像资源。 | 均不是当前 `libg.so` 的无 Android 宿主；`sc-decoder` 仅可能帮助后续 Windows 自绘画面。 |

本仓库当前已经有 Tauri/React 节点复盘、`bridge/server.py` 的本机 HTTP 契约、`bridge/controller.py` 的串行战斗操作、`src/domain/nativeReplay.ts` 的 v2 动作日志与状态摘要，以及内存快照加速。`src/NativeApp.tsx` 最多保留 32 个进程内快照句柄；关闭引擎进程后仍靠种子和动作日志重跑。**现有 `bridge/runtime.py`、`bridge/video.py` 和 `bridge/bootstrap_runtime.py` 仍走 Android/ADB，且真实战斗闭环未在本机验证。**当前自动化测试使用假引擎，不能作为原生引擎可用的证据。旧 [Android 实施计划](2026-09-23-native-engine-replay.md)仅作历史记录，本计划接替其运行时路线。

锁定二进制是 `nullsroyale.rel.free` APK 15.535.13 / ARM64 的 `libg.so`，SHA-256 为 `110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783`。该 APK **同包内嵌内容指纹 15.535.3**；用户补充的原游戏运行流程是安装 15.535.13 后在游戏内下载资源，内容更新到 **15.535.86**，这与 FirstLight 的同一库哈希/运行时内容清单一致。二者是资源更新阶段，不是两份不同 `libg.so`。先用手里的同包资源把原版启动与战斗路径测到实际缺口；若原版确实要求更新后的内容，再取得并校验 15.535.86。版本数字差异本身不作为启动诊断的阻塞。本地 `readelf` 确认库是 AArch64 ELF64，并声明 `libscid_sdk.so`、`liblog.so`、`libandroid.so`、`libEGL.so`、FMOD、Sentry、`libc++_shared.so`、`libc.so` 等依赖；哪些依赖位于战斗无画面路径，仍需逐一验证。

## 接入结构

```text
NativeApp + nativeReplay 节点/动作/摘要
             ↕ 现有 /v1/* HTTP 契约
bridge/server.py + BattleController（串行化、重放校验）
             ↕ 现有战斗命令/观测语义
Windows x64 战斗宿主（ARM64 执行 + ELF 装载 + 最小 ABI 兼容）
             ↕
用户本机的固定 libg.so + 匹配资源
```

先沿用 `bridge/engine_adapter.py` 的客户端接口，给它提供等价的 Windows 宿主命令和观测；不要同时复制另一套节点树或战斗规则。FirstLight 探针里按版本固定的函数入口、状态序列化与反序列化方式是验证线索，现成 `libcrprobe.so` 是 Android ARM64 库，不能直接当 Windows DLL 使用。有画面路线必须单独证明：现在 `/v1/frame` 取自 Android 屏幕流，Windows 战斗宿主不会自然产生相同画面。

## 阶段与验收

### 0. 固定输入并列出宿主需求

1. 对本机 `libg.so`、其配套库、`assets.scdb` 和内容包建立 SHA-256/版本清单；记录源包与探针偏移对应关系，不再依赖 APK *运行*。
2. 从 ELF 动态表和 FirstLight 已知调用路径先列出初始化、创建对局、推进、出牌、快照可能触及的导入符号、JNI、文件路径、线程/TLS、时钟和内存映射需求；最小实证期间再补实际调用记录。区分无画面必需与渲染才必需，而非一开始实现整个 Android API。
3. 清点可用于 Windows x64 的 ARM64 执行器与 ELF 装载方式，检查许可、维护状态和在本机运行的能力；选一个最小实验方案。

当前实验用 Unicorn 跑原版指令；其上游仓库的 [COPYING](https://github.com/unicorn-engine/unicorn/blob/master/COPYING) 为 GPLv2，只能暂作本机验证。发布方案优先评估 [Dynarmic](https://github.com/azahar-emu/dynarmic) 的 Windows x64/AArch64 JIT 与 [0BSD 许可](https://github.com/azahar-emu/dynarmic/blob/master/LICENSE.txt)。它只解决 CPU 执行，ELF 装载、线程/TLS、Bionic 接口、JNI 和资源初始化仍是本项目需要实现、验证的部分。

**完成证据：**一份可复现的依赖/调用清单、固定哈希和最小宿主方案；不得把“存在 Python API”当成已能运行 `libg.so`。

### 1. Windows 原版战斗最小实证（决定是否继续）

1. 建立独立 Windows 宿主实验：执行 ARM64 指令、映射 ELF 段和重定位、解析必需依赖；只补已证实用到的系统接口。现已运行 Mainloop、两个池线程、定时线程和 OpenGL 线程的客体切片，并按观测到的条件变量通知/等待调度。下一步须补齐线程退出与 JNI 附加等仍未覆盖的实际路径，并接通真实 GLES 后端；诊断中的虚拟 EGL/GLES 查询只用于定位调用，不能作为可交付图形能力。不得启动 Android、ADB 或游戏客户端。
2. 接通本机资源目录的 `AAssetManager` 列举、打开、读取和关闭。先用同 APK 的 15.535.3 内容追踪首次启动接口，再取得并验证**游戏内更新后的 15.535.86**，让原版完成与 FirstLight 一致的内容初始化。按已校验的入口创建标准对局；达到首个可操作 tick 90，推进到 91，并读出 tick/双方手牌/圣水/实体。仅验证目录存在或启动函数返回，均不能作为这一项的通过证据。
3. 对相同种子连续运行两次，比较观测摘要；让双方各出一张牌，确认原版函数返回的执行事件和状态变化。记录崩溃栈、缺失 ABI、速度和内存。

**继续条件：**全程只有 Windows 宿主和本机资源、战斗能稳定推进且两次结果一致。记录 tick/s；交互播放的后续性能目标至少是实时 20 tick/s，低于目标先定位瓶颈。若宿主必须带 Android 系统/游戏进程，或在限定实验中无法完成初始化，应停止此路线并给出具体阻塞点和可选方案，由用户决定是否调整目标；不偷偷改用规则重写或 Android 模拟器。

### 2. 把实证接到现有桥接层

1. 让 Windows 宿主提供 `configure/reset`、`step`、`play-card`、`observe`、`snapshot-create/restore/release`、`pause/resume/speed` 的等价命令与明确错误；在宿主侧串行修改同一战斗状态。
2. `bridge/engine_adapter.py` 保持已有 `Engine`/`Play`/`State` 用法；`bridge/runtime.py` 改为 Windows 宿主发现、启动、资源校验和进程管理，`bridge/server.py` 切换到该运行时。`BattleController` 尽量沿用现有 `/v1/*` 对外契约。
3. 将能力证明改为实际成功的功能声明；无画面宿主不得再被当前 `native_render` 必选检查挡住，也不得宣称自己能提供原版画面。新宿主的版本/哈希与战斗数据身份分开记录；打开旧 v2 项目时，以 `libg`、内容指纹、配置和重放摘要确认兼容，不能只因 Android 探针或画面配置哈希不同就误判战斗不兼容。

**完成证据：**不重写节点数据模型，通过现有 HTTP 路由完成两边出牌、步进、观测与失败恢复；断开宿主时 UI 明确报错，不产生未经确认的动作。

### 3. 节点快照与冷启动重跑

1. 移植原版状态序列化/反序列化调用到新宿主，快照句柄仍限定于一个进程、同一个战斗配置；给快照设内存上限和释放策略。
2. 继续保存种子、版本、动作日志和各节点摘要；应用关闭后从种子与动作日志重跑，不把进程指针或句柄写进项目文件。进程还在时优先复用当前 `src/NativeApp.tsx` 的最近祖先快照逻辑。
3. 验证跨分支事件序号、增量观测游标和摘要；对同一节点分别用“快照+后缀”和“从种子全量重跑”取得同一结果。测量创建/恢复快照、100/200/1000 tick 重跑及跨分支切换的耗时与内存。

**完成证据：**热切换、删除分支、宿主重启后恢复都得到同一摘要；快照失效会安全退回全量重跑。报告实测分位耗时，不套用 Jason 在 Android 的约 498 倍速度数据。

### 4. 画面与桌面交付

1. 在阶段 1 真实接通启动所需的 EGL/GLES 后，单独验证原游戏画面能否在 Windows 宿主输出：窗口/触摸和游戏场景生命周期要有实证；成功后把帧接到现有 `/v1/frame`，复核三视口裁切及点击坐标。启动需要图形接口，不等于已证明战斗推进依赖显示画面。
2. 若只能运行无画面战斗，先交付清楚标注的状态/事件视图；对是否采用 `.sc` 资源自绘作产品决策。自绘画面不能宣称与原版逐帧一致，也不阻碍原版战斗逻辑的验收。
3. `src-tauri/src/main.rs` 改为从安装目录启动已打包的桥接与 Windows 宿主，去掉编译时仓库路径和外部 Python 的发布依赖；在没有 Android SDK/ADB 的干净 Windows 机器上回归安装、打开项目、战斗、节点恢复和退出清理。安装包不内含未经授权的游戏二进制/资源。

**最终验收：**干净 Windows x64 上用用户本机的固定游戏文件跑通战斗、双方出牌、节点切换与重启后重放；画面能力按实际已验证模式呈现，所有依赖和限制在界面与说明中一致。

## 实施顺序

第一项实际开发只做阶段 0 和阶段 1 的最小实证。在它通过以前，先不大改 UI、不迁移项目格式、不承诺原版画面或性能。通过后再按 2 → 3 → 4 接入，每阶段保留可重复的输入、命令、观测与耗时记录。

## 2026-10-03 起实施记录

- 新增 `scripts/inventory_windows_native.py`，对用户本机文件建立可重跑的元数据清单。已生成被 Git 忽略的 `.local-runtime/windows-native-inventory.json`：锁定 `libg.so` 哈希匹配；12 个动态库依赖、116,748 条重定位，其中 115,021 条相对重定位；`.init_array` 有 753 个槽位；473 个具名未定义符号中有 268 个在 APK 同目录库里找不到提供者；探针记录的函数 prologue 与当前库全部匹配。**清单只证明静态版本匹配，不证明 Android ABI 或内容初始化可用。**
- 新增 `scripts/spike_windows_libg_tick.py`。本机 Windows x64 用实验性 ARM64 CPU 执行器映射原版 ELF 的 61,112,320 字节虚拟映像，应用 115,021 条相对重定位和 4 条库内绝对重定位，实际执行 `libg.so` 的 `GameStateTick`、`GameStateContextA`、`GameStateContextB` 三段原版 ARM64 代码，返回预置的 tick 91 和预期上下文指针。输出在 `.local-runtime/windows-libg-tick-spike.json`。**这些调用传入的是合成数据，尚未初始化战斗，更未推进任何真实 tick。**
- 已把本地 `libc++_shared.so` 作为第二个 ARM64 ELF 映射进同一诊断进程，并处理两库所有 121,006 条已知重定位：其中 1,371 条 `libg.so` 导入重定位连接到真实 C++ 库导出；12 条 `libg.so` 和 2 条 C++ 库的弱导入按 ELF 规则归零；余下 340 条 `libg.so` 与 161 条 C++ 库外部重定位仍指向诊断陷阱。输出在 `.local-runtime/windows-libg-tick-spike-with-libcxx.json`。按依赖顺序运行后，`libc++_shared.so` 的 3/3 个构造器和 `libg.so` 的 753/753 个构造器均在有界实验中返回。第 730 个构造器通过 `dlsym` 获取 `dl_iterate_phdr`，原版回调遍历两份 ELF 程序头，对 `libg.so` 代码段执行两次 `mprotect`（开放写入、恢复只读执行），并由原版代码写入 726 条 `RET` 指令。该修改与 FirstLight 探针记录的 58 个固定入口及额外内容根入口前 16 字节均无重叠；后三项原版小函数调用仍按预期返回。诊断适配只覆盖已触及的单线程锁、无等待者条件变量、TLS key、C locale、内存函数和固定虚拟时钟；其他依赖库仍未装载。**构造器返回不代表原版内容或战斗已初始化；当前 `battle_initialized=false`。**
- 2026-10-05：在同一客体状态执行原版 `JNI_OnLoad`（`scripts/spike_windows_libg_tick.py --diagnose-constructors --diagnose-jni-onload --libcxx ...`）。诊断 JavaVM/JNIEnv 只实现实际触及的线程附加、类查找、原生方法注册与异常检查，其他接口保持陷阱；原版函数返回 JNI 1.6 版本号 `0x10006`，记录到 15 个类和 92 个注册方法，`GameApp.createGameMain` 指针为 `libg+0x13d00e0`。无未处理调用或内存访问错误，TLS canary 未变，三项原版小函数回归仍通过。结果在被 Git 忽略的 `.local-runtime/windows-libg-tick-spike-with-jni.json`。**注册方法成功不代表 Java 应用生命周期、资源或战斗已经初始化；输出仍为 `battle_initialized=false`。**
- 同日：增加受限 `--diagnose-application-create`，从原版注册表取指针，按 APK 顺序调用 `TitanApplication.nOnNativeLibrariesLoaded`（`libg+0x13d1390`）和 `GameApp.nOnApplicationCreate`（`libg+0x13d0fd0`）。两个原版回调都在 Windows 客体中返回，应用单例槽由空变为 `0x7006ffe0` 并在第二个回调保持不变；TLS canary 完好，未触及未实现接口或非法内存。宿主仅对实际触及的四次 `posix_memalign` 调用分配客体内存，并明确记录、抑制两个 Sentry 遥测调用（包括 `promon.version=8.2.4`）；**原版 Sentry 库没有执行，不能将此结果表述为完整依赖链或游戏内容已启动。**输出在 `.local-runtime/windows-libg-tick-spike-with-application.json`，`battle_initialized=false`。
- 同日：再增加受限 `--diagnose-create-game-main`，用原版 JNI 注册表中的 `GameApp.createGameMain` 指针和核对过的 ARM64 参数布局进入函数。仅对本次已追踪的 Activity 与 `GameApp` 类句柄透传三次 `NewGlobalRef`；原版代码随后在 `libg+0x13fbb7c` 请求 `JNIEnv.GetStaticMethodID(GameApp, "isEmulator", "()Z")`，诊断因缺少 Java 方法解析而严格停止。此时尚未触及 `AAssetManager_fromJava`，内容根槽仍为零、`battle_initialized=false`；结果在 `.local-runtime/windows-libg-tick-spike-with-create-main.json`。随后核对 APK 的 `GameApp.smali` 第 620–627 行：该静态方法直接返回 `false`，无 Android API 调用或类静态初始化；可以只针对准确的类、方法名及签名提供 JNI 兼容回调，不必猜测其返回值或修改战斗逻辑。
- 后续按 APK 字节码精确实现上述 `isEmulator()Z` 的 JNI 方法查找与布尔调用，记录返回 `false`；原版接着访问 `/proc/self/cmdline`。受限虚拟文件只提供当前清单包名的 `nullsroyale.rel.free\0` 字节，原版依次执行 `fopen`、`fgets`、`fclose` 并自行把应用标志从 `0x2` 改为 `0x200`；宿主没有伪装成 `com.supercell.` 或写入该标志。下一实际停点是 `libg+0x13d0324` 的 `AAssetManager_fromJava(JNIEnv*, AssetManager)`；没有创建资源管理器，内容根仍为零，`battle_initialized=false`。同一被忽略的 JSON 记录了所有调用、传入字节与首个未实现接口。
- 增加 `--assets-dir` 诊断参数：仅在目录内存在非空 `assets.scdb` 和 `fingerprint.json`、传入同一次已追踪的 JNIEnv 与 AssetManager 句柄时，将 `AAssetManager_fromJava` 返回值关联到该本机目录。目录版本 **15.535.3** 与 FirstLight 运行时清单的 **15.535.86** 不同，诊断结果记录这个差异，但它是与本机 libg **同 APK 的资源**，不能因此直接判定不可用。首次实验停在 `JNIEnv.GetStringLength(dataDir)`，后续已沿调用继续；输出在被 Git 忽略的 `.local-runtime/windows-libg-tick-spike-with-assets-dir.json`。APK 中 `dataDir` 来自 `ApplicationInfo.dataDir`，缓存目录来自 `Context.getCacheDir().getAbsolutePath()`。
- 后续给三个已追踪的 Java 字符串提供 UTF-16 JNI 访问，按 APK 来源创建客体 `/data/user/0/nullsroyale.rel.free` 和 `/cache` 路径；原版的 `mkdir` 请求只允许映射到仓库内被忽略的 `.local-runtime/windows-guest-fs`。`/system/app/Superuser.apk`、`/system/app/SuperSU/SuperSU.apk`、`/sbin/magisk`、`/sbin/.magisk` 的 `stat`，以及两条 `which su` 命令，均只在精确匹配的调用点返回受控客体环境中的“不存在”或空输出；没有执行宿主命令。这些环境选择用于诊断路径，不能视为真实 Android 系统兼容性证明。
- 原版之后查询 `Context.getPackageManager()` 和 `PackageManager.hasSystemFeature("com.google.android.play.feature.HPE_EXPERIENCE")`。诊断 JNI 仅对这两个已观察方法及 ARM64 `va_list` 中核对出的参数提供回调，客体特性清单为空，故返回 `false`；局部/全局引用的已观察生命周期有计数记录。原版 `createGameMain` 已在 Windows 诊断器中**完整返回**一个 Java 字符串句柄，首个未处理调用为空、TLS canary 未变；但内容根槽仍是零，`battle_initialized=false`。此结果只证明启动 JNI 入口返回。
- APK 的 `GameApp.onCreate` 在 `createGameMain` 和 Java View 设置之后调用原版 `nOnCreate`。诊断器在没有执行 View 设置的前提下继续调用其注册入口 `libg+0x13d13dc`；原版 `pthread_self` 后请求 `pthread_create`，线程入口为 `libg+0x13a1088`，原版构造的工作回调为 `libg+0x139e2b0`。诊断适配只对已观察且核对参数的调用点创建客体工作项和独立句柄，使原版 `nOnCreate` 返回；随后在同一地址空间、独立 ARM64 栈和 TLS 下运行 Mainloop。原版 libc++ 线程局部数据及 `JavaVM.AttachCurrentThread(Mainloop)` 已执行。原版随后调用 `AssetManager.list("sc3d/shaders")`，从同包资源目录取得 133 个真实文件名，逐项读取并释放 JNI 引用；之后查询虚拟 CPU 数量并创建 `worker-0`、`worker-1`。两个线程已各自在独立栈/TLS 上运行至原版 `pthread_cond_wait`，宿主按条件变量语义释放其互斥锁并使其等待。后续再创建定时线程，按有界虚拟时钟调度。原版句柄仍可用于之后的 `join`；没有观察到 `pthread_detach`。此时仍没有完整宿主、资源文件内容读取或战斗，内容根为零，`battle_initialized=false`；诊断结果在 `.local-runtime/windows-libg-tick-spike-worker-probe.json`。
- 2026-10-07：按 APK 生命周期补齐两次 `nOnStart`、一次 `nOnResume`、`nOnSurfaceCreated` 和 `nOnSurfaceChanged(1080,1920)`；这五次原版回调均返回。Mainloop 消费 Surface 事件后创建名为 `OpenGL Thread` 的原版客体线程，并等待该线程通过条件变量发送启动通知；诊断器实际运行该线程及通知，主线程才继续。OpenGL 线程依次进入 `eglGetDisplay`、`eglInitialize`、配置枚举/筛选、`eglCreateContext`、`eglCreateWindowSurface`、`eglMakeCurrent`。诊断器对同一个 display/config/context/surface 保存状态与引用关系，并核对 RGBA8、深度 24、模板 8、GLES3 等原版实际请求；首次空 context 绑定失败，之后原版用真实 context 成功绑定。这里的 EGL 是虚拟身份和查询，不包含像素或驱动功能。
- 原版通过 `eglGetProcAddress` 动态解析约 280 个 GLES 名称；早期虚拟图形诊断只分配可识别的 ARM64 陷阱入口，不把这些名称当作已实现。`glGetString`、版本/扩展/上限与精度查询曾用明确标记的合成 GLES3 值继续诊断，顶点数组对象只追踪名字与绑定，不产生像素。装载器需至少一条扩展字符串才能沿当前 GLES3 分支返回成功。该轮诊断停在 `glGenTextures`；后续已转为真实 ANGLE 后端。启动依赖图形兼容层，但尚未证明战斗规则或后续逻辑步进需要显示画面。诊断中的 `battle_initialized=false` 仍准确。
- 同日，本机发现 Windows x64 的 ANGLE `libEGL.dll`/`libGLESv2.dll`。独立 Windows 进程已实测 `eglGetDisplay`、`eglInitialize`、RGBA8/深度24/模板8 的 GLES3 配置、无窗口 pbuffer、`eglCreateContext`、`eglMakeCurrent`、`glGetString(GL_VERSION)` 与 `eglSwapBuffers` 全部成功，不依赖 Android、APK 安装或 HWND。下一步将虚拟 `ANativeWindow` 对应到真正的 pbuffer，并为原版 ARM64 函数指针建立到 x64 ANGLE 的参数/客体内存桥接；Windows 函数地址不能直接交给 ARM64 代码调用。当前发现只证明本机有可用后端，不证明该桥接或游戏内容已运行。
- 后续新增 `scripts/windows_angle_backend.py` 与诊断器的可选 `--angle-dll-dir`。本机 OBS 附带的 x64 ANGLE 配对通过 EGL 1.5 / GLES 3.0 离屏自检。原版 ARM64 的 EGL 调用现已桥接到真实 Windows 上下文，`eglGetProcAddress` 的 x64 指针只保存在宿主侧，客体取得可追踪的 ARM64 入口；GL 字符串、整数、精度查询结果和纹理上传数据按客体内存复制。原版图形线程已实际完成 EGL 创建/绑定、能力查询、顶点数组、二维纹理和立方体纹理上传，以及不可变纹理分配；后续又桥接了帧缓冲、采样器、图形状态与清屏等已观察入口。本机独立 ANGLE 自检中的纹理、FBO、状态及释放流程均通过。OBS DLL 仅用于本机实证，发布时须锁定并自带许可合适的 ANGLE 文件。此前的虚拟 EGL/GLES 分支仍保留为严格标注的诊断路径，不能作为渲染或战斗能力证明。
- 2026-10-08：图形线程进入原版 `GameApp.getInstance`、窗口服务、默认 Display 与刷新率查询。诊断器按已核对的 APK/JNI 调用关系提供无窗口 Display，并记录受控刷新率 60 Hz；随后原版创建的 `sc3d_loader` 线程进入 `pthread_cond_wait`，已按 mutex 所有权及等待队列语义停放。Mainloop 对同包列举出的 shader 先检查游戏内 `/update/sc3d/shaders`，该文件在受控客体文件系统中确实不存在，原版随后从同包 assets 打开、读取并关闭 133 个真实 shader，合计读取 2,621,152 字节。实验用本机 APK 的 ZIP 条目压缩方式和解压 SHA-256 核验 `AAsset_openFileDescriptor64` 的负返回值；文件内容取自已解包目录。正式宿主应在导入时保存这一清单，摆脱每次运行对 APK 的查找。`_SC_PAGESIZE` 查询按 [Bionic 常量 0x27](https://android.googlesource.com/platform/bionic/+/master/libc/include/bits/sysconf.h) 返回受控 4 KiB 页大小。原版已首次进入内容初始化虚函数 `libg+0x756be8`，并开始创建后续客体工作线程；content root 仍为零，没有真实战斗或节点快照实证。诊断记录为 `.local-runtime/windows-libg-angle-probe.json`。
- 该内容初始化随后创建 `parallel_queue_worker`，原版线程入口和虚函数回调已在独立客体栈/TLS 上执行，按条件变量等待任务。Mainloop 扫描应用缓存目录：受控目录确实为空，`opendir` 成功、`readdir` 到 EOF、`closedir` 成功。接着从同包 assets 读取完整 `fingerprint.json`（1,826,813 字节），在原版解析过程中没有未支持调用；解析需要超过 200 个每轮 100 万指令的诊断切片。原版检查更新目录中的指纹和内容增量文件，均由实际客体文件状态决定是否存在。随后原版以 `wbe` 模式在客体更新目录写入并关闭 `fingerprint.json`，写出 1,775,401 字节，输出 SHA-256 为 `df97d9ec4d98b651cca0e0bda60eec0c2825ff6e8e8c70cdd9d070fca4c50c47`；该文件只在工作区内被忽略的客体临时目录创建，运行结束清理。**content root 仍为零，不能据此声称数据表或战斗初始化完成。**
- 原版随后从同包读取 `assets.scdb`：先查询 18,587,648 字节长度，再一次读取全部数据并关闭文件；客体内读取数据 SHA-256 为 `5c99be07db214452b78c4400c1a6b0f5eb836fb94bba95021f1ed6693a285a06`，与本地解包资源和对应 APK 条目相同。此前 libc++ 异常展开由诊断器人为设置的单次 16 MiB 分配上限触发；移除该限制后直接通过，无须把它归因于 15.535.3/86 差异。另一个单次读取 16 MiB 限制也已移除，仍核对客体目标内存可写。SQLite 随后查询 `SQLITE_TMPDIR` 和 `TMPDIR`；受控客体环境均未设置，原版继续执行。本地只读 `quick_check` 返回 `ok`，库内 `asset` 表有 13,726 条记录。内容根仍为零，尚无战斗实证。
- 2026-10-09：原版数据路径继续执行，调用 `bsearch` 时由原版 ARM64 比较回调决定查找结果；诊断器只按标准二分查找驱动回调。之后原版通过 APK 的 `ApplicationUtilBase.getKeyValue`/`storeKeyValue` 读取并写入本地设置。受控首次安装存储为空；按已核对的 APK 字节码，缺失键读得空字符串，并在同一进程内记录原版的写入。当前已实测写入 `MusEn=1`、`SfxEn=1`、`Mus=100`、`Sfx=100`，局部 JNI 引用和 UTF-8 字符串释放均走完。最新严格停点是 `GameApp.getDisplayModes()` 方法查找；该显示能力仍未接入。诊断执行至第 574 个调度切片，**content root 为零，标准对局/tick/出牌/节点快照仍未验证。**
- 本机 `assets/fingerprint.json` 是 **15.535.3**，FirstLight 清单使用的运行时资源是 **15.535.86**，但 7 份 APK 内的 `libg.so` 哈希也与当前锁定库完全相同。已找到 3 个解包库副本，哈希均一致；本机没有可证明不同版本的 ak1NaN 库。用户确认游戏内会把网站下载的 15.535.13 APK 资源更新至 15.535.86。这解释了两个内容指纹，不能据此断言当前同包资源无法完成启动或战斗。继续以原版实际访问和校验结果判断何时需要更新内容。常见用户目录未找到独立 15.535.86 文件；旧 AVD 镜像内部和权限拒绝的 NetEase 用户目录尚不能排除。原探针在内容上下文未完成后处理或 `assets.scdb` 未映射时拒绝配置战斗，因此资源路径与内容初始化仍须实测。
- 静态调用链确认 `JNI_OnLoad` 是 ELF 导出入口（偏移 `0x13cfe74`），它通过原版代码注册 15 个 Java 类共 92 个原生方法；`createGameMain` 是其中的 `GameApp` 方法（偏移 `0x13d00e0`），不是可直接调用的 ELF 导出函数。原 APK 先加载 FMOD 和 `libg.so`，依次调用上述两个 application 方法、`createGameMain`，再在界面初始化后调用 `nOnCreate`。内容初始化的 `0x756be8` 是 GameApp 的虚函数，需要真实对象；它先处理 `assets.scdb` 路径，再由 `libg.so` 自己发布 content root/context，数据表处理结束后才设置就绪位，不能用合成内存直接跳进去或手动设置就绪位。原探针从该 root/context 解析标准对局 JSON，取两组原版 context，预热地图，调用 `GameStateManager` 的 new/constructor/setup/load/step。**目前已动态进入该虚函数，正在执行指纹处理与客体文件写入；原版内容根与战斗仍未建立。**
