export const NATIVE_REPLAY_SCHEMA = "cr-native-replay.v2" as const;
export const ENGINE_TICKS_PER_SECOND = 20;
export const STANDARD_NATIVE_DECK = [26000000, 26000001, 26000005, 28000001,
  28000000, 26000003, 26000014, 26000018] as const;

export interface EngineIdentity {
  upstreamCommit: string;
  probeSha256: string;
  apkSha256: string;
  libgSha256: string;
  runtimeFingerprintSha256: string;
  protocolVersion: string;
  cropProfileId: string;
}

export interface NativeMatchConfig {
  seed: number;
  deck0: number[];
  deck1: number[];
  preset: "standard" | "custom";
}

export interface NativeBattleAction {
  id: string;
  tick: number;
  sequence: number;
  owner: 0 | 1;
  kind: "play-card" | "activate-ability";
  nativeCardId?: number;
  slot?: number;
  nativeX?: number;
  nativeY?: number;
  nativeEntityId?: number;
  nativeAbilityId?: number;
}

export interface NativeTimelineNode {
  id: string;
  parentId: string | null;
  childIds: string[];
  finalTick: number;
  title: string;
  note: string;
  actionsFromParent: NativeBattleAction[];
  stateDigest: string;
  createdAt: string;
}

export interface NativeReplayProject {
  schema: typeof NATIVE_REPLAY_SCHEMA;
  projectId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  engine: EngineIdentity;
  match: NativeMatchConfig;
  rootNodeId: string;
  nodes: Record<string, NativeTimelineNode>;
  layout: Record<string, { x: number; y: number }>;
}

export interface NativeWorkingBranch {
  checkedOutNodeId: string;
  workingTick: number;
  actions: NativeBattleAction[];
}

export function createNativeProject(
  engine: EngineIdentity,
  match: NativeMatchConfig,
  rootTick: number,
  stateDigest: string,
  title = "新战斗"
): NativeReplayProject {
  const now = new Date().toISOString();
  const rootId = crypto.randomUUID();
  return {
    schema: NATIVE_REPLAY_SCHEMA,
    projectId: crypto.randomUUID(),
    title,
    createdAt: now,
    updatedAt: now,
    engine: { ...engine },
    match: { ...match, deck0: [...match.deck0], deck1: [...match.deck1] },
    rootNodeId: rootId,
    nodes: {
      [rootId]: {
        id: rootId,
        parentId: null,
        childIds: [],
        finalTick: rootTick,
        title: "开局",
        note: "",
        actionsFromParent: [],
        stateDigest,
        createdAt: now
      }
    },
    layout: { [rootId]: { x: 0, y: 0 } }
  };
}

export function checkoutNativeNode(project: NativeReplayProject, nodeId: string): NativeWorkingBranch {
  const node = project.nodes[nodeId];
  if (!node) throw new Error("节点不存在");
  return { checkedOutNodeId: nodeId, workingTick: node.finalTick, actions: [] };
}

export function advanceNativeWork(branch: NativeWorkingBranch, tick: number): NativeWorkingBranch {
  if (!Number.isSafeInteger(tick) || tick < branch.workingTick) {
    throw new Error("战斗时间不能倒退");
  }
  return { ...branch, workingTick: tick };
}

export function appendConfirmedNativeAction(
  project: NativeReplayProject,
  branch: NativeWorkingBranch,
  action: NativeBattleAction
): NativeWorkingBranch {
  const base = project.nodes[branch.checkedOutNodeId];
  if (!base) throw new Error("分支基节点不存在");
  if (action.tick <= base.finalTick || action.tick > branch.workingTick ||
      !Number.isSafeInteger(action.sequence) || action.sequence < 0) {
    throw new Error("动作 tick 或顺序无效");
  }
  const previous = branch.actions[branch.actions.length - 1];
  if (previous && (action.tick < previous.tick ||
      (action.tick === previous.tick && action.sequence <= previous.sequence))) {
    throw new Error("动作必须按 tick 与序号递增");
  }
  validateAction(action);
  return { ...branch, actions: [...branch.actions, { ...action }] };
}

