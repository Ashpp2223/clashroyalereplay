import nativeCards from "../data/nativeCards.json" with { type: "json" };
export interface NativeCardDefinition {
  id:number;name:string;engineName:string;cost:number;kind:string;image:string|null;
  forms:number;evolutionImage:string|null;heroImage:string|null;abilityNames:string[];omitFromStartingHand:boolean;
}
export const NATIVE_CARD_CATALOG:readonly NativeCardDefinition[] = nativeCards;
const SUPPORTED_CARD_IDS = new Set(nativeCards.map(card => card.id));

export const NATIVE_REPLAY_SCHEMA = "cr-native-replay.v2" as const;
export const ENGINE_TICKS_PER_SECOND = 20;
export const DEFAULT_NATIVE_LEVEL = 16;
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
  readonly seed: number;
  readonly deck0: readonly number[];
  readonly deck1: readonly number[];
  readonly preset: "standard" | "custom";
  readonly forms0?: readonly number[];
  readonly forms1?: readonly number[];
  readonly level?: number;
  readonly dealMode?: 'ordered';
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
  nativeForm?: number;
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
  editableCopy?: boolean;
}

export interface NativeReplayProject {
  schema: typeof NATIVE_REPLAY_SCHEMA;
  projectId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  engine: EngineIdentity;
  readonly match: NativeMatchConfig;
  rootNodeId: string;
  nodes: Record<string, NativeTimelineNode>;
  layout: Record<string, { x: number; y: number }>;
}

export interface NativeWorkingBranch {
  checkedOutNodeId: string;
  workingTick: number;
  actions: NativeBattleAction[];
}

export function validateNativeDeck(deck: readonly number[]): void {
  if (!Array.isArray(deck) || deck.length !== 8 || new Set(deck).size !== 8 ||
      deck.some(card => !Number.isSafeInteger(card) || !SUPPORTED_CARD_IDS.has(card))) {
    throw new Error("每方需要八张不同的受支持普通卡牌");
  }
}

export function validateNativeForms(deck:readonly number[], forms:readonly number[] = Array(8).fill(0)):void {
  if (!Array.isArray(forms) || forms.length !== 8 || forms.some((mode,index) =>
      !Number.isSafeInteger(mode) || ![0,1,2].includes(mode) || (mode !== 0 && !(NATIVE_CARD_CATALOG.find(card=>card.id===deck[index])!.forms & mode)))) throw new Error('卡牌形态不受当前引擎支持');
  if (forms.filter(mode=>mode===1).length>2 || forms.filter(mode=>mode===2).length>2 || forms.filter(Boolean).length>3) throw new Error('每方最多两张觉醒、两张精英，特殊形态合计最多三张');
}

export function validateNativeDeal(deck:readonly number[]):void {
  validateNativeDeck(deck);
  const omitted=(id:number)=>Boolean(NATIVE_CARD_CATALOG.find(card=>card.id===id)?.omitFromStartingHand);
  const count=deck.filter(omitted).length;
  const positions=deck.map((id,index)=>omitted(id)?index:-1).filter(index=>index>=0);
  const expected=count===0?[]:count===1?[7]:[5,7];
  if(JSON.stringify(positions)!==JSON.stringify(expected))throw new Error('原生牌序规则：单张镜像或采集器在第 8 位；同时携带时在第 6、8 位');
}

function lockNativeMatch(match:NativeMatchConfig):NativeMatchConfig {
  return Object.freeze({...match,deck0:Object.freeze([...match.deck0]),deck1:Object.freeze([...match.deck1]),
    ...(match.forms0?{forms0:Object.freeze([...match.forms0])}:{}),...(match.forms1?{forms1:Object.freeze([...match.forms1])}:{})});
}

