import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ChangeEvent, MouseEvent, PointerEvent, RefObject, WheelEvent } from "react";
import { DEFAULT_CARDS } from "./data/cards.ts";
import { applyPlayCardToSnapshot, roundCostDelta } from "./domain/cycle.ts";
import {
  ARENA_GRID,
  MAX_TIMER_SECONDS,
  canConnectNodeToParent,
  clamp,
  cloneJson,
  connectNodeToParent,
  createAction,
  createChildNode,
  createNewProject,
  createTowerAction,
  createUnitMarker,
  disconnectNode,
  normalizeProject
} from "./domain/project.ts";
import type {
  CardData,
  PlayerCycleState,
  Point,
  ReplayNodeData,
  ReplayProject,
  Side,
  TowerActionRecord,
  TowerStateData,
  UnitMarkerData
} from "./domain/types.ts";
import {
  downloadProject,
  getDefaultSavesDirectory,
  isTauriDesktop,
  listSavedProjects,
  openProjectFromNativeDialog,
  openSavedProject,
  readProjectFile,
  saveProjectToNativeFile,
  type SavedProjectInfo
} from "./utils/file.ts";

type PendingPlacement = {
  side: Side;
  cardId: string;
} | null;

type DragState =
  | { type: "graph-pan"; pointerId: number; start: Point; origin: Point }
  | { type: "node"; pointerId: number; nodeId: string; start: Point; origin: Point }
  | null;

type UnitDragState = {
  pointerId: number;
  unitId: string;
} | null;

type CommitOptions = {
  undo?: boolean;
};

const CARD_ORDER = new Map(DEFAULT_CARDS.map((card, index) => [card.id, index]));

type UndoEntry = {
  project: ReplayProject;
  currentNodeId: string;
};

type PlayedPulse = {
  side: Side;
  cardId: string;
  nonce: number;
} | null;

type AppScreen = "archive" | "editor";
type Language = "zh" | "en";

const LANGUAGE_STORAGE_KEY = "clash-replay-tree.language";
const HELP_SEEN_STORAGE_KEY = "clash-replay-tree.helpSeen.v1";
const MIN_LOGICAL_WIDTH = 1180;
const MIN_LOGICAL_HEIGHT = 760;
const BATTLE_LOGICAL_WIDTH = 940;
const BATTLE_LOGICAL_HEIGHT = 720;

const UI_TEXT = {
  zh: {
    languageName: "中文",
    alternateLanguageName: "English",
    side: { my: "我方", enemy: "敌方" },
    tower: {
      king: "国王塔",
      princess: "公主塔",
      left: "左塔",
      right: "右塔",
      enemyKing: "敌方国王塔",
      enemyLeft: "敌方左塔",
      enemyRight: "敌方右塔",
      myKing: "我方国王塔",
      myLeft: "我方左塔",
      myRight: "我方右塔"
    },
    notice: {
      ready: "MVP 已就绪：先复制父节点，再在子节点里编辑。",
      chooseArchive: "请选择一个存档，或新建节点树。",
      noArchive: "还没有存档，可以先新建节点树。",
      archiveReadFailed: "读取默认存档目录失败，可以先用“打开其他保存的节点树”。",
      noUndo: "没有可撤回的操作。",
      undone: "已撤回上一步操作。",
      newProject: "已创建新节点树。",
      openedPath: (path: string) => `已打开本地文件：${path}`,
      openedFile: (name: string) => `已打开 ${name}。`,
      openFailed: "打开失败：JSON 格式不正确，或没有文件读取权限。",
      openDefaultFailed: "打开默认存档失败：JSON 格式不正确，或文件不可读取。",
      openJsonFailed: "打开失败：JSON 格式不正确。",
      savedPath: (path: string) => `已保存到本地文件：${path}`,
      saveFailed: "保存失败：没有文件写入权限，或目标路径不可写。",
      cardNotInHand: (side: string, card: string) => `${side}当前手牌里没有 ${card}，未执行出牌。`,
      playedCard: (side: string, card: string) => `${side}打出 ${card}；已更新 snapshot，并追加动作日志。`,
      childCreated: "已创建子节点：复制父节点局面和单位，动作与备注为空。",
      rootCannotDelete: "根节点不能删除。",
      subtreeDeleted: "已删除当前节点及其子树。",
      selectCardFirst: "先点击一张牌进入放置模式，再点击竞技场格子。",
      cardNotPlaced: (side: string, card: string) => `${side}当前手牌里没有 ${card}，未执行出牌和放置。`,
      placedCard: (side: string, card: string, x: string, y: string) =>
        `${side}在 (${x}, ${y}) 打出 ${card}；已更新牌序、费用差、动作日志和场上标记。`,
      rootCannotDisconnect: "根节点不能断开。",
      alreadyDisconnected: "当前节点已经是断开状态。",
      disconnected: "已断开当前节点；节点内容保留，可重新连接到其他父节点。",
      chooseParent: "先选择一个要连接到的父节点。",
      invalidConnect: "这个连接无效：不能连接到自己、子节点或改变根节点。",
      reconnected: (title: string) => `已把当前节点连接到「${title}」。`,
      deckRootOnly: "卡组只能在根节点修改。请先切回根节点。",
      deckApplyRootOnly: "卡组只能应用到根节点初始局面。请先切回根节点。",
      deckApplied: (side: string) => `已把${side}卡组应用到根节点初始手牌和牌序。`,
      deckSettingsRootOnly: "卡组设置只能在根节点修改。请先点击根节点。",
      unitDeleted: "已删除单位标记。",
      towerHpChanged: (tower: string, hp: number) => `${tower}当前血量已改为 ${hp}。`,
      towerMaxRootOnly: "塔的总血量只能在根节点修改；后续节点只能修改当前血量。",
      towerMaxChanged: (tower: string, hp: number) => `${tower}总血量已改为 ${hp}。`,
      onlyPrincessTower: "只能删除公主塔。",
      actionReordered: "已调整动作日志顺序；snapshot 不会自动重算。",
      placementSelected: (side: string, card: string) => `已选中 ${side} ${card}，再次点击竞技场放置。`
    },
    top: {
      archive: "存档页",
      new: "新建",
      open: "打开 JSON",
      save: "保存 JSON",
      undo: "撤回",
      deck: "卡组设置",
      help: "使用说明",
      projectName: "工程名称",
      deckTitleReady: "编辑双方卡组",
      deckTitleBlocked: "卡组只能在根节点修改"
    },
    archive: {
      title: "存档",
      refresh: "刷新",
      newTree: "新建节点树",
      openOther: "打开其他保存的节点树",
      defaultDir: "默认存档目录：",
      loading: "正在读取存档...",
      empty: "暂无默认存档。",
      unknownTime: "时间未知"
    },
    panels: {
      tree: "局面树",
      scale: (value: number) => `缩放 ${value}%`,
      current: "当前局面",
      editor: "编辑器",
      snapshot: "snapshot 为准",
      node: "节点",
      title: "标题",
      note: "节点备注",
      cycleEditor: "当前节点手牌和牌序"
    },
    graph: {
      createChild: "从当前节点创建子节点",
      deleteSubtree: "删除当前节点子树",
      disconnect: "断开节点",
      chooseParent: "选择父节点",
      reconnect: "重新连接",
      selectParentAria: "选择重连父节点",
      meta: (actions: number, markers: number) => `${actions} 动作 / ${markers} 标记`
    },
    timer: {
      title: "节点时间",
      hidden: "不显示",
      minute: "分",
      second: "秒",
      showZero: "显示 0:00",
      clear: "清除",
      hint: "像时钟滚轮一样点上下或滚动调整，最多 3:00。",
      decrease: (label: string) => `${label}减少`,
      increase: (label: string) => `${label}增加`
    },
    arena: {
      grid: "竞技场格子：18 x 32",
      placing: (side: string, card: string) => `放置模式：${side} ${card}`,
      placementHint: "点击卡牌后，再点击格子放置单位标记",
      enemyHalf: "敌方半场",
      myHalf: "我方半场",
      owner: "所属方：",
      health: "生命值：",
      status: "状态：",
      none: "无",
      unset: "未填写"
    },
    cycle: {
      title: "双方下牌",
      subtitle: "手牌同时显示",
      exitPlacement: "退出放置模式",
      active: "当前操作方",
      setActive: "点击设为操作方",
      hand: "手牌",
      queue: "牌序",
      handHint: "双击出牌，单击选中后到竞技场放置",
      queueHint: "单击也可选中放置标记",
      costDelta: "费用差",
      costAria: "费用差，正数我方赚费",
      costHint: "正数我方赚费，负数敌方赚费"
    },
    deck: {
      eyebrow: "Root Node Only",
      title: "根节点卡组设置",
      close: "关闭",
      note: "卡组是工程的初始条件，只能在根节点修改。已有分支节点的局面快照不会被自动覆盖。",
      myDeck: "我方卡组",
      enemyDeck: "敌方卡组",
      applyBoth: "应用双方卡组到根节点",
      handSlot: (index: number) => `手牌 ${index}`,
      queueSlot: (index: number) => `牌序 ${index}`,
      applyRoot: "应用到根节点"
    },
    unit: {
      title: "单位标记",
      empty: "点击竞技场中的单位标记后，在这里编辑状态。",
      side: "所属方",
      card: "卡牌",
      x: "X 格",
      y: "Y 格",
      hp: "当前生命",
      maxHp: "最大生命",
      tags: "状态标签（逗号分隔）",
      tagsPlaceholder: "残血，过河，锁塔",
      note: "单位备注",
      delete: "删除单位标记"
    },
    towerEditor: {
      title: "塔血量",
      hint: "根节点可改总血量；后续节点只记录当前血量变化。",
      deleted: "已删除",
      empty: "点击竞技场上的塔，或在上方列表选择一座塔。",
      hp: "当前血量",
      maxHp: "总血量",
      maxRootOnly: "总血量只能在根节点修改。",
      restorePrincess: "恢复公主塔",
      deletePrincess: "删除公主塔"
    },
    action: {
      title: "动作日志",
      hint: "动作只解释本节点操作；修改或删除不会反推 snapshot。",
      empty: "当前节点还没有动作。",
      drag: "拖动排序",
      dragAria: (card: string) => `拖动排序 ${card}`,
      notePlaceholder: "动作备注",
      delete: "删除",
      towerAdjust: "塔血量调整",
      hpAfter: (hp: number) => `调整后：${hp}`
    },
    help: {
      title: "使用说明",
      firstUse: "首次使用时会显示这份说明，之后可从顶部按钮重新打开。",
      close: "开始使用",
      overviewTitle: "这个工具做什么",
      overview: [
        "用节点树记录一次对局复盘的不同分支。",
        "每个子节点会复制父节点 snapshot；本节点动作只是备注记录，不会反向重算局面。",
        "竞技场支持单位、塔血量、费用差、计时器和动作日志。"
      ],
      workflowTitle: "基本流程",
      workflow: [
        "在存档页新建节点树，根节点先设置双方卡组。",
        "单击卡牌进入放置模式，再点击竞技场格子完成下牌和单位标记。",
        "从当前节点创建子节点来记录下一步局面，必要时断开或重新连接节点。"
      ],
      shortcutsTitle: "快捷键和操作",
      shortcuts: [
        "Ctrl+Z：撤回上一步编辑。",
        "鼠标滚轮：在局面树里缩放；在时间滚轮上调整节点时间。",
        "右键单位：删除单位标记。",
        "动作日志里的“拖动排序”：调整动作顺序，不会重算 snapshot。"
      ],
      layoutTitle: "窗口和显示",
      layout: [
        "窗口较小时页面会整体缩小，避免必须全屏才能使用。",
        "各面板保留内部滚动；竞技场格子始终保持方格和 18:32 比例。"
      ]
    }
  },
  en: {
    languageName: "English",
    alternateLanguageName: "中文",
    side: { my: "Ally", enemy: "Enemy" },
    tower: {
      king: "King Tower",
      princess: "Princess Tower",
      left: "Left Tower",
      right: "Right Tower",
      enemyKing: "Enemy King Tower",
      enemyLeft: "Enemy Left Tower",
      enemyRight: "Enemy Right Tower",
      myKing: "Ally King Tower",
      myLeft: "Ally Left Tower",
      myRight: "Ally Right Tower"
    },
    notice: {
      ready: "MVP ready: create a child by copying the parent, then edit the child snapshot.",
      chooseArchive: "Choose a save, or create a new replay tree.",
      noArchive: "No saves yet. Create a new replay tree first.",
      archiveReadFailed: "Failed to read the default save folder. Use Open other replay tree instead.",
      noUndo: "Nothing to undo.",
      undone: "Undid the last edit.",
      newProject: "Created a new replay tree.",
      openedPath: (path: string) => `Opened local file: ${path}`,
      openedFile: (name: string) => `Opened ${name}.`,
      openFailed: "Open failed: invalid JSON or no read permission.",
      openDefaultFailed: "Failed to open the default save: invalid JSON or unreadable file.",
      openJsonFailed: "Open failed: invalid JSON.",
      savedPath: (path: string) => `Saved to local file: ${path}`,
      saveFailed: "Save failed: no write permission or the target path is not writable.",
      cardNotInHand: (side: string, card: string) => `${side} does not have ${card} in hand. Play was not recorded.`,
      playedCard: (side: string, card: string) => `${side} played ${card}; snapshot and action log updated.`,
      childCreated: "Created a child node: copied position and units; actions and note are empty.",
      rootCannotDelete: "The root node cannot be deleted.",
      subtreeDeleted: "Deleted the current node and its subtree.",
      selectCardFirst: "Select a card first, then click an arena grid cell.",
      cardNotPlaced: (side: string, card: string) => `${side} does not have ${card} in hand. Play and placement were not recorded.`,
      placedCard: (side: string, card: string, x: string, y: string) =>
        `${side} played ${card} at (${x}, ${y}); cycle, cost delta, action log, and marker updated.`,
      rootCannotDisconnect: "The root node cannot be disconnected.",
      alreadyDisconnected: "The current node is already disconnected.",
      disconnected: "Disconnected the current node. Its content is kept and can be reconnected.",
      chooseParent: "Choose a parent node first.",
      invalidConnect: "Invalid connection: cannot connect to itself, a descendant, or change the root node.",
      reconnected: (title: string) => `Connected the current node to "${title}".`,
      deckRootOnly: "Decks can only be changed on the root node.",
      deckApplyRootOnly: "Decks can only be applied to the root starting snapshot.",
      deckApplied: (side: string) => `Applied ${side} deck to the root starting hand and queue.`,
      deckSettingsRootOnly: "Deck settings are only editable on the root node.",
      unitDeleted: "Deleted the unit marker.",
      towerHpChanged: (tower: string, hp: number) => `${tower} current HP changed to ${hp}.`,
      towerMaxRootOnly: "Tower max HP can only be changed on the root node; later nodes only edit current HP.",
      towerMaxChanged: (tower: string, hp: number) => `${tower} max HP changed to ${hp}.`,
      onlyPrincessTower: "Only Princess Towers can be removed.",
      actionReordered: "Action order updated; snapshot is not recalculated.",
      placementSelected: (side: string, card: string) => `Selected ${side} ${card}. Click the arena to place it.`
    },
    top: {
      archive: "Saves",
      new: "New",
      open: "Open JSON",
      save: "Save JSON",
      undo: "Undo",
      deck: "Decks",
      help: "Help",
      projectName: "Project name",
      deckTitleReady: "Edit both decks",
      deckTitleBlocked: "Decks can only be changed on the root node"
    },
    archive: {
      title: "Saves",
      refresh: "Refresh",
      newTree: "New Replay Tree",
      openOther: "Open Other Replay Tree",
      defaultDir: "Default save folder: ",
      loading: "Reading saves...",
      empty: "No default saves yet.",
      unknownTime: "Unknown time"
    },
    panels: {
      tree: "Replay Tree",
      scale: (value: number) => `Zoom ${value}%`,
      current: "Current Position",
      editor: "Editor",
      snapshot: "snapshot source",
      node: "Node",
      title: "Title",
      note: "Node note",
      cycleEditor: "Current node hand and queue"
    },
    graph: {
      createChild: "Create child from current node",
      deleteSubtree: "Delete current subtree",
      disconnect: "Disconnect",
      chooseParent: "Choose parent",
      reconnect: "Reconnect",
      selectParentAria: "Choose reconnect parent",
      meta: (actions: number, markers: number) => `${actions} actions / ${markers} markers`
    },
    timer: {
      title: "Node time",
      hidden: "Hidden",
      minute: "min",
      second: "sec",
      showZero: "Show 0:00",
      clear: "Clear",
      hint: "Click or scroll like a clock wheel. Max 3:00.",
      decrease: (label: string) => `Decrease ${label}`,
      increase: (label: string) => `Increase ${label}`
    },
    arena: {
      grid: "Arena grid: 18 x 32",
      placing: (side: string, card: string) => `Placement mode: ${side} ${card}`,
      placementHint: "Click a card, then click a grid cell to place a marker",
      enemyHalf: "Enemy half",
      myHalf: "Ally half",
      owner: "Side: ",
      health: "HP: ",
      status: "Status: ",
      none: "None",
      unset: "Unset"
    },
    cycle: {
      title: "Both Players",
      subtitle: "Hands shown together",
      exitPlacement: "Exit placement",
      active: "Active side",
      setActive: "Set active side",
      hand: "Hand",
      queue: "Queue",
      handHint: "Double click to play; single click selects for arena placement",
      queueHint: "Single click can also select a marker",
      costDelta: "Cost delta",
      costAria: "Cost delta, positive means ally advantage",
      costHint: "Positive means ally advantage; negative means enemy advantage"
    },
    deck: {
      eyebrow: "Root Node Only",
      title: "Root Deck Settings",
      close: "Close",
      note: "Decks are project starting conditions and can only be changed on the root node. Existing branch snapshots are not overwritten.",
      myDeck: "Ally deck",
      enemyDeck: "Enemy deck",
      applyBoth: "Apply both decks to root",
      handSlot: (index: number) => `Hand ${index}`,
      queueSlot: (index: number) => `Queue ${index}`,
      applyRoot: "Apply to root"
    },
    unit: {
      title: "Unit Marker",
      empty: "Select a unit marker on the arena to edit its state here.",
      side: "Side",
      card: "Card",
      x: "X cell",
      y: "Y cell",
      hp: "Current HP",
      maxHp: "Max HP",
      tags: "Status tags, comma separated",
      tagsPlaceholder: "low HP, crossed, locked tower",
      note: "Unit note",
      delete: "Delete unit marker"
    },
    towerEditor: {
      title: "Tower HP",
      hint: "Root node edits max HP; later nodes only record current HP changes.",
      deleted: "Removed",
      empty: "Click a tower on the arena, or select one above.",
      hp: "Current HP",
      maxHp: "Max HP",
      maxRootOnly: "Max HP can only be changed on the root node.",
      restorePrincess: "Restore Princess Tower",
      deletePrincess: "Remove Princess Tower"
    },
    action: {
      title: "Action Log",
      hint: "Actions describe only this node; edits or deletes do not recalculate snapshot.",
      empty: "No actions in the current node yet.",
      drag: "Drag to sort",
      dragAria: (card: string) => `Drag to reorder ${card}`,
      notePlaceholder: "Action note",
      delete: "Delete",
      towerAdjust: "Tower HP adjustment",
      hpAfter: (hp: number) => `After: ${hp}`
    },
    help: {
      title: "User Guide",
      firstUse: "This guide appears on first launch. You can reopen it from the top bar.",
      close: "Start",
      overviewTitle: "What this tool does",
      overview: [
        "Record replay analysis branches with a position tree.",
        "Each child node copies the parent snapshot. Actions are notes only and do not reverse-calculate state.",
        "The arena supports units, tower HP, cost delta, timer, and action logs."
      ],
      workflowTitle: "Basic workflow",
      workflow: [
        "Create a replay tree from the save page, then set both decks on the root node.",
        "Click a card to enter placement mode, then click the arena grid to record the play and marker.",
        "Create child nodes for later decisions. Disconnect and reconnect nodes when needed."
      ],
      shortcutsTitle: "Shortcuts and controls",
      shortcuts: [
        "Ctrl+Z: undo the last edit.",
        "Mouse wheel: zoom the replay tree; scroll on the time wheel to adjust node time.",
        "Right click a unit: delete the unit marker.",
        "Drag to sort in the action log: changes action order without recalculating snapshot."
      ],
      layoutTitle: "Window and display",
      layout: [
        "When the window is small, the whole page scales down so fullscreen is not required.",
        "Panels keep their own scrolling; the arena keeps square cells and an 18:32 ratio."
      ]
    }
  }
} as const;

