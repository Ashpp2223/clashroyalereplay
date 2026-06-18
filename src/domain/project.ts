import { DEFAULT_CARDS, DEFAULT_ENEMY_DECK, DEFAULT_MY_DECK } from "../data/cards.ts";
import type {
  ActionRecord,
  BoardSnapshot,
  CardData,
  Point,
  ReplayNodeData,
  ReplayProject,
  Side,
  TowerActionRecord,
  TowerStateData,
  UnitMarkerData
} from "./types.ts";

export const PROJECT_VERSION = 1;
export const ARENA_GRID = {
  columns: 18,
  rows: 32
};
export const MAX_TIMER_SECONDS = 180;
export const DEFAULT_TOWER_MAX_HP: Record<string, number> = {
  "enemy-king": 4824,
  "enemy-left-princess": 3052,
  "enemy-right-princess": 3052,
  "my-left-princess": 3052,
  "my-right-princess": 3052,
  "my-king": 4824
};

export function createId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}_${crypto.randomUUID()}`;
  }

  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

export function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function cardsToRecord(cards: CardData[]): Record<string, CardData> {
  return Object.fromEntries(cards.map((card) => [card.id, card]));
}

export function createSnapshot(myDeck: string[], enemyDeck: string[]): BoardSnapshot {
  return {
    myState: {
      hand: myDeck.slice(0, 4),
      drawQueue: myDeck.slice(4, 8)
    },
    enemyState: {
      hand: enemyDeck.slice(0, 4),
      drawQueue: enemyDeck.slice(4, 8)
    },
    costDelta: 0
  };
}

export function createDefaultTowerStates(
  towerMaxHp: Record<string, number> = DEFAULT_TOWER_MAX_HP
): Record<string, TowerStateData> {
  return Object.fromEntries(
    Object.entries(towerMaxHp).map(([towerId, maxHp]) => [
      towerId,
      {
        hp: Math.max(0, Math.round(maxHp)),
        destroyed: false
      }
    ])
  );
}

export function createNewProject(projectName = "未命名复盘工程"): ReplayProject {
  const now = new Date().toISOString();
  const rootNodeId = createId("node");
  const cards = cardsToRecord(DEFAULT_CARDS);
  const snapshot = createSnapshot(DEFAULT_MY_DECK, DEFAULT_ENEMY_DECK);
  const towerMaxHp = { ...DEFAULT_TOWER_MAX_HP };

  return {
    version: PROJECT_VERSION,
    projectName,
    createdAt: now,
    updatedAt: now,
    rootNodeId,
    cards,
    towerMaxHp,
    myDeck: [...DEFAULT_MY_DECK],
    enemyDeck: [...DEFAULT_ENEMY_DECK],
    nodes: {
      [rootNodeId]: {
        id: rootNodeId,
        parentId: null,
        childrenIds: [],
        title: "根局面",
        graphPosition: { x: 90, y: 70 },
        snapshot,
        actions: [],
        towerActions: [],
        towerStates: createDefaultTowerStates(towerMaxHp),
        unitMarkers: [],
        note: ""
      }
    }
  };
}

export function createChildNode(parent: ReplayNodeData, siblingIndex: number): ReplayNodeData {
  return {
    id: createId("node"),
    parentId: parent.id,
    childrenIds: [],
    title: `分支 ${siblingIndex + 1}`,
    graphPosition: {
      x: parent.graphPosition.x + siblingIndex * 42,
      y: parent.graphPosition.y + 130
    },
    timerSeconds: parent.timerSeconds,
    snapshot: cloneJson(parent.snapshot),
    actions: [],
    towerActions: [],
    towerStates: cloneJson(parent.towerStates ?? createDefaultTowerStates()),
    unitMarkers: cloneJson(parent.unitMarkers),
    note: ""
  };
}

export function createAction(side: Side, cardId: string, order: number, position?: Point): ActionRecord {
  return {
    id: createId("action"),
    side,
    cardId,
    actionType: "play_card",
    order,
    position
  };
}

export function createTowerAction(
  towerId: string,
  delta: number,
  hpAfter: number,
  order: number
): TowerActionRecord {
  return {
    id: createId("tower_action"),
    towerId,
    delta,
    hpAfter,
    order
  };
}

export function createUnitMarker(
  side: Side,
  card: CardData,
  point: Point
): UnitMarkerData {
  return {
    id: createId("unit"),
    side,
    cardId: card.id,
    displayName: card.name,
    x: clamp(point.x, 0, ARENA_GRID.columns),
    y: clamp(point.y, 0, ARENA_GRID.rows),
    statusTags: ["正常"],
    note: ""
  };
}

export function collectProjectSubtreeIds(project: ReplayProject, nodeId: string): string[] {
  const node = project.nodes[nodeId];
  if (!node) {
    return [];
  }

  return [
    nodeId,
    ...node.childrenIds.flatMap((childId) => collectProjectSubtreeIds(project, childId))
  ];
}

export function canConnectNodeToParent(
  project: ReplayProject,
  nodeId: string,
  parentId: string
): boolean {
  if (nodeId === project.rootNodeId || nodeId === parentId) {
    return false;
  }
  if (!project.nodes[nodeId] || !project.nodes[parentId]) {
    return false;
  }

  return !collectProjectSubtreeIds(project, nodeId).includes(parentId);
}

export function disconnectNode(project: ReplayProject, nodeId: string): ReplayProject {
  const node = project.nodes[nodeId];
  if (!node || node.id === project.rootNodeId || !node.parentId) {
    return project;
  }

  const parent = project.nodes[node.parentId];
  return {
    ...project,
    nodes: {
      ...project.nodes,
      [node.id]: {
        ...node,
        parentId: null
      },
      ...(parent
        ? {
            [parent.id]: {
              ...parent,
              childrenIds: parent.childrenIds.filter((childId) => childId !== node.id)
            }
          }
        : {})
    }
  };
}

export function connectNodeToParent(
  project: ReplayProject,
  nodeId: string,
  parentId: string
): ReplayProject {
  if (!canConnectNodeToParent(project, nodeId, parentId)) {
    return project;
  }

  const node = project.nodes[nodeId];
  const oldParent = node.parentId ? project.nodes[node.parentId] : null;
  const nextParent = project.nodes[parentId];

  return {
    ...project,
    nodes: {
      ...project.nodes,
      [node.id]: {
        ...node,
        parentId
      },
      ...(oldParent
        ? {
            [oldParent.id]: {
              ...oldParent,
              childrenIds: oldParent.childrenIds.filter((childId) => childId !== node.id)
            }
          }
        : {}),
      [nextParent.id]: {
        ...nextParent,
        childrenIds: nextParent.childrenIds.includes(node.id)
          ? nextParent.childrenIds
          : [...nextParent.childrenIds, node.id]
      }
    }
  };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function normalizeProject(input: unknown): ReplayProject {
  const fallback = createNewProject();
  if (!input || typeof input !== "object") {
    return fallback;
  }

  const project = input as Partial<ReplayProject>;
  if (!project.nodes || !project.rootNodeId || !project.nodes[project.rootNodeId]) {
    return fallback;
  }

  const cards = normalizeCards(project.cards);
  const towerMaxHp = normalizeTowerMaxHp(project.towerMaxHp);

  return {
    version: PROJECT_VERSION,
    projectName: project.projectName || fallback.projectName,
    createdAt: project.createdAt || new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    rootNodeId: project.rootNodeId,
    cards,
    towerMaxHp,
    myDeck: normalizeDeck(project.myDeck, DEFAULT_MY_DECK),
    enemyDeck: normalizeDeck(project.enemyDeck, DEFAULT_ENEMY_DECK),
    nodes: normalizeNodes(project.nodes, towerMaxHp, cards)
  };
}

function normalizeNodes(
  nodes: Record<string, ReplayNodeData>,
  towerMaxHp: Record<string, number>,
  cards: Record<string, CardData>
): Record<string, ReplayNodeData> {
  return Object.fromEntries(
    Object.entries(nodes).map(([nodeId, node]) => [
      nodeId,
      {
        ...node,
        timerSeconds: normalizeTimerSeconds(node.timerSeconds),
        towerActions: normalizeTowerActions(node.towerActions),
        towerStates: normalizeTowerStates(node.towerStates, towerMaxHp),
        unitMarkers: normalizeUnitMarkers(node.unitMarkers, cards)
      }
    ])
  );
}

function normalizeTowerMaxHp(value: unknown): Record<string, number> {
  const raw = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return Object.fromEntries(
    Object.entries(DEFAULT_TOWER_MAX_HP).map(([towerId, fallback]) => {
      const parsed = Number(raw[towerId]);
      return [towerId, Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : fallback];
    })
  );
}

function normalizeCards(cards: Record<string, CardData> | undefined): Record<string, CardData> {
  const defaults = cardsToRecord(DEFAULT_CARDS);
  if (!cards) {
    return defaults;
  }

  return {
    ...Object.fromEntries(
      Object.entries(defaults).map(([cardId, defaultCard]) => [
        cardId,
        {
          ...(cards[cardId] ?? {}),
          ...defaultCard
        }
      ])
    ),
    ...Object.fromEntries(
      Object.entries(cards).filter(([cardId]) => !defaults[cardId])
    )
  };
}

function normalizeTowerStates(
  states: Record<string, TowerStateData> | undefined,
  towerMaxHp: Record<string, number>
): Record<string, TowerStateData> {
  const fallback = createDefaultTowerStates(towerMaxHp);
  return Object.fromEntries(
    Object.entries(towerMaxHp).map(([towerId, maxHp]) => {
      const state = states?.[towerId];
      const hp = typeof state?.hp === "number" && Number.isFinite(state.hp) ? state.hp : fallback[towerId].hp;
      return [
        towerId,
        {
          hp: Math.round(clamp(hp, 0, maxHp)),
          destroyed: Boolean(state?.destroyed)
        }
      ];
    })
  );
}

function normalizeTowerActions(actions: TowerActionRecord[] | undefined): TowerActionRecord[] {
  return Array.isArray(actions)
    ? actions
        .filter((action) => action && typeof action.towerId === "string")
        .map((action, index) => ({
          ...action,
          id: action.id || createId("tower_action"),
          delta: typeof action.delta === "number" && Number.isFinite(action.delta) ? action.delta : 0,
          hpAfter: typeof action.hpAfter === "number" && Number.isFinite(action.hpAfter) ? action.hpAfter : 0,
          order: typeof action.order === "number" && Number.isFinite(action.order) ? action.order : index + 1
        }))
    : [];
}

function normalizeUnitMarkers(
  units: UnitMarkerData[] | undefined,
  cards: Record<string, CardData>
): UnitMarkerData[] {
  return Array.isArray(units)
    ? units.map((unit) => ({
        ...unit,
        displayName: cards[unit.cardId]?.name ?? unit.displayName
      }))
    : [];
}

function normalizeTimerSeconds(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }

  return Math.round(clamp(value, 0, MAX_TIMER_SECONDS));
}

function normalizeDeck(deck: string[] | undefined, fallback: string[]): string[] {
  const next = Array.isArray(deck) ? deck.filter(Boolean).slice(0, 8) : [];
  while (next.length < 8) {
    next.push(fallback[next.length] ?? fallback[0]);
  }
  return next;
}
