# 新卡牌 icon 与可用性核对

核对日期：2026-10-11。卡牌 icon 使用 RoyaleAPI 的带边框 PNG（150 × 180，含透明通道），不是角色立绘或无边框的纹理。图标已下载到 `public/cards/`，运行时已支持的六项已接入 `src/data/nativeCards.json`。

| 卡牌／形态 | 图标文件 | 当前战斗系统 |
| --- | --- | --- |
| 浪人 | `ronin.png` | 原生出牌通过，5 费 |
| 觉醒公主 | `evolved-princess.png` | 觉醒出牌通过，3 费 |
| 觉醒野蛮人精锐 | `evolved-elite-barbarians.png` | 觉醒出牌通过，6 费 |
| 精英瓦基丽武神 | `hero-valkyrie.png` | 精英出牌与技能执行确认通过 |
| 精英狂战士 | `hero-berserker.png` | 精英出牌与技能执行确认通过 |
| 精英骷髅墓碑 | `hero-tombstone.png` | 精英出牌与技能执行确认通过 |
| 亡灵巨人（Minion Giant） | `minion-giant.png` | 仅备齐 icon；当前原生卡表没有此单位 |
| 精英寒冰法师 | `hero-ice-wizard.png` | 仅备齐 icon；当前普通冰法没有精英形态配置 |
| 精英雷电法师 | `hero-electro-wizard.png` | 仅备齐 icon；当前普通电法没有精英形态配置 |
| 觉醒雷电巨人 | `evolved-electro-giant.png` | 仅备齐 icon；当前普通电巨人没有觉醒配置 |

后四项没有加入可选卡组或开放形态。普通冰法、电法、电巨人仍可正常使用。

## 验证范围

在独立的 `emulator-5560` 上运行真实原生引擎，使用 16 级卡牌与塔。浪人在 tick 595 出牌；觉醒公主在 tick 1605 确认觉醒出牌，觉醒野蛮人精锐在 tick 1100 确认觉醒出牌；三种精英均在 tick 595 确认精英出牌，并在 tick 616 确认技能执行后原生状态变化。六项均通过，无待确认技能。测试完成后关闭该测试实例。

本次验证覆盖出牌、原生形态标记和技能消耗确认，未逐项验证技能的所有目标交互、长对局与画面表现。前端构建与既有项目领域测试（9 项）通过，全部卡表图标路径存在。

引擎仍使用 `15.535.86` 更新资源，`libg` SHA-256 为 `110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783`。本次仅更新图标与浪人的中文名称，没有迁移战斗引擎或同步后续官方平衡补丁；可出牌不表示数值与当前线上版本完全一致。

详细实测记录保存在本机 `.local-runtime/card-update/native-check.json`，图标来源及 SHA-256 保存在 `.local-runtime/card-update/icon-sources.json`。

## 来源与下载尝试

- 卡牌 icon：`https://cdn.royaleapi.com/static/img/cards-150/<slug>.png`。六个已接入 slug 为 `ronin`、`princess-ev1`、`elite-barbarians-ev1`、`valkyrie-hero`、`berserker-hero`、`tombstone-hero`；四个备用 slug 为 `minion-giant`、`ice-wizard-hero`、`electro-wizard-hero`、`electro-giant-ev1`。
- [官方 8 月更新](https://supercell.com/en/games/clashroyale/blog/news/new-season-k-h-a-o-s/)新增精英武神、精英狂战士与觉醒野蛮人精锐；这些已存在于本机原生资源，且本次实测通过。
- [官方 9 月更新](https://supercell.com/en/games/clashroyale/blog/release-notes/new-season-minion-academy/)新增亡灵巨人与精英冰法；[官方 10 月更新](https://supercell.com/en/games/clashroyale/blog/release-notes/new-season-shocktober/)新增精英电法与觉醒电巨人。这四项不在本机固定原生卡表中。Minion Giant 的中文名采用用户确认的“亡灵巨人”；上述英文公告不作为中文名称的依据，术语表中的“大胃袋”仅作为口语称呼。
- 已按 [SC Downloader GUI](https://github.com/Darknighture/sc-downloader-gui) 的握手格式实际请求国际服与国服。国际服返回 20103／错误码 16，没有新指纹；国服对该版本参数返回错误码 8。用本机已有 masterhash 请求官方 CDN 也未取得新资源。不能用这次尝试证明任意有效新 masterhash 都不可下载，但当前未取得可升级的新资源指纹与配套引擎。