type UiText = (typeof UI_TEXT)[Language];

const UiContext = createContext<UiText>(UI_TEXT.zh);

function useUi(): UiText {
  return useContext(UiContext);
}

function readStoredLanguage(): Language {
  if (typeof window === "undefined") {
    return "zh";
  }

  const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  return stored === "en" || stored === "zh" ? stored : "zh";
}

function hasSeenHelp(): boolean {
  return typeof window !== "undefined" && window.localStorage.getItem(HELP_SEEN_STORAGE_KEY) === "1";
}

function useScaledViewport(): { scale: number; style: CSSProperties } {
  const [viewport, setViewport] = useState(() => ({
    width: typeof window === "undefined" ? MIN_LOGICAL_WIDTH : window.innerWidth,
    height: typeof window === "undefined" ? MIN_LOGICAL_HEIGHT : window.innerHeight
  }));

  useEffect(() => {
    function updateViewport(): void {
      setViewport({
        width: window.innerWidth,
        height: window.innerHeight
      });
    }

    updateViewport();
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

  const scale = Math.min(
    1,
    viewport.width / MIN_LOGICAL_WIDTH,
    viewport.height / MIN_LOGICAL_HEIGHT
  );
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : 1;

  return {
    scale: safeScale,
    style: {
      width: `${viewport.width / safeScale}px`,
      height: `${viewport.height / safeScale}px`,
      transform: `scale(${safeScale})`
    }
  };
}

function useElementScale(
  logicalWidth: number,
  logicalHeight: number
): {
  ref: (element: HTMLDivElement | null) => void;
  scale: number;
  frameStyle: CSSProperties;
  canvasStyle: CSSProperties;
} {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: logicalWidth, height: logicalHeight });
  const ref = useCallback((nextElement: HTMLDivElement | null) => {
    setElement(nextElement);
  }, []);

  useEffect(() => {
    if (!element) {
      return;
    }

    const observedElement = element;
    function updateSize(): void {
      const styles = window.getComputedStyle(observedElement);
      const horizontalPadding = parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
      const verticalPadding = parseFloat(styles.paddingTop) + parseFloat(styles.paddingBottom);
      setSize({
        width: Math.max(0, observedElement.clientWidth - horizontalPadding),
        height: Math.max(0, observedElement.clientHeight - verticalPadding)
      });
    }

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(observedElement);
    return () => observer.disconnect();
  }, [element]);

  const scale = Math.min(1, size.width / logicalWidth, size.height / logicalHeight);
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : 1;

  return {
    ref,
    scale: safeScale,
    frameStyle: {
      width: `${logicalWidth * safeScale}px`,
      height: `${logicalHeight * safeScale}px`
    },
    canvasStyle: {
      width: `${logicalWidth}px`,
      height: `${logicalHeight}px`,
      transform: `scale(${safeScale})`,
      transformOrigin: "0 0"
    }
  };
}

const ARENA_TOWERS = [
  { id: "enemy-king", side: "enemy", type: "king", label: "国王塔", shortLabel: "敌方国王塔", x: 9, y: 3, size: 4 },
  { id: "enemy-left-princess", side: "enemy", type: "princess", label: "左塔", shortLabel: "敌方左塔", x: 3.5, y: 6.5, size: 3 },
  { id: "enemy-right-princess", side: "enemy", type: "princess", label: "右塔", shortLabel: "敌方右塔", x: 14.5, y: 6.5, size: 3 },
  { id: "my-left-princess", side: "my", type: "princess", label: "左塔", shortLabel: "我方左塔", x: 3.5, y: 25.5, size: 3 },
  { id: "my-right-princess", side: "my", type: "princess", label: "右塔", shortLabel: "我方右塔", x: 14.5, y: 25.5, size: 3 },
  { id: "my-king", side: "my", type: "king", label: "国王塔", shortLabel: "我方国王塔", x: 9, y: 29, size: 4 }
] as const;

type TowerMetadata = (typeof ARENA_TOWERS)[number];

const ARENA_ROADS = [
  { id: "enemy-cross", side: "enemy", x: 9, y: 3.5, width: 12, height: 1 },
  { id: "enemy-left-main", side: "enemy", x: 3.5, y: 9.5, width: 1, height: 11 },
  { id: "enemy-right-main", side: "enemy", x: 14.5, y: 9.5, width: 1, height: 11 },
  { id: "my-cross", side: "my", x: 9, y: 28.5, width: 12, height: 1 },
  { id: "my-left-main", side: "my", x: 3.5, y: 22.5, width: 1, height: 11 },
  { id: "my-right-main", side: "my", x: 14.5, y: 22.5, width: 1, height: 11 }
] as const;

const ARENA_BRIDGES = [
  { id: "left-bridge", x: 3.5, y: 16, width: 2.6, height: 2 },
  { id: "right-bridge", x: 14.5, y: 16, width: 2.6, height: 2 }
] as const;