export function createNativeCheckpoint(
  project: NativeReplayProject,
  branch: NativeWorkingBranch,
  stateDigest: string,
  title?: string
): { project: NativeReplayProject; branch: NativeWorkingBranch; node: NativeTimelineNode } {
  const parent = project.nodes[branch.checkedOutNodeId];
  if (!parent) throw new Error("分支基节点不存在");
  if (!Number.isSafeInteger(branch.workingTick) || branch.workingTick <= parent.finalTick) {
    throw new Error("新节点必须晚于父节点");
  }
  if (!stateDigest) throw new Error("缺少已确认的状态摘要");
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  const node: NativeTimelineNode = {
    id,
    parentId: parent.id,
    childIds: [],
    finalTick: branch.workingTick,
    title: title?.trim() || `时间点 ${formatNativeTick(branch.workingTick)}`,
    note: "",
    actionsFromParent: branch.actions.map((action) => ({ ...action })),
    stateDigest,
    createdAt: now
  };
  const siblingCount = parent.childIds.length;
  const parentPosition = project.layout[parent.id] ?? { x: 0, y: 0 };
  const next: NativeReplayProject = {
    ...project,
    updatedAt: now,
    nodes: {
      ...project.nodes,
      [parent.id]: { ...parent, childIds: [...parent.childIds, id] },
      [id]: node
    },
    layout: {
      ...project.layout,
      [id]: { x: parentPosition.x + siblingCount * 180, y: parentPosition.y + 130 }
    }
  };
  return { project: next, branch: checkoutNativeNode(next, id), node };
}

export function nativePathToNode(project: NativeReplayProject, nodeId: string): NativeTimelineNode[] {
  const path: NativeTimelineNode[] = [];
  const seen = new Set<string>();
  let id: string | null = nodeId;
  while (id) {
    if (seen.has(id)) throw new Error("节点树存在环");
    seen.add(id);
    const node: NativeTimelineNode | undefined = project.nodes[id];
    if (!node) throw new Error("节点引用缺失");
    path.push(node);
    id = node.parentId;
  }
  if (path[path.length - 1]?.id !== project.rootNodeId) throw new Error("节点未连接到根节点");
  return path.reverse();
}

export function nativeActionsToNode(project: NativeReplayProject, nodeId: string): NativeBattleAction[] {
  return nativePathToNode(project, nodeId).flatMap((node) => node.actionsFromParent);
}

export function nearestNativeSnapshotNode(
  project: NativeReplayProject, nodeId: string, targetTick: number, availableIds: ReadonlySet<string>
): NativeTimelineNode | null {
  return [...nativePathToNode(project, nodeId)].reverse().find((node) =>
    node.finalTick <= targetTick && availableIds.has(node.id)) ?? null;
}

export function updateNativeNodeMetadata(
  project: NativeReplayProject,
  nodeId: string,
  patch: { title?: string; note?: string; position?: { x: number; y: number } }
): NativeReplayProject {
  const node = project.nodes[nodeId];
  if (!node) throw new Error("节点不存在");
  const position = patch.position;
  if (position && (!Number.isFinite(position.x) || !Number.isFinite(position.y))) {
    throw new Error("节点位置无效");
  }
  return {
    ...project,
    updatedAt: new Date().toISOString(),
    nodes: {
      ...project.nodes,
      [nodeId]: {
        ...node,
        title: patch.title === undefined ? node.title : patch.title.trim(),
        note: patch.note === undefined ? node.note : patch.note
      }
    },
    layout: position ? { ...project.layout, [nodeId]: { ...position } } : project.layout
  };
}

export function deleteNativeSubtree(project: NativeReplayProject, nodeId: string): NativeReplayProject {
  if (nodeId === project.rootNodeId) throw new Error("根节点不可删除");
  const parentId = project.nodes[nodeId]?.parentId;
  if (!parentId || !project.nodes[parentId]) throw new Error("节点或父节点不存在");
  const deleted = new Set<string>();
  const visit = (id: string): void => {
    if (deleted.has(id)) throw new Error("节点树存在环");
    const node = project.nodes[id];
    if (!node) throw new Error("节点引用缺失");
    deleted.add(id);
    node.childIds.forEach(visit);
  };
  visit(nodeId);
  const nodes = Object.fromEntries(Object.entries(project.nodes).filter(([id]) => !deleted.has(id)));
  nodes[parentId] = { ...nodes[parentId], childIds: nodes[parentId].childIds.filter((id) => id !== nodeId) };
  const layout = Object.fromEntries(Object.entries(project.layout).filter(([id]) => !deleted.has(id)));
  return { ...project, updatedAt: new Date().toISOString(), nodes, layout };
}

export function nativeSubtreeIds(project: NativeReplayProject, nodeId: string): string[] {
  const visited = new Set<string>();
  const collect = (id: string): void => {
    if (visited.has(id)) throw new Error("节点树存在环");
    const node = project.nodes[id];
    if (!node) throw new Error("节点引用缺失");
    visited.add(id);
    node.childIds.forEach(collect);
  };
  collect(nodeId);
  return [...visited];
}