export function projectForCurrentEngine(project:NativeReplayProject, identity:EngineIdentity): {project:NativeReplayProject; upgraded:boolean} {
  if(Object.entries(identity).every(([key,value])=>project.engine[key as keyof EngineIdentity]===value)) return {project,upgraded:false};
  const old=project.engine;
  const known = old.probeSha256==='97044a901f4eaef335e09014631bd2ac7bd3d9701a7522c0c7b761413c7b2028' && old.apkSha256==='c7e32cdfe5486d0432562320d1c3bd3265faf2891bd3f7658fe4a7dc4aca4ced';
  if(!known || ['upstreamCommit','libgSha256','runtimeFingerprintSha256','protocolVersion','cropProfileId'].some(key=>old[key as keyof EngineIdentity]!==identity[key as keyof EngineIdentity]) || project.match.preset!=='standard' || [...(project.match.forms0??[]),...(project.match.forms1??[])].some(Boolean)) throw new Error('项目的引擎版本不匹配');
  return {project:{...project, projectId:crypto.randomUUID(),title:project.title+'（升级副本）',updatedAt:new Date().toISOString(),engine:{...identity}},upgraded:true};
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
    match: lockNativeMatch(match),
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
  if (action.nativeCardId !== undefined && !project.match[action.owner === 0 ? 'deck0' : 'deck1'].includes(action.nativeCardId)) {
    throw new Error("出牌不属于该方初始卡组");
  }
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
  if(parent.editableCopy && parent.parentId && parent.childIds.length===0) {
    const node={...parent,finalTick:branch.workingTick,stateDigest,actionsFromParent:[...parent.actionsFromParent,...branch.actions.map(action=>({...action}))]};
    const next={...project,updatedAt:new Date().toISOString(),nodes:{...project.nodes,[node.id]:node}};
    return {project:next,branch:checkoutNativeNode(next,node.id),node};
  }
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

export function copyNativeNode(project:NativeReplayProject, branch:NativeWorkingBranch, stateDigest:string): {project:NativeReplayProject;branch:NativeWorkingBranch;node:NativeTimelineNode} {
  const parent=project.nodes[branch.checkedOutNodeId];
  if(!parent || !Number.isSafeInteger(branch.workingTick) || branch.workingTick<parent.finalTick)throw new Error('复制节点的时间无效');
  if(!stateDigest || (branch.workingTick===parent.finalTick && stateDigest!==parent.stateDigest))throw new Error('复制节点的状态不一致');
  const now=new Date().toISOString(),id=crypto.randomUUID();
  const node:NativeTimelineNode={id,parentId:parent.id,childIds:[],finalTick:branch.workingTick,title:`${parent.title} · 子节点`,note:'',actionsFromParent:branch.actions.map(action=>({...action})),stateDigest,createdAt:now,editableCopy:true};
  const position=project.layout[parent.id]??{x:0,y:0};
  const next={...project,updatedAt:now,nodes:{...project.nodes,[parent.id]:{...parent,editableCopy:false,childIds:[...parent.childIds,id]},[id]:node},layout:{...project.layout,[id]:{x:position.x+parent.childIds.length*190,y:position.y+130}}};
  return {project:next,branch:checkoutNativeNode(next,id),node};
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
  if (!Number.isSafeInteger(project.match.seed) || project.match.seed < 0 || project.match.seed > 2**31 - 1 || project.match.deck0?.length !== 8 ||
      project.match.deck1?.length !== 8) {
    throw new Error("随机种子或卡组无效");
  }
  validateNativeDeck(project.match.deck0);
  validateNativeDeck(project.match.deck1);
  validateNativeForms(project.match.deck0,project.match.forms0);
  validateNativeForms(project.match.deck1,project.match.forms1);
  if(project.match.level!==undefined && (!Number.isSafeInteger(project.match.level) || project.match.level<1 || project.match.level>16))throw new Error('战斗等级必须为 1～16 级');
  if(project.match.dealMode!==undefined && project.match.dealMode!=='ordered')throw new Error('初始牌序模式无效');
  if(project.match.dealMode==='ordered') [project.match.deck0,project.match.deck1].forEach(validateNativeDeal);
  if (!['standard', 'custom'].includes(project.match.preset)) throw new Error("卡组配置格式无效");
  if (project.match.preset === 'standard' && [project.match.deck0, project.match.deck1].some(deck =>
      JSON.stringify(deck) !== JSON.stringify(STANDARD_NATIVE_DECK))) throw new Error("标准卡组配置与预设不一致");
  if(project.match.preset==='standard' && [...(project.match.forms0??[]),...(project.match.forms1??[])].some(Boolean))throw new Error('特殊形态需使用自定义卡组配置');
  const identity = project.engine;
  if (!identity || ["upstreamCommit", "probeSha256", "apkSha256", "libgSha256", "runtimeFingerprintSha256",
    "protocolVersion", "cropProfileId"].some((key) => typeof identity[key as keyof EngineIdentity] !== "string" || !identity[key as keyof EngineIdentity])) {
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
    if(node.editableCopy!==undefined && (typeof node.editableCopy!=='boolean' || (node.editableCopy && (!node.parentId || node.childIds.length))))throw new Error('可编辑节点必须是复制出的末端节点');
    const parent = node.parentId ? project.nodes[node.parentId] : null;
    if (parent && node.finalTick < parent.finalTick) throw new Error("子节点时间不能早于父节点");
    if (node.parentId && !parent) throw new Error("父节点缺失");
    let lastTick = parent?.finalTick ?? node.finalTick;
    let lastSequence = -1;
    for (const action of node.actionsFromParent) {
      validateAction(action);
      if (action.nativeCardId !== undefined && !project.match[action.owner === 0 ? 'deck0' : 'deck1'].includes(action.nativeCardId)) throw new Error("出牌不属于该方初始卡组");
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
  return {...project,match:lockNativeMatch(project.match)};
}

export function formatNativeTick(tick: number): string {
  const seconds = Math.max(0, tick) / ENGINE_TICKS_PER_SECOND;
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${
    (seconds % 60).toFixed(2).padStart(5, "0")
  }`;
}

function validateAction(action: NativeBattleAction): void {
  if (!action.id || !Number.isSafeInteger(action.tick) || !Number.isSafeInteger(action.sequence) ||
      action.tick < 0 || action.sequence < 0 || (action.owner !== 0 && action.owner !== 1)) throw new Error("动作字段无效");
  if (action.kind === "play-card") {
    if(action.nativeForm!==undefined && ![0,1,2].includes(action.nativeForm))throw new Error('出牌形态无效');
    if (!Number.isSafeInteger(action.nativeCardId) || !SUPPORTED_CARD_IDS.has(action.nativeCardId!) || !Number.isSafeInteger(action.slot) ||
        action.slot! < 0 || action.slot! > 3 || !Number.isSafeInteger(action.nativeX) ||
        !Number.isSafeInteger(action.nativeY) || action.nativeX! < 0 || action.nativeX! > 18000 ||
        action.nativeY! < 0 || action.nativeY! > 32000) throw new Error("出牌动作字段无效");
  } else if (action.kind === "activate-ability") {
    if (!Number.isSafeInteger(action.nativeEntityId) || action.nativeEntityId! <= 0 || !Number.isSafeInteger(action.nativeAbilityId) || action.nativeAbilityId! <= 0) throw new Error("技能动作来源无效");
  } else {
    throw new Error("未知动作类型");
  }
}