function App() {
  const [language, setLanguage] = useState<Language>(() => readStoredLanguage());
  const ui = UI_TEXT[language];
  const scaledViewport = useScaledViewport();
  const battleScale = useElementScale(BATTLE_LOGICAL_WIDTH, BATTLE_LOGICAL_HEIGHT);
  const [screen, setScreen] = useState<AppScreen>("archive");
  const [project, setProject] = useState<ReplayProject>(() => createNewProject());
  const [currentNodeId, setCurrentNodeId] = useState(project.rootNodeId);
  const [activeSide, setActiveSide] = useState<Side>("my");
  const [pendingPlacement, setPendingPlacement] = useState<PendingPlacement>(null);
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);
  const [selectedTowerId, setSelectedTowerId] = useState<string | null>(null);
  const [hoveredUnitId, setHoveredUnitId] = useState<string | null>(null);
  const [graphScale, setGraphScale] = useState(1);
  const [graphOffset, setGraphOffset] = useState<Point>({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<DragState>(null);
  const [unitDragState, setUnitDragState] = useState<UnitDragState>(null);
  const [deckSettingsOpen, setDeckSettingsOpen] = useState(false);
  const [currentFilePath, setCurrentFilePath] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>(() => UI_TEXT[readStoredLanguage()].notice.ready);
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [reconnectTargetId, setReconnectTargetId] = useState("");
  const [playedPulse, setPlayedPulse] = useState<PlayedPulse>(null);
  const [savedProjects, setSavedProjects] = useState<SavedProjectInfo[]>([]);
  const [savesDirectory, setSavesDirectory] = useState<string | null>(null);
  const [archiveNotice, setArchiveNotice] = useState<string>(() => UI_TEXT[readStoredLanguage()].notice.chooseArchive);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [helpOpen, setHelpOpen] = useState(() => !hasSeenHelp());
  const fileInputRef = useRef<HTMLInputElement>(null);

  const currentNode = project.nodes[currentNodeId] ?? project.nodes[project.rootNodeId];
  const cards = project.cards;
  const cardList = useMemo(
    () =>
      Object.values(cards).sort(
        (a, b) =>
          (CARD_ORDER.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
            (CARD_ORDER.get(b.id) ?? Number.MAX_SAFE_INTEGER) ||
          a.cost - b.cost ||
          a.name.localeCompare(b.name, "zh-Hans")
      ),
    [cards]
  );
  const selectedUnit = currentNode.unitMarkers.find((unit) => unit.id === selectedUnitId) ?? null;
  const selectedTower = selectedTowerId ? getTowerMetadata(selectedTowerId) : null;
  const hoveredUnit = currentNode.unitMarkers.find((unit) => unit.id === hoveredUnitId) ?? null;
  const isRootNode = currentNode.id === project.rootNodeId;
  const graphNodes = useMemo(() => Object.values(project.nodes), [project.nodes]);
  const reconnectCandidates = useMemo(
    () => graphNodes.filter((node) => canConnectNodeToParent(project, currentNode.id, node.id)),
    [currentNode.id, graphNodes, project]
  );

  useEffect(() => {
    void refreshSavedProjects();
  }, []);

  useEffect(() => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  }, [language]);

  function toggleLanguage(): void {
    setLanguage((previous) => {
      const next = previous === "zh" ? "en" : "zh";
      const nextUi = UI_TEXT[next];
      setArchiveNotice(savedProjects.length > 0 ? nextUi.notice.chooseArchive : nextUi.notice.noArchive);
      if (notice === UI_TEXT[previous].notice.ready) {
        setNotice(nextUi.notice.ready);
      }
      return next;
    });
  }

  function closeHelp(): void {
    window.localStorage.setItem(HELP_SEEN_STORAGE_KEY, "1");
    setHelpOpen(false);
  }

  async function refreshSavedProjects(): Promise<void> {
    if (!isTauriDesktop()) {
      return;
    }

    setArchiveLoading(true);
    try {
      const [directory, saves] = await Promise.all([
        getDefaultSavesDirectory(),
        listSavedProjects()
      ]);
      setSavesDirectory(directory);
      setSavedProjects(saves);
      setArchiveNotice(saves.length > 0 ? ui.notice.chooseArchive : ui.notice.noArchive);
    } catch {
      setArchiveNotice(ui.notice.archiveReadFailed);
    } finally {
      setArchiveLoading(false);
    }
  }

  function loadProjectIntoEditor(next: ReplayProject, filePath: string | null, message: string): void {
    setProject(next);
    setCurrentNodeId(next.rootNodeId);
    setSelectedUnitId(null);
    setSelectedTowerId(null);
    setHoveredUnitId(null);
    setPendingPlacement(null);
    setDeckSettingsOpen(false);
    setCurrentFilePath(filePath);
    setUndoStack([]);
    setReconnectTargetId("");
    setPlayedPulse(null);
    setScreen("editor");
    setNotice(message);
  }

  function pushUndoSnapshot(snapshotProject = project, snapshotNodeId = currentNodeId): void {
    setUndoStack((previous) => [
      ...previous.slice(-79),
      {
        project: cloneJson(snapshotProject),
        currentNodeId: snapshotNodeId
      }
    ]);
  }

  function commitProject(
    updater: (draft: ReplayProject) => ReplayProject,
    options: CommitOptions = {}
  ): void {
    if (options.undo !== false) {
      pushUndoSnapshot();
    }

    setProject((previous) => {
      const next = updater(previous);
      return {
        ...next,
        updatedAt: new Date().toISOString()
      };
    });
  }

  function updateCurrentNode(
    updater: (node: ReplayNodeData) => ReplayNodeData,
    options?: CommitOptions
  ): void {
    commitProject((previous) => {
      const node = previous.nodes[currentNodeId];
      if (!node) {
        return previous;
      }

      const nextNode = updater(node);
      return {
        ...previous,
        nodes: {
          ...previous.nodes,
          [nextNode.id]: nextNode
        }
      };
    }, options);
  }

  function handleUndo(): void {
    const undoEntry = undoStack[undoStack.length - 1];
    if (!undoEntry) {
      setNotice(ui.notice.noUndo);
      return;
    }

    setUndoStack((previous) => previous.slice(0, -1));
    setProject(undoEntry.project);
    setCurrentNodeId(
      undoEntry.project.nodes[undoEntry.currentNodeId]
        ? undoEntry.currentNodeId
        : undoEntry.project.rootNodeId
    );
    setSelectedUnitId(null);
    setSelectedTowerId(null);
    setPendingPlacement(null);
    setDeckSettingsOpen(false);
    setReconnectTargetId("");
    setNotice(ui.notice.undone);
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !event.shiftKey) {
        event.preventDefault();
        handleUndo();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [undoStack]);

  useEffect(() => {
    if (reconnectTargetId && !canConnectNodeToParent(project, currentNode.id, reconnectTargetId)) {
      setReconnectTargetId("");
    }
  }, [currentNode.id, project, reconnectTargetId]);

  function handleNewProject(): void {
    const next = createNewProject(project.projectName ? `${project.projectName} 副本` : undefined);
    loadProjectIntoEditor(next, null, ui.notice.newProject);
  }

  async function handleOpenProjectClick(): Promise<void> {
    if (!isTauriDesktop()) {
      fileInputRef.current?.click();
      return;
    }

    try {
      const opened = await openProjectFromNativeDialog();
      if (!opened) {
        return;
      }
      const next = normalizeProject(opened.raw);
      loadProjectIntoEditor(next, opened.path, ui.notice.openedPath(opened.path));
    } catch {
      setNotice(ui.notice.openFailed);
      setArchiveNotice(ui.notice.openFailed);
    }
  }

  async function handleOpenSavedProject(path: string): Promise<void> {
    try {
      const opened = await openSavedProject(path);
      const next = normalizeProject(opened.raw);
      loadProjectIntoEditor(next, opened.path, ui.notice.openedPath(opened.path));
    } catch {
      setArchiveNotice(ui.notice.openDefaultFailed);
    }
  }

  async function handleOpenProject(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      const raw = await readProjectFile(file);
      const next = normalizeProject(raw);
      loadProjectIntoEditor(next, null, ui.notice.openedFile(file.name));
    } catch {
      setNotice(ui.notice.openJsonFailed);
      setArchiveNotice(ui.notice.openJsonFailed);
    } finally {
      event.target.value = "";
    }
  }

  async function handleSaveProject(): Promise<void> {
    if (!isTauriDesktop()) {
      downloadProject(project);
      return;
    }

    try {
      const savedPath = await saveProjectToNativeFile(project, currentFilePath);
      if (!savedPath) {
        return;
      }
      setCurrentFilePath(savedPath);
      setNotice(ui.notice.savedPath(savedPath));
      await refreshSavedProjects();
    } catch {
      setNotice(ui.notice.saveFailed);
    }
  }

  function handlePlayCard(side: Side, cardId: string, position?: Point): void {
    const card = cards[cardId];
    if (!card) {
      return;
    }

    const nextSnapshot = applyPlayCardToSnapshot(currentNode.snapshot, side, card);
    if (!nextSnapshot) {
      setNotice(ui.notice.cardNotInHand(ui.side[side], card.name));
      return;
    }

    updateCurrentNode((node) => {
      const action = createAction(side, cardId, node.actions.length + 1, position);
      return {
        ...node,
        snapshot: nextSnapshot,
        actions: [...node.actions, action]
      };
    });
    setPendingPlacement(null);
    const pulse: PlayedPulse = { side, cardId, nonce: Date.now() };
    setPlayedPulse(pulse);
    window.setTimeout(() => {
      setPlayedPulse((previous) => (previous?.nonce === pulse.nonce ? null : previous));
    }, 460);
    setNotice(ui.notice.playedCard(ui.side[side], card.name));
  }

  function handleCreateChild(): void {
    const parent = project.nodes[currentNodeId];
    if (!parent) {
      return;
    }

    const child = createChildNode(parent, parent.childrenIds.length);
    commitProject((previous) => ({
      ...previous,
      nodes: {
        ...previous.nodes,
        [parent.id]: {
          ...parent,
          childrenIds: [...parent.childrenIds, child.id]
        },
        [child.id]: child
      }
    }));
    setCurrentNodeId(child.id);
    setSelectedUnitId(null);
    setSelectedTowerId(null);
    setNotice(ui.notice.childCreated);
  }

  function handleDeleteCurrentNode(): void {
    if (currentNode.id === project.rootNodeId) {
      setNotice(ui.notice.rootCannotDelete);
      return;
    }

    const idsToDelete = collectSubtreeIds(project, currentNode.id);
    const parentId = currentNode.parentId;
    if (!parentId) {
      return;
    }

    commitProject((previous) => {
      const nextNodes = { ...previous.nodes };
      for (const nodeId of idsToDelete) {
        delete nextNodes[nodeId];
      }

      const parent = nextNodes[parentId];
      if (parent) {
        nextNodes[parentId] = {
          ...parent,
          childrenIds: parent.childrenIds.filter((id) => id !== currentNode.id)
        };
      }

      return {
        ...previous,
        nodes: nextNodes
      };
    });
    setCurrentNodeId(parentId);
    setSelectedUnitId(null);
    setSelectedTowerId(null);
    setNotice(ui.notice.subtreeDeleted);
  }

  function handleArenaClick(point: Point): void {
    if (!pendingPlacement) {
      setNotice(ui.notice.selectCardFirst);
      return;
    }

    const placement = pendingPlacement;
    const card = cards[placement.cardId];
    if (!card) {
      return;
    }

    const nextSnapshot = applyPlayCardToSnapshot(currentNode.snapshot, placement.side, card);
    if (!nextSnapshot) {
      setNotice(ui.notice.cardNotPlaced(ui.side[placement.side], card.name));
      return;
    }

    const unitMarker = createUnitMarker(placement.side, card, point);
    updateCurrentNode((node) => {
      const action = createAction(placement.side, card.id, node.actions.length + 1, point);
      return {
        ...node,
        snapshot: nextSnapshot,
        actions: [...node.actions, action],
        unitMarkers: [...node.unitMarkers, unitMarker]
      };
    });
    setPendingPlacement(null);
    setSelectedUnitId(unitMarker.id);
    setSelectedTowerId(null);
    setHoveredUnitId(null);
    const pulse: PlayedPulse = { side: placement.side, cardId: card.id, nonce: Date.now() };
    setPlayedPulse(pulse);
    window.setTimeout(() => {
      setPlayedPulse((previous) => (previous?.nonce === pulse.nonce ? null : previous));
    }, 460);
    setNotice(
      ui.notice.placedCard(
        ui.side[placement.side],
        card.name,
        point.x.toFixed(1),
        point.y.toFixed(1)
      )
    );
  }

  function handleGraphPointerDown(event: PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return;
    }

    setDragState({
      type: "graph-pan",
      pointerId: event.pointerId,
      start: { x: event.clientX, y: event.clientY },
      origin: graphOffset
    });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleGraphPointerMove(event: PointerEvent<HTMLDivElement>): void {
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }

    if (dragState.type === "graph-pan") {
      setGraphOffset({
        x: dragState.origin.x + (event.clientX - dragState.start.x) / scaledViewport.scale,
        y: dragState.origin.y + (event.clientY - dragState.start.y) / scaledViewport.scale
      });
      return;
    }

    const dx = (event.clientX - dragState.start.x) / (graphScale * scaledViewport.scale);
    const dy = (event.clientY - dragState.start.y) / (graphScale * scaledViewport.scale);
    const nextPosition = {
      x: dragState.origin.x + dx,
      y: dragState.origin.y + dy
    };
    commitProject(
      (previous) => {
        const node = previous.nodes[dragState.nodeId];
        if (!node) {
          return previous;
        }
        return {
          ...previous,
          nodes: {
            ...previous.nodes,
            [node.id]: {
              ...node,
              graphPosition: nextPosition
            }
          }
        };
      },
      { undo: false }
    );
  }

  function handleGraphPointerUp(event: PointerEvent<HTMLDivElement>): void {
    if (dragState?.pointerId === event.pointerId) {
      setDragState(null);
    }
  }

  function handleGraphWheel(event: WheelEvent<HTMLDivElement>): void {
    event.preventDefault();
    const nextScale = clamp(graphScale + (event.deltaY > 0 ? -0.08 : 0.08), 0.45, 1.8);
    setGraphScale(nextScale);
  }

  function handleNodePointerDown(event: PointerEvent<HTMLButtonElement>, node: ReplayNodeData): void {
    event.stopPropagation();
    setCurrentNodeId(node.id);
    setSelectedUnitId(null);
    setSelectedTowerId(null);
    setDragState({
      type: "node",
      pointerId: event.pointerId,
      nodeId: node.id,
      start: { x: event.clientX, y: event.clientY },
      origin: node.graphPosition
    });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleDisconnectCurrentNode(): void {
    if (currentNode.id === project.rootNodeId) {
      setNotice(ui.notice.rootCannotDisconnect);
      return;
    }
    if (!currentNode.parentId) {
      setNotice(ui.notice.alreadyDisconnected);
      return;
    }

    commitProject((previous) => disconnectNode(previous, currentNode.id));
    setReconnectTargetId("");
    setNotice(ui.notice.disconnected);
  }

  function handleReconnectCurrentNode(): void {
    if (!reconnectTargetId) {
      setNotice(ui.notice.chooseParent);
      return;
    }
    if (!canConnectNodeToParent(project, currentNode.id, reconnectTargetId)) {
      setNotice(ui.notice.invalidConnect);
      return;
    }

    const target = project.nodes[reconnectTargetId];
    commitProject((previous) => connectNodeToParent(previous, currentNode.id, reconnectTargetId));
    setReconnectTargetId("");
    setNotice(ui.notice.reconnected(target?.title ?? ui.graph.chooseParent));
  }

  function updateDeck(side: Side, index: number, cardId: string): void {
    if (!isRootNode) {
      setNotice(ui.notice.deckRootOnly);
      return;
    }

    commitProject((previous) => {
      const deckKey = side === "my" ? "myDeck" : "enemyDeck";
      const nextDeck = [...previous[deckKey]];
      nextDeck[index] = cardId;
      return {
        ...previous,
        [deckKey]: nextDeck
      };
    });
  }

  function applyDeckToRootSnapshot(side: Side): void {
    if (!isRootNode) {
      setNotice(ui.notice.deckApplyRootOnly);
      return;
    }

    const deck = side === "my" ? project.myDeck : project.enemyDeck;
    updateCurrentNode((node) => {
      const snapshot = {
        ...node.snapshot,
        [side === "my" ? "myState" : "enemyState"]: {
          hand: deck.slice(0, 4),
          drawQueue: deck.slice(4, 8)
        }
      };
      return { ...node, snapshot };
    });
    setNotice(ui.notice.deckApplied(ui.side[side]));
  }

  function handleOpenDeckSettings(): void {
    if (!isRootNode) {
      setNotice(ui.notice.deckSettingsRootOnly);
      return;
    }

    setDeckSettingsOpen(true);
  }

  function updateCycleSlot(side: Side, zone: "hand" | "drawQueue", index: number, cardId: string): void {
    updateCurrentNode((node) => {
      const stateKey = side === "my" ? "myState" : "enemyState";
      const nextState = {
        ...node.snapshot[stateKey],
        [zone]: node.snapshot[stateKey][zone].map((value, itemIndex) =>
          itemIndex === index ? cardId : value
        )
      };
      return {
        ...node,
        snapshot: {
          ...node.snapshot,
          [stateKey]: nextState
        }
      };
    });
  }

  function updateUnit(
    unitId: string,
    updater: (unit: UnitMarkerData) => UnitMarkerData,
    options?: CommitOptions
  ): void {
    updateCurrentNode((node) => ({
      ...node,
      unitMarkers: node.unitMarkers.map((unit) => (unit.id === unitId ? updater(unit) : unit))
    }), options);
  }

  function deleteUnit(unitId: string): void {
    updateCurrentNode((node) => ({
      ...node,
      unitMarkers: node.unitMarkers.filter((unit) => unit.id !== unitId)
    }));
    setSelectedUnitId(null);
    setNotice(ui.notice.unitDeleted);
  }

  function updateTowerCurrentHp(towerId: string, rawHp: number): void {
    const tower = getTowerMetadata(towerId);
    if (!tower) {
      return;
    }

    const maxHp = getTowerMaxHp(project, towerId);
    const nextHp = Math.round(clamp(rawHp, 0, maxHp));
    updateCurrentNode((node) => {
      const previousState = getTowerState(node, towerId, project.towerMaxHp);
      const delta = nextHp - previousState.hp;
      const destroyed = tower.type === "princess" && nextHp <= 0 ? true : false;
      const nextTowerStates = {
        ...node.towerStates,
        [towerId]: {
          ...previousState,
          hp: nextHp,
          destroyed
        }
      };

      if (delta === 0 && previousState.destroyed === destroyed) {
        return {
          ...node,
          towerStates: nextTowerStates
        };
      }

      return {
        ...node,
        towerStates: nextTowerStates,
        towerActions:
          delta === 0
            ? node.towerActions
            : [
                ...node.towerActions,
                createTowerAction(towerId, delta, nextHp, node.towerActions.length + 1)
              ]
      };
    });
    setSelectedTowerId(towerId);
    setSelectedUnitId(null);
    setNotice(ui.notice.towerHpChanged(formatTowerName(tower.id, language), nextHp));
  }

  function updateTowerMaxHp(towerId: string, rawMaxHp: number): void {
    if (!isRootNode) {
      setNotice(ui.notice.towerMaxRootOnly);
      return;
    }

    const tower = getTowerMetadata(towerId);
    if (!tower) {
      return;
    }

    const nextMaxHp = Math.max(1, Math.round(rawMaxHp));
    commitProject((previous) => ({
      ...previous,
      towerMaxHp: {
        ...previous.towerMaxHp,
        [towerId]: nextMaxHp
      },
      nodes: Object.fromEntries(
        Object.entries(previous.nodes).map(([nodeId, node]) => {
          const state = getTowerState(node, towerId, previous.towerMaxHp);
          return [
            nodeId,
            {
              ...node,
              towerStates: {
                ...node.towerStates,
                [towerId]: {
                  ...state,
                  hp: Math.round(clamp(state.hp, 0, nextMaxHp))
                }
              }
            }
          ];
        })
      )
    }));
    setSelectedTowerId(towerId);
    setSelectedUnitId(null);
    setNotice(ui.notice.towerMaxChanged(formatTowerName(tower.id, language), nextMaxHp));
  }

  function deletePrincessTower(towerId: string): void {
    const tower = getTowerMetadata(towerId);
    if (!tower || tower.type !== "princess") {
      setNotice(ui.notice.onlyPrincessTower);
      return;
    }

    updateTowerCurrentHp(towerId, 0);
  }

  function restorePrincessTower(towerId: string): void {
    const tower = getTowerMetadata(towerId);
    if (!tower || tower.type !== "princess") {
      return;
    }

    updateTowerCurrentHp(towerId, getTowerMaxHp(project, towerId));
  }

  function reorderAction(
    actionId: string,
    targetActionId: string,
    placement: "before" | "after"
  ): void {
    updateCurrentNode((node) => {
      const sorted = [...node.actions].sort((a, b) => a.order - b.order);
      const sourceIndex = sorted.findIndex((action) => action.id === actionId);
      const targetIndex = sorted.findIndex((action) => action.id === targetActionId);
      if (sourceIndex < 0 || targetIndex < 0 || actionId === targetActionId) {
        return node;
      }
      const [action] = sorted.splice(sourceIndex, 1);
      const nextTargetIndex = sorted.findIndex((item) => item.id === targetActionId);
      const insertionIndex = placement === "after" ? nextTargetIndex + 1 : nextTargetIndex;
      sorted.splice(insertionIndex, 0, action);
      return {
        ...node,
        actions: sorted.map((item, itemIndex) => ({
          ...item,
          order: itemIndex + 1
        }))
      };
    });
    setNotice(ui.notice.actionReordered);
  }

  if (screen === "archive") {
    return (
      <UiContext.Provider value={ui}>
        <ArchivePage
          savedProjects={savedProjects}
          savesDirectory={savesDirectory}
          notice={archiveNotice}
          loading={archiveLoading}
          fileInputRef={fileInputRef}
          scaledStyle={scaledViewport.style}
          language={language}
          onToggleLanguage={toggleLanguage}
          onOpenHelp={() => setHelpOpen(true)}
          onFileChange={handleOpenProject}
          onNewProject={handleNewProject}
          onOpenProjectClick={handleOpenProjectClick}
          onOpenSavedProject={handleOpenSavedProject}
          onRefresh={refreshSavedProjects}
        />
        {helpOpen ? <HelpModal language={language} onToggleLanguage={toggleLanguage} onClose={closeHelp} /> : null}
      </UiContext.Provider>
    );
  }

  return (
    <UiContext.Provider value={ui}>
    <div className="app-shell" style={scaledViewport.style}>
      <header className="top-bar">
        <div>
          <div className="eyebrow">Clash Royale Replay Tree</div>
          <input
            className="project-title-input"
            value={project.projectName}
            onChange={(event) =>
              commitProject((previous) => ({
                ...previous,
                projectName: event.target.value
              }))
            }
            aria-label={ui.top.projectName}
          />
        </div>
        <div className="top-actions">
          <button className="language-toggle" onClick={toggleLanguage} title={ui.alternateLanguageName}>
            {language === "zh" ? "EN" : "中文"}
          </button>
          <button onClick={() => setHelpOpen(true)}>{ui.top.help}</button>
          <button
            onClick={() => {
              setScreen("archive");
              void refreshSavedProjects();
            }}
          >
            {ui.top.archive}
          </button>
          <button onClick={handleNewProject}>{ui.top.new}</button>
          <button onClick={handleOpenProjectClick}>{ui.top.open}</button>
          <button onClick={handleSaveProject}>{ui.top.save}</button>
          <button onClick={handleUndo} disabled={undoStack.length === 0} title="Ctrl+Z">
            {ui.top.undo}
          </button>
          <button
            className="deck-settings-button"
            disabled={!isRootNode}
            onClick={handleOpenDeckSettings}
            title={isRootNode ? ui.top.deckTitleReady : ui.top.deckTitleBlocked}
          >
            {ui.top.deck}
          </button>
          <input
            ref={fileInputRef}
            className="hidden-file"
            type="file"
            accept=".json,.cr-replay.json,application/json"
            onChange={handleOpenProject}
          />
        </div>
      </header>

      <main className="workspace">
        <section className="graph-panel">
          <PanelHeader title={ui.panels.tree} detail={ui.panels.scale(Math.round(graphScale * 100))} />
          <div className="graph-toolbar">
            <button onClick={handleCreateChild}>{ui.graph.createChild}</button>
            <button onClick={handleDeleteCurrentNode}>{ui.graph.deleteSubtree}</button>
            <button
              onClick={handleDisconnectCurrentNode}
              disabled={currentNode.id === project.rootNodeId || !currentNode.parentId}
            >
              {ui.graph.disconnect}
            </button>
            <select
              className="graph-reconnect-select"
              value={reconnectTargetId}
              onChange={(event) => setReconnectTargetId(event.target.value)}
              disabled={reconnectCandidates.length === 0}
              aria-label={ui.graph.selectParentAria}
            >
              <option value="">{ui.graph.chooseParent}</option>
              {reconnectCandidates.map((node) => (
                <option key={node.id} value={node.id}>
                  {node.title}
                </option>
              ))}
            </select>
            <button onClick={handleReconnectCurrentNode} disabled={!reconnectTargetId}>
              {ui.graph.reconnect}
            </button>
          </div>
          <div
            className="graph-canvas"
            onPointerDown={handleGraphPointerDown}
            onPointerMove={handleGraphPointerMove}
            onPointerUp={handleGraphPointerUp}
            onPointerCancel={handleGraphPointerUp}
            onWheel={handleGraphWheel}
          >
            <div
              className="graph-world"
              style={{
                transform: `translate(${graphOffset.x}px, ${graphOffset.y}px) scale(${graphScale})`
              }}
            >
              <svg className="graph-edges" width="2200" height="1600" aria-hidden="true">
                {graphNodes.flatMap((node) =>
                  node.childrenIds.map((childId) => {
                    const child = project.nodes[childId];
                    if (!child) {
                      return null;
                    }
                    const nodeHeight = estimateGraphNodeHeight(node, cards, language);
                    const start = {
                      x: node.graphPosition.x + 84,
                      y: node.graphPosition.y + nodeHeight
                    };
                    const end = {
                      x: child.graphPosition.x + 84,
                      y: child.graphPosition.y
                    };
                    return (
                      <path
                        key={`${node.id}_${child.id}`}
                        d={`M ${start.x} ${start.y} C ${start.x} ${start.y + 58}, ${end.x} ${end.y - 58}, ${end.x} ${end.y}`}
                        className="edge-path"
                      />
                    );
                  })
                )}
              </svg>
              {graphNodes.map((node) => (
                <GraphNodeButton
                  key={node.id}
                  node={node}
                  cards={cards}
                  language={language}
                  isCurrent={node.id === currentNode.id}
                  onPointerDown={handleNodePointerDown}
                />
              ))}
            </div>
          </div>
        </section>

        <section className="center-panel">
          <PanelHeader title={ui.panels.current} detail={currentNode.title} />
          <div className="notice">{notice}</div>
          <div className="battle-viewport" ref={battleScale.ref}>
            <div className="battle-fit" style={battleScale.frameStyle}>
              <div className="battle-layout" style={battleScale.canvasStyle}>
                <ArenaBoard
                  currentNode={currentNode}
                  cards={cards}
                  pendingPlacement={pendingPlacement}
                  hoveredUnit={hoveredUnit}
                  selectedUnitId={selectedUnitId}
                  selectedTowerId={selectedTowerId}
                  towerMaxHp={project.towerMaxHp}
                  unitDragState={unitDragState}
                  setUnitDragState={setUnitDragState}
                  setSelectedUnitId={setSelectedUnitId}
                  setSelectedTowerId={setSelectedTowerId}
                  setHoveredUnitId={setHoveredUnitId}
                  onArenaClick={handleArenaClick}
                  onUnitMove={(unitId, point) =>
                    updateUnit(
                      unitId,
                      (unit) => ({
                        ...unit,
                        x: point.x,
                        y: point.y
                      }),
                      { undo: false }
                    )
                  }
                  onUnitDelete={deleteUnit}
                />
                <CyclePanel
                  activeSide={activeSide}
                  setActiveSide={setActiveSide}
                  node={currentNode}
                  project={project}
                  playedPulse={playedPulse}
                  onPlayCard={handlePlayCard}
                  onCostDeltaChange={(costDelta) =>
                    updateCurrentNode((node) => ({
                      ...node,
                      snapshot: {
                        ...node.snapshot,
                        costDelta
                      }
                    }))
                  }
                  onSelectPlacement={(side, cardId) => {
                    setPendingPlacement({ side, cardId });
                    setNotice(ui.notice.placementSelected(ui.side[side], cards[cardId]?.name ?? cardId));
                  }}
                  onClearPlacement={() => setPendingPlacement(null)}
                />
              </div>
            </div>
          </div>
        </section>

        <aside className="inspector-panel">
          <PanelHeader title={ui.panels.editor} detail={ui.panels.snapshot} />
          <section className="inspector-section">
            <h3>{ui.panels.node}</h3>
            <label>
              {ui.panels.title}
              <input
                value={currentNode.title}
                onChange={(event) =>
                  updateCurrentNode((node) => ({
                    ...node,
                    title: event.target.value
                  }))
                }
              />
            </label>
            <TimerControl
              value={currentNode.timerSeconds}
              onChange={(timerSeconds) =>
                updateCurrentNode((node) => ({
                  ...node,
                  timerSeconds
                }))
              }
            />
            <label>
              {ui.panels.note}
              <textarea
                rows={4}
                value={currentNode.note}
                onChange={(event) =>
                  updateCurrentNode((node) => ({
                    ...node,
                    note: event.target.value
                  }))
                }
              />
            </label>
          </section>

          <CycleEditor
            title={ui.panels.cycleEditor}
            project={project}
            node={currentNode}
            onUpdateSlot={updateCycleSlot}
          />

          <UnitInspector
            unit={selectedUnit}
            cards={cardList}
            onUpdate={updateUnit}
            onDelete={deleteUnit}
          />

          <TowerInspector
            selectedTower={selectedTower}
            node={currentNode}
            towerMaxHp={project.towerMaxHp}
            isRootNode={isRootNode}
            selectedTowerId={selectedTowerId}
            onSelectTower={(towerId) => {
              setSelectedTowerId(towerId);
              setSelectedUnitId(null);
            }}
            onCurrentHpChange={updateTowerCurrentHp}
            onMaxHpChange={updateTowerMaxHp}
            onDeletePrincessTower={deletePrincessTower}
            onRestorePrincessTower={restorePrincessTower}
          />

          <ActionLog
            node={currentNode}
            cards={cards}
            onDelete={(actionId) =>
              updateCurrentNode((node) => ({
                ...node,
                actions: node.actions
                  .filter((action) => action.id !== actionId)
                  .map((action, index) => ({ ...action, order: index + 1 }))
              }))
            }
            onReorder={reorderAction}
            onUpdateNote={(actionId, note) =>
              updateCurrentNode((node) => ({
                ...node,
                actions: node.actions.map((action) =>
                  action.id === actionId ? { ...action, note } : action
                )
              }))
            }
          />
        </aside>
      </main>

      {deckSettingsOpen ? (
        <DeckSettingsModal
          project={project}
          cards={cardList}
          onClose={() => setDeckSettingsOpen(false)}
          onDeckChange={updateDeck}
          onApplyDeck={applyDeckToRootSnapshot}
        />
      ) : null}
    </div>
    {helpOpen ? <HelpModal language={language} onToggleLanguage={toggleLanguage} onClose={closeHelp} /> : null}
    </UiContext.Provider>
  );
}