export function validateNativeProject(input: unknown): NativeReplayProject {
  if (!input || typeof input !== "object" || (input as { schema?: string }).schema !== NATIVE_REPLAY_SCHEMA) {
    throw new Error("旧版或未知项目格式；仅支持原生战斗 v2 项目");
  }
  const project = input as NativeReplayProject;
  if (!project.rootNodeId || !project.nodes || !project.match || !project.engine || !project.layout) {
    throw new Error("项目字段缺失");
  }
  if (!Number.isSafeInteger(project.match.seed) || project.match.deck0?.length !== 8 ||
      project.match.deck1?.length !== 8) {
    throw new Error("随机种子或卡组无效");
  }
  if (project.match.preset !== "standard" ||
      JSON.stringify(project.match.deck0) !== JSON.stringify(STANDARD_NATIVE_DECK) ||
      JSON.stringify(project.match.deck1) !== JSON.stringify(STANDARD_NATIVE_DECK)) {
    throw new Error("当前引擎仅支持已经验证的标准卡组");
  }
  const identity = project.engine;
  if (!identity || Object.values(identity).some((value) => typeof value !== "string" || !value)) {
    throw new Error("引擎身份字段无效");
  }
  const entries = Object.entries(project.nodes);
  if (entries.length === 0 || !project.nodes[project.rootNodeId]) throw new Error("根节点缺失");
  const root = project.nodes[project.rootNodeId];
  if (root.parentId !== null || root.actionsFromParent.length !== 0) throw new Error("根节点历史无效");
  const visited = new Set<string>();
  const visit = (id: string): void => {
    if (visited.has(id)) throw new Error("节点树有环或重复引用");
    visited.add(id);
    const node = project.nodes[id];
    if (!node || node.id !== id || !Array.isArray(node.childIds) ||
        !Array.isArray(node.actionsFromParent) || !Number.isSafeInteger(node.finalTick) ||
        !node.stateDigest) throw new Error("节点数据无效");
    const parent = node.parentId ? project.nodes[node.parentId] : null;
    if (parent && node.finalTick <= parent.finalTick) throw new Error("子节点时间必须晚于父节点");
    if (node.parentId && !parent) throw new Error("父节点缺失");
    let lastTick = parent?.finalTick ?? node.finalTick;
    let lastSequence = -1;
    for (const action of node.actionsFromParent) {
      validateAction(action);
      if (action.tick <= (parent?.finalTick ?? -1) || action.tick > node.finalTick ||
          action.tick < lastTick || (action.tick === lastTick && action.sequence <= lastSequence)) {
        throw new Error("动作时间或顺序无效");
      }
      if (action.tick !== lastTick) lastSequence = -1;
      lastTick = action.tick;
      lastSequence = action.sequence;
    }
    for (const childId of node.childIds) {
      if (project.nodes[childId]?.parentId !== id) throw new Error("父子节点引用不一致");
      visit(childId);
    }
  };
  visit(project.rootNodeId);
  if (visited.size !== entries.length) throw new Error("项目中存在孤立节点");
  return project;
}

export function formatNativeTick(tick: number): string {
  const seconds = Math.max(0, tick) / ENGINE_TICKS_PER_SECOND;
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${
    (seconds % 60).toFixed(2).padStart(5, "0")
  }`;
}

function validateAction(action: NativeBattleAction): void {
  if (!action.id || !Number.isSafeInteger(action.tick) || !Number.isSafeInteger(action.sequence) ||
      (action.owner !== 0 && action.owner !== 1)) throw new Error("动作字段无效");
  if (action.kind === "play-card") {
    if (!Number.isSafeInteger(action.nativeCardId) || !Number.isSafeInteger(action.slot) ||
        action.slot! < 0 || action.slot! > 3 || !Number.isSafeInteger(action.nativeX) ||
        !Number.isSafeInteger(action.nativeY) || action.nativeX! < 0 || action.nativeX! > 18000 ||
        action.nativeY! < 0 || action.nativeY! > 32000) throw new Error("出牌动作字段无效");
  } else if (action.kind === "activate-ability") {
    if (!Number.isSafeInteger(action.nativeEntityId) || !Number.isSafeInteger(action.nativeAbilityId)) {
      throw new Error("能力动作字段无效");
    }
  } else {
    throw new Error("未知动作类型");
  }
}
