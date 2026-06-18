export type Side = "my" | "enemy";
export type CardType = "troop" | "spell" | "building";
export type ActionType = "play_card" | "manual_adjust";
export type TowerType = "king" | "princess";
export type TowerLane = "left" | "right" | "center";

export interface Point {
  x: number;
  y: number;
}

export interface CardData {
  id: string;
  name: string;
  cost: number;
  imagePath?: string;
  cardType: CardType;
}

export interface PlayerCycleState {
  hand: string[];
  drawQueue: string[];
}

export interface BoardSnapshot {
  myState: PlayerCycleState;
  enemyState: PlayerCycleState;
  costDelta: number;
  myTowerHp?: number;
  enemyTowerHp?: number;
}

export interface ActionRecord {
  id: string;
  side: Side;
  cardId: string;
  actionType: ActionType;
  order: number;
  position?: Point;
  note?: string;
}

export interface TowerStateData {
  hp: number;
  destroyed?: boolean;
}

export interface TowerActionRecord {
  id: string;
  towerId: string;
  delta: number;
  hpAfter: number;
  order: number;
}

export interface UnitMarkerData {
  id: string;
  side: Side;
  cardId: string;
  displayName: string;
  x: number;
  y: number;
  hp?: number;
  maxHp?: number;
  statusTags: string[];
  note: string;
}

export interface ReplayNodeData {
  id: string;
  parentId: string | null;
  childrenIds: string[];
  title: string;
  graphPosition: Point;
  timerSeconds?: number;
  snapshot: BoardSnapshot;
  actions: ActionRecord[];
  towerActions: TowerActionRecord[];
  towerStates: Record<string, TowerStateData>;
  unitMarkers: UnitMarkerData[];
  note: string;
}

export interface ReplayProject {
  version: number;
  projectName: string;
  createdAt: string;
  updatedAt: string;
  rootNodeId: string;
  cards: Record<string, CardData>;
  towerMaxHp: Record<string, number>;
  myDeck: string[];
  enemyDeck: string[];
  nodes: Record<string, ReplayNodeData>;
}