function ArchivePage({
  savedProjects,
  savesDirectory,
  notice,
  loading,
  fileInputRef,
  scaledStyle,
  language,
  onToggleLanguage,
  onOpenHelp,
  onFileChange,
  onNewProject,
  onOpenProjectClick,
  onOpenSavedProject,
  onRefresh
}: {
  savedProjects: SavedProjectInfo[];
  savesDirectory: string | null;
  notice: string;
  loading: boolean;
  fileInputRef: RefObject<HTMLInputElement>;
  scaledStyle: CSSProperties;
  language: Language;
  onToggleLanguage: () => void;
  onOpenHelp: () => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onNewProject: () => void;
  onOpenProjectClick: () => Promise<void>;
  onOpenSavedProject: (path: string) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const ui = useUi();

  return (
    <main className="archive-shell" style={scaledStyle}>
      <section className="archive-card">
        <div className="archive-heading">
          <div>
            <div className="eyebrow">Clash Royale Replay Tree</div>
            <h1>{ui.archive.title}</h1>
          </div>
          <div className="archive-heading-actions">
            <button className="language-toggle" onClick={onToggleLanguage} title={ui.alternateLanguageName}>
              {language === "zh" ? "EN" : "中文"}
            </button>
            <button onClick={onOpenHelp}>{ui.top.help}</button>
            <button onClick={() => void onRefresh()} disabled={loading}>
              {ui.archive.refresh}
            </button>
          </div>
        </div>

        <div className="archive-actions">
          <button className="primary-button" onClick={onNewProject}>
            {ui.archive.newTree}
          </button>
          <button onClick={() => void onOpenProjectClick()}>{ui.archive.openOther}</button>
        </div>

        <div className="archive-notice">{notice}</div>
        {savesDirectory ? <div className="archive-path">{ui.archive.defaultDir}{savesDirectory}</div> : null}

        <div className="save-list">
          {loading ? <div className="save-empty">{ui.archive.loading}</div> : null}
          {!loading && savedProjects.length === 0 ? (
            <div className="save-empty">{ui.archive.empty}</div>
          ) : null}
          {!loading
            ? savedProjects.map((save) => (
                <button
                  className="save-item"
                  key={save.path}
                  onClick={() => void onOpenSavedProject(save.path)}
                  title={save.path}
                >
                  <span>
                    <strong>{save.name}</strong>
                    <small>{save.path}</small>
                  </span>
                  <time>{formatSavedProjectTime(save.updatedAt, language)}</time>
                </button>
              ))
            : null}
        </div>

        <input
          ref={fileInputRef}
          className="hidden-file"
          type="file"
          accept=".json,.cr-replay.json,application/json"
          onChange={onFileChange}
        />
      </section>
    </main>
  );
}

function HelpModal({
  language,
  onToggleLanguage,
  onClose
}: {
  language: Language;
  onToggleLanguage: () => void;
  onClose: () => void;
}) {
  const ui = useUi();

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="help-modal" role="dialog" aria-modal="true" aria-labelledby="help-title">
        <header className="help-modal-header">
          <div>
            <div className="eyebrow">Clash Royale Replay Tree</div>
            <h2 id="help-title">{ui.help.title}</h2>
          </div>
          <div className="help-modal-actions">
            <button className="language-toggle" onClick={onToggleLanguage} title={ui.alternateLanguageName}>
              {language === "zh" ? "EN" : "中文"}
            </button>
            <button onClick={onClose}>{ui.help.close}</button>
          </div>
        </header>
        <p className="help-lead">{ui.help.firstUse}</p>
        <div className="help-content">
          <HelpSection title={ui.help.overviewTitle} items={ui.help.overview} />
          <HelpSection title={ui.help.workflowTitle} items={ui.help.workflow} />
          <HelpSection title={ui.help.shortcutsTitle} items={ui.help.shortcuts} />
          <HelpSection title={ui.help.layoutTitle} items={ui.help.layout} />
        </div>
      </section>
    </div>
  );
}

function HelpSection({ title, items }: { title: string; items: readonly string[] }) {
  return (
    <section className="help-section">
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function GraphNodeButton({
  node,
  cards,
  language,
  isCurrent,
  onPointerDown
}: {
  node: ReplayNodeData;
  cards: Record<string, CardData>;
  language: Language;
  isCurrent: boolean;
  onPointerDown: (event: PointerEvent<HTMLButtonElement>, node: ReplayNodeData) => void;
}) {
  const ui = useUi();
  const actionSummary = formatNodeActionSummary(node, cards, language);
  const towerActionSummary = formatNodeTowerActionSummary(node, language);
  const summaryTitle = [actionSummary, towerActionSummary].filter(Boolean).join("\n");

  return (
    <button
      className={`graph-node ${isCurrent ? "is-current" : ""}`}
      style={{
        left: node.graphPosition.x,
        top: node.graphPosition.y
      }}
      onPointerDown={(event) => onPointerDown(event, node)}
    >
      <span className="graph-node-head">
        <strong>{node.title}</strong>
        {node.timerSeconds === undefined ? null : (
          <span className="graph-node-time">{formatTimer(node.timerSeconds)}</span>
        )}
      </span>
      {actionSummary ? (
        <span className="graph-node-actions" title={summaryTitle}>
          {actionSummary}
        </span>
      ) : null}
      {towerActionSummary ? (
        <span className="graph-node-actions graph-node-tower-actions" title={summaryTitle}>
          {towerActionSummary}
        </span>
      ) : null}
      <span className="graph-node-meta">
        {ui.graph.meta(node.actions.length + node.towerActions.length, node.unitMarkers.length)}
      </span>
    </button>
  );
}

function TimerControl({
  value,
  onChange
}: {
  value: number | undefined;
  onChange: (value: number | undefined) => void;
}) {
  const ui = useUi();
  const controlRef = useRef<HTMLDivElement>(null);
  const currentValue = normalizeTimerSeconds(value ?? 0);
  const minutes = Math.floor(currentValue / 60);
  const seconds = currentValue % 60;

  useEffect(() => {
    const control = controlRef.current;
    if (!control) {
      return;
    }

    function blockPageWheel(event: globalThis.WheelEvent): void {
      event.preventDefault();
      event.stopPropagation();
    }

    control.addEventListener("wheel", blockPageWheel, { passive: false });
    return () => control.removeEventListener("wheel", blockPageWheel);
  }, []);

  function adjustSeconds(delta: number): void {
    onChange(normalizeTimerSeconds(currentValue + delta));
  }

  function adjustMinutes(delta: number): void {
    onChange(normalizeTimerSeconds(currentValue + delta * 60));
  }

  return (
    <div ref={controlRef} className="timer-control">
      <div className="timer-control-header">
        <span>{ui.timer.title}</span>
        <strong>{value === undefined ? ui.timer.hidden : formatTimer(value)}</strong>
      </div>
      <div className={`timer-wheel-picker ${value === undefined ? "is-unset" : ""}`}>
        <TimerWheelColumn
          label={ui.timer.minute}
          value={minutes}
          max={3}
          canStepDown={currentValue > 0}
          canStepUp={currentValue < MAX_TIMER_SECONDS}
          onStep={adjustMinutes}
        />
        <span className="timer-separator">:</span>
        <TimerWheelColumn
          label={ui.timer.second}
          value={seconds}
          max={59}
          canStepDown={currentValue > 0}
          canStepUp={currentValue < MAX_TIMER_SECONDS}
          wrap
          pad
          onStep={adjustSeconds}
        />
      </div>
      <div className="timer-control-row">
        <button type="button" onClick={() => onChange(0)} disabled={value !== undefined}>
          {ui.timer.showZero}
        </button>
        <button type="button" onClick={() => onChange(undefined)} disabled={value === undefined}>
          {ui.timer.clear}
        </button>
      </div>
      <span>{ui.timer.hint}</span>
    </div>
  );
}

function TimerWheelColumn({
  label,
  value,
  max,
  canStepDown,
  canStepUp,
  wrap = false,
  pad = false,
  onStep
}: {
  label: string;
  value: number;
  max: number;
  canStepDown: boolean;
  canStepUp: boolean;
  wrap?: boolean;
  pad?: boolean;
  onStep: (delta: number) => void;
}) {
  const ui = useUi();
  const wheelRef = useRef<HTMLDivElement>(null);
  const display = (nextValue: number) =>
    formatWheelValue(wrap ? wrapWheelValue(nextValue, max) : clamp(nextValue, 0, max), pad);

  useEffect(() => {
    const wheel = wheelRef.current;
    if (!wheel) {
      return;
    }

    function handleNativeWheel(event: globalThis.WheelEvent): void {
      event.preventDefault();
      event.stopPropagation();
      if (event.deltaY > 0 && canStepUp) {
        onStep(1);
      }
      if (event.deltaY < 0 && canStepDown) {
        onStep(-1);
      }
    }

    wheel.addEventListener("wheel", handleNativeWheel, { passive: false });
    return () => wheel.removeEventListener("wheel", handleNativeWheel);
  }, [canStepDown, canStepUp, onStep]);

  return (
    <div ref={wheelRef} className="timer-wheel">
      <button type="button" onClick={() => onStep(-1)} disabled={!canStepDown} aria-label={ui.timer.decrease(label)}>
        ▲
      </button>
      <div className="timer-wheel-window" aria-label={label}>
        <span>{display(value - 1)}</span>
        <strong>{display(value)}</strong>
        <span>{display(value + 1)}</span>
      </div>
      <button type="button" onClick={() => onStep(1)} disabled={!canStepUp} aria-label={ui.timer.increase(label)}>
        ▼
      </button>
      <em>{label}</em>
    </div>
  );
}

function PanelHeader({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="panel-header">
      <h2>{title}</h2>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}

function ArenaBoard({
  currentNode,
  cards,
  pendingPlacement,
  hoveredUnit,
  selectedUnitId,
  selectedTowerId,
  towerMaxHp,
  unitDragState,
  setUnitDragState,
  setSelectedUnitId,
  setSelectedTowerId,
  setHoveredUnitId,
  onArenaClick,
  onUnitMove,
  onUnitDelete
}: {
  currentNode: ReplayNodeData;
  cards: Record<string, CardData>;
  pendingPlacement: PendingPlacement;
  hoveredUnit: UnitMarkerData | null;
  selectedUnitId: string | null;
  selectedTowerId: string | null;
  towerMaxHp: Record<string, number>;
  unitDragState: UnitDragState;
  setUnitDragState: (state: UnitDragState) => void;
  setSelectedUnitId: (unitId: string | null) => void;
  setSelectedTowerId: (towerId: string | null) => void;
  setHoveredUnitId: (unitId: string | null) => void;
  onArenaClick: (point: Point) => void;
  onUnitMove: (unitId: string, point: Point) => void;
  onUnitDelete: (unitId: string) => void;
}) {
  const ui = useUi();
  function eventToGridPoint(event: PointerEvent<HTMLDivElement> | MouseEvent<HTMLDivElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: clamp(roundToTenth(((event.clientX - rect.left) / rect.width) * ARENA_GRID.columns), 0, ARENA_GRID.columns),
      y: clamp(roundToTenth(((event.clientY - rect.top) / rect.height) * ARENA_GRID.rows), 0, ARENA_GRID.rows)
    };
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>): void {
    if (!unitDragState || unitDragState.pointerId !== event.pointerId) {
      return;
    }

    onUnitMove(unitDragState.unitId, eventToGridPoint(event));
  }

  return (
    <div className="arena-wrap">
      <div className="arena-status">
        <span>{ui.arena.grid}</span>
        {pendingPlacement ? (
          <strong>
            {ui.arena.placing(ui.side[pendingPlacement.side], cards[pendingPlacement.cardId]?.name ?? pendingPlacement.cardId)}
          </strong>
        ) : (
          <span>{ui.arena.placementHint}</span>
        )}
      </div>
      <div className="arena-board-shell">
        <div
          className="arena-board"
          onClick={(event) => onArenaClick(eventToGridPoint(event))}
          onPointerMove={handlePointerMove}
          onPointerUp={(event) => {
            if (unitDragState?.pointerId === event.pointerId) {
              setUnitDragState(null);
            }
          }}
          onPointerCancel={() => setUnitDragState(null)}
        >
          <div className="river-line" />
          {ARENA_ROADS.map((road) => (
            <div
              id={road.id}
              key={road.id}
              className={`arena-road ${road.side}-road`}
              style={{
                left: `${(road.x / ARENA_GRID.columns) * 100}%`,
                top: `${(road.y / ARENA_GRID.rows) * 100}%`,
                width: `${(road.width / ARENA_GRID.columns) * 100}%`,
                height: `${(road.height / ARENA_GRID.rows) * 100}%`
              }}
              aria-hidden="true"
            />
          ))}
          {ARENA_BRIDGES.map((bridge) => (
            <div
              id={bridge.id}
              key={bridge.id}
              className="bridge"
              style={{
                left: `${(bridge.x / ARENA_GRID.columns) * 100}%`,
                top: `${(bridge.y / ARENA_GRID.rows) * 100}%`,
                width: `${(bridge.width / ARENA_GRID.columns) * 100}%`,
                height: `${(bridge.height / ARENA_GRID.rows) * 100}%`
              }}
              aria-hidden="true"
            />
          ))}
          <div className="arena-lane-label enemy-lane">{ui.arena.enemyHalf}</div>
          <div className="arena-lane-label my-lane">{ui.arena.myHalf}</div>
          {ARENA_TOWERS.map((tower) => {
            const state = getTowerState(currentNode, tower.id, towerMaxHp);
            if (tower.type === "princess" && state.destroyed) {
              return null;
            }
            const maxHp = getTowerMaxHpFromRecord(towerMaxHp, tower.id);
            const hpPercent = getTowerHealthPercent(state, maxHp);
            const towerName = formatTowerName(tower.id, ui);
            return (
              <button
                type="button"
                id={tower.id}
                key={tower.id}
                className={`tower-marker ${tower.side}-tower ${tower.type}-tower ${
                  tower.id === selectedTowerId ? "is-selected" : ""
                }`}
                style={{
                  left: `${(tower.x / ARENA_GRID.columns) * 100}%`,
                  top: `${(tower.y / ARENA_GRID.rows) * 100}%`,
                  width: `${(tower.size / ARENA_GRID.columns) * 100}%`,
                  height: `${(tower.size / ARENA_GRID.rows) * 100}%`
                }}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedTowerId(tower.id);
                  setSelectedUnitId(null);
                }}
                aria-label={`${towerName} ${state.hp}/${maxHp}`}
                title={`${towerName} ${state.hp}/${maxHp}`}
              >
                <span className="tower-health-bar" aria-hidden="true">
                  <span style={{ width: `${hpPercent}%` }} />
                </span>
                <span className="tower-health-label">{state.hp}</span>
                <span className="tower-crown">{tower.type === "king" ? "K" : "P"}</span>
                <span className="tower-label">{tower.type === "king" ? ui.tower.king : ui.tower.princess}</span>
              </button>
            );
          })}
          {currentNode.unitMarkers.map((unit) => {
            const left = `${(unit.x / ARENA_GRID.columns) * 100}%`;
            const top = `${(unit.y / ARENA_GRID.rows) * 100}%`;
            const card = cards[unit.cardId];
            const healthPercent = getUnitHealthPercent(unit);
            return (
              <button
                key={unit.id}
                className={`unit-marker ${unit.side === "my" ? "my-unit" : "enemy-unit"} ${
                  unit.id === selectedUnitId ? "is-selected" : ""
                }`}
                style={{ left, top }}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedUnitId(unit.id);
                  setSelectedTowerId(null);
                }}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  setSelectedUnitId(unit.id);
                  setSelectedTowerId(null);
                  setUnitDragState({ pointerId: event.pointerId, unitId: unit.id });
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onUnitDelete(unit.id);
                }}
                onMouseEnter={() => setHoveredUnitId(unit.id)}
                onMouseLeave={() => setHoveredUnitId(null)}
                title={unit.displayName}
              >
                {card?.imagePath ? (
                  <img className="unit-card-image" src={card.imagePath} alt="" draggable={false} />
                ) : (
                  <span>{card?.name.slice(0, 2) ?? unit.displayName.slice(0, 2)}</span>
                )}
                {healthPercent === null ? null : (
                  <span className="unit-health-bar" aria-hidden="true">
                    <span style={{ width: `${healthPercent}%` }} />
                  </span>
                )}
              </button>
            );
          })}
          {hoveredUnit ? (
            <div
              className="unit-tooltip"
              style={{
                left: `${(hoveredUnit.x / ARENA_GRID.columns) * 100}%`,
                top: `${(hoveredUnit.y / ARENA_GRID.rows) * 100}%`
              }}
            >
              <strong>{hoveredUnit.displayName}</strong>
              <span>{ui.arena.owner}{ui.side[hoveredUnit.side]}</span>
              <span>
                {ui.arena.health}
                {formatUnitHealth(hoveredUnit, ui)}
              </span>
              <span>{ui.arena.status}{hoveredUnit.statusTags.join("、") || ui.arena.none}</span>
              {hoveredUnit.note ? <span>{hoveredUnit.note}</span> : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function CyclePanel({
  activeSide,
  setActiveSide,
  node,
  project,
  playedPulse,
  onPlayCard,
  onCostDeltaChange,
  onSelectPlacement,
  onClearPlacement
}: {
  activeSide: Side;
  setActiveSide: (side: Side) => void;
  node: ReplayNodeData;
  project: ReplayProject;
  playedPulse: PlayedPulse;
  onPlayCard: (side: Side, cardId: string) => void;
  onCostDeltaChange: (costDelta: number) => void;
  onSelectPlacement: (side: Side, cardId: string) => void;
  onClearPlacement: () => void;
}) {
  const ui = useUi();
  function handlePlayCard(side: Side, cardId: string): void {
    setActiveSide(side);
    onPlayCard(side, cardId);
  }

  function handleSelectPlacement(side: Side, cardId: string): void {
    setActiveSide(side);
    onSelectPlacement(side, cardId);
  }

  return (
    <div className="cycle-panel">
      <div className="cycle-panel-toolbar">
        <div>
          <strong>{ui.cycle.title}</strong>
          <span>{ui.cycle.subtitle}</span>
        </div>
        <button className="ghost-button" onClick={onClearPlacement}>
          {ui.cycle.exitPlacement}
        </button>
      </div>
      <div className="cycle-side-list">
        <CycleSideSection
          side="enemy"
          state={node.snapshot.enemyState}
          activeSide={activeSide}
          setActiveSide={setActiveSide}
          project={project}
          playedPulse={playedPulse}
          onPlayCard={handlePlayCard}
          onSelectPlacement={handleSelectPlacement}
        />
        <CostDeltaControl
          nodeId={node.id}
          value={node.snapshot.costDelta}
          onChange={onCostDeltaChange}
        />
        <CycleSideSection
          side="my"
          state={node.snapshot.myState}
          activeSide={activeSide}
          setActiveSide={setActiveSide}
          project={project}
          playedPulse={playedPulse}
          onPlayCard={handlePlayCard}
          onSelectPlacement={handleSelectPlacement}
        />
      </div>
    </div>
  );
}

function CycleSideSection({
  side,
  state,
  activeSide,
  setActiveSide,
  project,
  playedPulse,
  onPlayCard,
  onSelectPlacement
}: {
  side: Side;
  state: PlayerCycleState;
  activeSide: Side;
  setActiveSide: (side: Side) => void;
  project: ReplayProject;
  playedPulse: PlayedPulse;
  onPlayCard: (side: Side, cardId: string) => void;
  onSelectPlacement: (side: Side, cardId: string) => void;
}) {
  const ui = useUi();
  return (
    <section className={`cycle-side-card ${side}-cycle ${activeSide === side ? "is-active" : ""}`}>
      <button className="cycle-side-title" onClick={() => setActiveSide(side)}>
        <strong>{ui.side[side]}</strong>
        <span>{activeSide === side ? ui.cycle.active : ui.cycle.setActive}</span>
      </button>
      <CycleCards
        title={ui.cycle.hand}
        hint={ui.cycle.handHint}
        cardIds={state.hand}
        project={project}
        side={side}
        playedPulse={playedPulse}
        onPlayCard={onPlayCard}
        onSelectPlacement={onSelectPlacement}
        canPlay
      />
      <CycleCards
        title={ui.cycle.queue}
        hint={ui.cycle.queueHint}
        cardIds={state.drawQueue}
        project={project}
        side={side}
        playedPulse={playedPulse}
        onPlayCard={onPlayCard}
        onSelectPlacement={onSelectPlacement}
      />
    </section>
  );
}

function CostDeltaControl({
  nodeId,
  value,
  onChange
}: {
  nodeId: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const ui = useUi();
  const [draft, setDraft] = useState(formatCostDelta(value));
  const [focused, setFocused] = useState(false);
  const tone = value > 0 ? "positive" : value < 0 ? "negative" : "neutral";

  useEffect(() => {
    setDraft(formatCostDelta(value));
  }, [nodeId]);

  useEffect(() => {
    if (!focused) {
      setDraft(formatCostDelta(value));
    }
  }, [focused, value]);

  function commitDraft(nextDraft = draft): void {
    const nextValue = parseCostDelta(nextDraft);
    if (nextValue === null) {
      setDraft(formatCostDelta(value));
      return;
    }

    setDraft(formatCostDelta(nextValue));
  }

  return (
    <div className={`cost-delta-control ${tone}`}>
      <label>
        <span>{ui.cycle.costDelta}</span>
        <input
          type="text"
          inputMode="decimal"
          value={draft}
          onFocus={() => setFocused(true)}
          onChange={(event) => {
            const nextDraft = event.target.value.replace("−", "-");
            if (!isCostDeltaDraft(nextDraft)) {
              return;
            }
            setDraft(nextDraft);
            const nextValue = parseCostDelta(nextDraft);
            if (nextValue !== null) {
              const rounded = roundCostDelta(nextValue);
              if (rounded !== value) {
                onChange(rounded);
              }
            }
          }}
          onBlur={() => {
            setFocused(false);
            commitDraft();
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.currentTarget.blur();
            }
          }}
          aria-label={ui.cycle.costAria}
        />
      </label>
      <strong>{value > 0 ? `+${formatCostDelta(value)}` : formatCostDelta(value)}</strong>
      <span>{ui.cycle.costHint}</span>
    </div>
  );
}

function CycleCards({
  title,
  hint,
  cardIds,
  project,
  side,
  playedPulse,
  canPlay,
  onPlayCard,
  onSelectPlacement
}: {
  title: string;
  hint: string;
  cardIds: string[];
  project: ReplayProject;
  side: Side;
  playedPulse: PlayedPulse;
  canPlay?: boolean;
  onPlayCard: (side: Side, cardId: string) => void;
  onSelectPlacement: (side: Side, cardId: string) => void;
}) {
  return (
    <section className="cycle-row">
      <div className="cycle-row-heading">
        <h3>{title}</h3>
        <span>{hint}</span>
      </div>
      <div className="card-strip">
        {cardIds.map((cardId, index) => {
          const card = project.cards[cardId];
          const cardName = card?.name ?? cardId;
          return (
            <button
              key={`${cardId}_${index}`}
              className={`card-tile ${side}-card ${
                playedPulse?.side === side && playedPulse.cardId === cardId ? "is-played-pulse" : ""
              }`}
              onClick={() => onSelectPlacement(side, cardId)}
              onDoubleClick={() => canPlay && onPlayCard(side, cardId)}
              title={cardName}
            >
              <span className="card-artwork-frame">
                {card?.imagePath ? (
                  <img className="card-artwork" src={card.imagePath} alt="" draggable={false} loading="lazy" />
                ) : (
                  <span className="card-artwork-placeholder">{cardName.slice(0, 2)}</span>
                )}
                <span className="card-cost-badge">
                  <img src="/elixir.png" alt="" draggable={false} />
                  <span>{card?.cost ?? "?"}</span>
                </span>
              </span>
              <strong>{cardName}</strong>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function DeckSettingsModal({
  project,
  cards,
  onClose,
  onDeckChange,
  onApplyDeck
}: {
  project: ReplayProject;
  cards: CardData[];
  onClose: () => void;
  onDeckChange: (side: Side, index: number, cardId: string) => void;
  onApplyDeck: (side: Side) => void;
}) {
  const ui = useUi();
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="deck-modal" role="dialog" aria-modal="true" aria-labelledby="deck-settings-title">
        <header className="deck-modal-header">
          <div>
            <div className="eyebrow">{ui.deck.eyebrow}</div>
            <h2 id="deck-settings-title">{ui.deck.title}</h2>
          </div>
          <button onClick={onClose}>{ui.deck.close}</button>
        </header>
        <p className="deck-modal-note">
          {ui.deck.note}
        </p>
        <div className="deck-modal-content">
          <DeckEditor
            title={ui.deck.myDeck}
            side="my"
            deck={project.myDeck}
            cards={cards}
            onDeckChange={onDeckChange}
            onApplyDeck={onApplyDeck}
          />
          <DeckEditor
            title={ui.deck.enemyDeck}
            side="enemy"
            deck={project.enemyDeck}
            cards={cards}
            onDeckChange={onDeckChange}
            onApplyDeck={onApplyDeck}
          />
        </div>
        <footer className="deck-modal-footer">
          <button
            className="primary-button"
            onClick={() => {
              onApplyDeck("my");
              onApplyDeck("enemy");
              onClose();
            }}
          >
            {ui.deck.applyBoth}
          </button>
        </footer>
      </section>
    </div>
  );
}

function DeckEditor({
  title,
  side,
  deck,
  cards,
  onDeckChange,
  onApplyDeck
}: {
  title: string;
  side: Side;
  deck: string[];
  cards: CardData[];
  onDeckChange: (side: Side, index: number, cardId: string) => void;
  onApplyDeck: (side: Side) => void;
}) {
  const ui = useUi();
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      <div className="deck-grid">
        {deck.map((cardId, index) => (
          <label key={`${side}_${index}`}>
            {index < 4 ? ui.deck.handSlot(index + 1) : ui.deck.queueSlot(index - 3)}
            <select value={cardId} onChange={(event) => onDeckChange(side, index, event.target.value)}>
              {cards.map((card) => (
                <option key={card.id} value={card.id}>
                  {card.name}（{card.cost}）
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <button onClick={() => onApplyDeck(side)}>{ui.deck.applyRoot}</button>
    </section>
  );
}

function CycleEditor({
  title,
  project,
  node,
  onUpdateSlot
}: {
  title: string;
  project: ReplayProject;
  node: ReplayNodeData;
  onUpdateSlot: (side: Side, zone: "hand" | "drawQueue", index: number, cardId: string) => void;
}) {
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      <EditableCycle
        side="my"
        state={node.snapshot.myState}
        deck={project.myDeck}
        cards={project.cards}
        onUpdateSlot={onUpdateSlot}
      />
      <EditableCycle
        side="enemy"
        state={node.snapshot.enemyState}
        deck={project.enemyDeck}
        cards={project.cards}
        onUpdateSlot={onUpdateSlot}
      />
    </section>
  );
}

function EditableCycle({
  side,
  state,
  deck,
  cards,
  onUpdateSlot
}: {
  side: Side;
  state: PlayerCycleState;
  deck: string[];
  cards: Record<string, CardData>;
  onUpdateSlot: (side: Side, zone: "hand" | "drawQueue", index: number, cardId: string) => void;
}) {
  const ui = useUi();
  return (
    <div className="editable-cycle">
      <strong>{ui.side[side]}</strong>
      {(["hand", "drawQueue"] as const).map((zone) => (
        <div className="mini-select-row" key={`${side}_${zone}`}>
          <span>{zone === "hand" ? ui.cycle.hand : ui.cycle.queue}</span>
          {state[zone].map((cardId, index) => (
            <select
              key={`${zone}_${index}`}
              value={cardId}
              onChange={(event) => onUpdateSlot(side, zone, index, event.target.value)}
            >
              {deck.map((deckCardId) => (
                <option key={deckCardId} value={deckCardId}>
                  {cards[deckCardId]?.name ?? deckCardId}
                </option>
              ))}
            </select>
          ))}
        </div>
      ))}
    </div>
  );
}

function UnitInspector({
  unit,
  cards,
  onUpdate,
  onDelete
}: {
  unit: UnitMarkerData | null;
  cards: CardData[];
  onUpdate: (unitId: string, updater: (unit: UnitMarkerData) => UnitMarkerData) => void;
  onDelete: (unitId: string) => void;
}) {
  const ui = useUi();

  if (!unit) {
    return (
      <section className="inspector-section muted-section">
        <h3>{ui.unit.title}</h3>
        <p>{ui.unit.empty}</p>
      </section>
    );
  }

  return (
    <section className="inspector-section">
      <h3>{ui.unit.title}</h3>
      <label>
        {ui.unit.side}
        <select
          value={unit.side}
          onChange={(event) =>
            onUpdate(unit.id, (previous) => ({
              ...previous,
              side: event.target.value as Side
            }))
          }
        >
          <option value="my">{ui.side.my}</option>
          <option value="enemy">{ui.side.enemy}</option>
        </select>
      </label>
      <label>
        {ui.unit.card}
        <select
          value={unit.cardId}
          onChange={(event) => {
            const card = cards.find((item) => item.id === event.target.value);
            onUpdate(unit.id, (previous) => ({
              ...previous,
              cardId: event.target.value,
              displayName: card?.name ?? previous.displayName
            }));
          }}
        >
          {cards.map((card) => (
            <option key={card.id} value={card.id}>
              {card.name}
            </option>
          ))}
        </select>
      </label>
      <div className="field-row">
        <label>
          {ui.unit.x}
          <input
            type="number"
            step="0.1"
            value={unit.x}
            onChange={(event) =>
              onUpdate(unit.id, (previous) => ({
                ...previous,
                x: clamp(Number(event.target.value || 0), 0, ARENA_GRID.columns)
              }))
            }
          />
        </label>
        <label>
          {ui.unit.y}
          <input
            type="number"
            step="0.1"
            value={unit.y}
            onChange={(event) =>
              onUpdate(unit.id, (previous) => ({
                ...previous,
                y: clamp(Number(event.target.value || 0), 0, ARENA_GRID.rows)
              }))
            }
          />
        </label>
      </div>
      <div className="field-row">
        <label>
          {ui.unit.hp}
          <input
            type="number"
            value={unit.hp ?? ""}
            onChange={(event) =>
              onUpdate(unit.id, (previous) => ({
                ...previous,
                hp: toOptionalNumber(event.target.value)
              }))
            }
          />
        </label>
        <label>
          {ui.unit.maxHp}
          <input
            type="number"
            value={unit.maxHp ?? ""}
            onChange={(event) =>
              onUpdate(unit.id, (previous) => ({
                ...previous,
                maxHp: toOptionalNumber(event.target.value)
              }))
            }
          />
        </label>
      </div>
      <label>
        {ui.unit.tags}
        <input
          value={unit.statusTags.join("，")}
          onChange={(event) =>
            onUpdate(unit.id, (previous) => ({
              ...previous,
              statusTags: splitTags(event.target.value)
            }))
          }
          placeholder={ui.unit.tagsPlaceholder}
        />
      </label>
      <label>
        {ui.unit.note}
        <textarea
          rows={3}
          value={unit.note}
          onChange={(event) =>
            onUpdate(unit.id, (previous) => ({
              ...previous,
              note: event.target.value
            }))
          }
        />
      </label>
      <button className="danger-button" onClick={() => onDelete(unit.id)}>
        {ui.unit.delete}
      </button>
    </section>
  );
}

function TowerInspector({
  selectedTower,
  node,
  towerMaxHp,
  isRootNode,
  selectedTowerId,
  onSelectTower,
  onCurrentHpChange,
  onMaxHpChange,
  onDeletePrincessTower,
  onRestorePrincessTower
}: {
  selectedTower: TowerMetadata | null;
  node: ReplayNodeData;
  towerMaxHp: Record<string, number>;
  isRootNode: boolean;
  selectedTowerId: string | null;
  onSelectTower: (towerId: string) => void;
  onCurrentHpChange: (towerId: string, hp: number) => void;
  onMaxHpChange: (towerId: string, maxHp: number) => void;
  onDeletePrincessTower: (towerId: string) => void;
  onRestorePrincessTower: (towerId: string) => void;
}) {
  const ui = useUi();
  const selectedState = selectedTower
    ? getTowerState(node, selectedTower.id, towerMaxHp)
    : null;
  const selectedMaxHp = selectedTower ? getTowerMaxHpFromRecord(towerMaxHp, selectedTower.id) : 0;

  return (
    <section className="inspector-section">
      <h3>{ui.towerEditor.title}</h3>
      <p className="hint-text">{ui.towerEditor.hint}</p>
      <div className="tower-select-grid">
        {ARENA_TOWERS.map((tower) => {
          const state = getTowerState(node, tower.id, towerMaxHp);
          const maxHp = getTowerMaxHpFromRecord(towerMaxHp, tower.id);
          return (
            <button
              type="button"
              key={tower.id}
              className={`tower-select-button ${tower.id === selectedTowerId ? "is-selected" : ""} ${
                state.destroyed ? "is-destroyed" : ""
              }`}
              onClick={() => onSelectTower(tower.id)}
            >
              <strong>{formatTowerName(tower.id, ui)}</strong>
              <span>{state.destroyed ? ui.towerEditor.deleted : `${state.hp}/${maxHp}`}</span>
            </button>
          );
        })}
      </div>

      {!selectedTower || !selectedState ? (
        <p className="empty-text">{ui.towerEditor.empty}</p>
      ) : (
        <div className="tower-editor">
          <label>
            {ui.towerEditor.hp}
            <input
              type="number"
              min={0}
              max={selectedMaxHp}
              value={selectedState.hp}
              onChange={(event) => onCurrentHpChange(selectedTower.id, Number(event.target.value || 0))}
            />
          </label>
          <label>
            {ui.towerEditor.maxHp}
            <input
              type="number"
              min={1}
              value={selectedMaxHp}
              disabled={!isRootNode}
              onChange={(event) => onMaxHpChange(selectedTower.id, Number(event.target.value || 1))}
            />
          </label>
          {!isRootNode ? (
            <p className="hint-text">{ui.towerEditor.maxRootOnly}</p>
          ) : null}
          {selectedTower.type === "princess" ? (
            selectedState.destroyed ? (
              <button onClick={() => onRestorePrincessTower(selectedTower.id)}>
                {ui.towerEditor.restorePrincess}
              </button>
            ) : (
              <button className="danger-button" onClick={() => onDeletePrincessTower(selectedTower.id)}>
                {ui.towerEditor.deletePrincess}
              </button>
            )
          ) : null}
        </div>
      )}
    </section>
  );
}

function ActionLog({
  node,
  cards,
  onDelete,
  onReorder,
  onUpdateNote
}: {
  node: ReplayNodeData;
  cards: Record<string, CardData>;
  onDelete: (actionId: string) => void;
  onReorder: (actionId: string, targetActionId: string, placement: "before" | "after") => void;
  onUpdateNote: (actionId: string, note: string) => void;
}) {
  const ui = useUi();
  const actionListRef = useRef<HTMLDivElement>(null);
  const [draggedActionId, setDraggedActionId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{
    actionId: string;
    placement: "before" | "after";
  } | null>(null);
  const sortedActions = [...node.actions].sort((a, b) => a.order - b.order);

  useEffect(() => {
    if (!draggedActionId) {
      return;
    }

    const activeActionId = draggedActionId;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";

    function updateDropTarget(clientY: number): void {
      setDropTarget(getActionDropTarget(actionListRef.current, activeActionId, clientY));
    }

    function handlePointerMove(event: globalThis.PointerEvent): void {
      event.preventDefault();
      updateDropTarget(event.clientY);
    }

    function finishDrag(event: globalThis.PointerEvent): void {
      event.preventDefault();
      const target = getActionDropTarget(actionListRef.current, activeActionId, event.clientY);
      if (target) {
        onReorder(activeActionId, target.actionId, target.placement);
      }
      setDraggedActionId(null);
      setDropTarget(null);
    }

    window.addEventListener("pointermove", handlePointerMove, { passive: false });
    window.addEventListener("pointerup", finishDrag, { passive: false });
    window.addEventListener("pointercancel", finishDrag, { passive: false });

    return () => {
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", finishDrag);
    };
  }, [draggedActionId, onReorder]);

  return (
    <section className="inspector-section">
      <h3>{ui.action.title}</h3>
      <p className="hint-text">{ui.action.hint}</p>
      {node.actions.length === 0 && node.towerActions.length === 0 ? (
        <p className="empty-text">{ui.action.empty}</p>
      ) : null}
      <div className="action-list" ref={actionListRef}>
        {sortedActions.map((action) => (
          <article
            className={`action-item ${draggedActionId === action.id ? "is-dragging" : ""} ${
              dropTarget?.actionId === action.id ? `is-drop-${dropTarget.placement}` : ""
            }`}
            data-action-id={action.id}
            key={action.id}
          >
              <div className="action-item-header">
                <strong>
                  {action.order}. {ui.side[action.side]} {cards[action.cardId]?.name ?? action.cardId}
                </strong>
                <button
                  type="button"
                  className="action-drag-handle"
                  aria-label={ui.action.dragAria(cards[action.cardId]?.name ?? action.cardId)}
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setDraggedActionId(action.id);
                    setDropTarget(null);
                  }}
                >
                  {ui.action.drag}
                </button>
              </div>
              <textarea
                rows={2}
                value={action.note ?? ""}
                placeholder={ui.action.notePlaceholder}
                onChange={(event) => onUpdateNote(action.id, event.target.value)}
              />
              <div className="action-buttons">
                <button className="danger-button" onClick={() => onDelete(action.id)}>
                  {ui.action.delete}
                </button>
              </div>
            </article>
        ))}
        {[...node.towerActions]
          .sort((a, b) => a.order - b.order)
          .map((action) => (
            <article className="action-item tower-action-item" key={action.id}>
              <div>
                <strong>{formatTowerAction(action, ui)}</strong>
                <span>{ui.action.towerAdjust}</span>
              </div>
              <span className="hint-text">{ui.action.hpAfter(action.hpAfter)}</span>
            </article>
          ))}
      </div>
    </section>
  );
}

function getActionDropTarget(
  listElement: HTMLDivElement | null,
  draggedActionId: string,
  clientY: number
): { actionId: string; placement: "before" | "after" } | null {
  if (!listElement) {
    return null;
  }

  const actionItems = Array.from(listElement.querySelectorAll<HTMLElement>("[data-action-id]")).filter(
    (item) => item.dataset.actionId && item.dataset.actionId !== draggedActionId
  );

  if (actionItems.length === 0) {
    return null;
  }

  for (const item of actionItems) {
    const rect = item.getBoundingClientRect();
    if (clientY >= rect.top && clientY <= rect.bottom) {
      return {
        actionId: item.dataset.actionId!,
        placement: clientY > rect.top + rect.height / 2 ? "after" : "before"
      };
    }
  }

  const firstItem = actionItems[0];
  const lastItem = actionItems[actionItems.length - 1];
  if (clientY < firstItem.getBoundingClientRect().top) {
    return { actionId: firstItem.dataset.actionId!, placement: "before" };
  }
  if (clientY > lastItem.getBoundingClientRect().bottom) {
    return { actionId: lastItem.dataset.actionId!, placement: "after" };
  }

  return null;
}

function collectSubtreeIds(project: ReplayProject, nodeId: string): string[] {
  const node = project.nodes[nodeId];
  if (!node) {
    return [];
  }

  return [nodeId, ...node.childrenIds.flatMap((childId) => collectSubtreeIds(project, childId))];
}

function roundToTenth(value: number): number {
  return Math.round(value * 10) / 10;
}

function normalizeTimerSeconds(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }

  return Math.round(clamp(value, 0, MAX_TIMER_SECONDS));
}

function wrapWheelValue(value: number, max: number): number {
  const range = max + 1;
  return ((value % range) + range) % range;
}

function formatWheelValue(value: number, pad: boolean): string {
  return pad ? String(value).padStart(2, "0") : String(value);
}

function formatTimer(seconds: number): string {
  const normalized = normalizeTimerSeconds(seconds);
  const minutes = Math.floor(normalized / 60);
  const restSeconds = normalized % 60;
  return `${minutes}:${String(restSeconds).padStart(2, "0")}`;
}

function formatSavedProjectTime(timestamp: number | undefined, language: Language): string {
  if (!timestamp) {
    return UI_TEXT[language].archive.unknownTime;
  }

  return new Date(timestamp).toLocaleString(language === "zh" ? "zh-CN" : "en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function getUnitHealthPercent(unit: UnitMarkerData): number | null {
  if (unit.hp === undefined && unit.maxHp === undefined) {
    return null;
  }

  if (unit.hp !== undefined && unit.maxHp !== undefined && unit.maxHp > 0) {
    return clamp((unit.hp / unit.maxHp) * 100, 0, 100);
  }

  return 100;
}

function formatUnitHealth(unit: UnitMarkerData, ui: UiText): string {
  if (unit.hp === undefined && unit.maxHp === undefined) {
    return ui.arena.unset;
  }

  return `${unit.hp ?? "?"}/${unit.maxHp ?? "?"}`;
}

function getTowerMetadata(towerId: string): TowerMetadata | null {
  return ARENA_TOWERS.find((tower) => tower.id === towerId) ?? null;
}

function getTowerMaxHp(project: ReplayProject, towerId: string): number {
  return getTowerMaxHpFromRecord(project.towerMaxHp, towerId);
}

function getTowerMaxHpFromRecord(towerMaxHp: Record<string, number>, towerId: string): number {
  const value = towerMaxHp[towerId];
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 1;
}

function getTowerState(
  node: ReplayNodeData,
  towerId: string,
  towerMaxHp: Record<string, number>
): TowerStateData {
  const maxHp = getTowerMaxHpFromRecord(towerMaxHp, towerId);
  const state = node.towerStates?.[towerId];
  return {
    hp: Math.round(clamp(state?.hp ?? maxHp, 0, maxHp)),
    destroyed: Boolean(state?.destroyed)
  };
}

function getTowerHealthPercent(state: TowerStateData, maxHp: number): number {
  return clamp((state.hp / Math.max(1, maxHp)) * 100, 0, 100);
}

function formatTowerName(towerId: string, languageOrUi: Language | UiText): string {
  const ui = typeof languageOrUi === "string" ? UI_TEXT[languageOrUi] : languageOrUi;
  switch (towerId) {
    case "enemy-king":
      return ui.tower.enemyKing;
    case "enemy-left-princess":
      return ui.tower.enemyLeft;
    case "enemy-right-princess":
      return ui.tower.enemyRight;
    case "my-left-princess":
      return ui.tower.myLeft;
    case "my-right-princess":
      return ui.tower.myRight;
    case "my-king":
      return ui.tower.myKing;
    default:
      return towerId;
  }
}

function formatTowerAction(action: TowerActionRecord, languageOrUi: Language | UiText): string {
  return `${formatTowerName(action.towerId, languageOrUi)}${formatSignedDelta(action.delta)}`;
}

function formatSignedDelta(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

function formatNodeActionSummary(
  node: ReplayNodeData,
  cards: Record<string, CardData>,
  language: Language
): string {
  const ui = UI_TEXT[language];
  return [...node.actions]
    .sort((a, b) => a.order - b.order)
    .map((action) => `${ui.side[action.side]}${cards[action.cardId]?.name ?? action.cardId}`)
    .join(language === "zh" ? "，" : ", ");
}

function formatNodeTowerActionSummary(node: ReplayNodeData, language: Language): string {
  return [...(node.towerActions ?? [])]
    .sort((a, b) => a.order - b.order)
    .map((action) => formatTowerAction(action, language))
    .join(language === "zh" ? "，" : ", ");
}

function estimateGraphNodeHeight(node: ReplayNodeData, cards: Record<string, CardData>, language: Language): number {
  const summary = formatNodeActionSummary(node, cards, language);
  const towerSummary = formatNodeTowerActionSummary(node, language);
  const summaryLines = summary ? Math.ceil(summary.length / 15) : 0;
  const towerSummaryLines = towerSummary ? Math.ceil(towerSummary.length / 15) : 0;
  return Math.max(86, 62 + (summaryLines + towerSummaryLines) * 18);
}

function formatCostDelta(value: number): string {
  const rounded = roundCostDelta(value);
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

function isCostDeltaDraft(value: string): boolean {
  return /^-?(?:\d+|\d*\.\d?)?$/.test(value);
}

function parseCostDelta(value: string): number | null {
  if (value === "" || value === "-" || value === "." || value === "-.") {
    return null;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function toOptionalNumber(value: string): number | undefined {
  if (value.trim() === "") {
    return undefined;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function splitTags(value: string): string[] {
  return value
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default App;
