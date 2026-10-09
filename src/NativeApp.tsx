import { useEffect, useRef, useState } from "react";
import {
  advanceNativeWork, appendConfirmedNativeAction, checkoutNativeNode, createNativeCheckpoint,
  createNativeProject, deleteNativeSubtree, formatNativeTick, nativeActionsToNode,
  nativePathToNode, nativeSubtreeIds, nearestNativeSnapshotNode, STANDARD_NATIVE_DECK,
  updateNativeNodeMetadata, type NativeBattleAction,
  type NativeReplayProject, type NativeWorkingBranch
} from "./domain/nativeReplay.ts";
import { NativeBridge, type BridgeStatus, type CropProfile, type NativeObservation } from "./native/bridge.ts";
import { arenaPixelToWorld, handSlotAtPixel } from "./native/geometry.ts";
import NativeSurface from "./native/NativeSurface.tsx";
import { openNativeProject, saveNativeProject } from "./native/files.ts";
import { clearNativeJournal, readNativeJournal, writeNativeJournal } from "./native/journal.ts";
import "./native.css";

const EMPTY_PROFILE: CropProfile = {
  id: "stock-v15-1080x1920", nativeWidth: 1080, nativeHeight: 1920,
  enemyHud: [0, 0, 1080, 282], arena: [54, 282, 972, 1384], myHud: [0, 1666, 1080, 254],
  world: [111, 436, 856, 1230], handCentersX: [211, 354, 497, 640], enemyHandReversed: true
};

export default function NativeApp() {
  const [bridge, setBridge] = useState<NativeBridge | null>(null);
  const [status, setStatus] = useState<BridgeStatus | null>(null);
  const [project, setProject] = useState<NativeReplayProject | null>(null);
  const [branch, setBranch] = useState<NativeWorkingBranch | null>(null);
  const [observation, setObservation] = useState<NativeObservation | null>(null);
  const [selected, setSelected] = useState<{ owner: 0 | 1; slot: number } | null>(null);
  const [speed, setSpeed] = useState(1);
  const [scrubRoute, setScrubRoute] = useState<{ nodeId: string; maxTick: number } | null>(null);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [notice, setNotice] = useState("准备启动原生战斗环境");
  const [busy, setBusy] = useState(false);
  const initialized = useRef(false);
  const busyRef = useRef(false);
  const projectRef = useRef(project);
  const branchRef = useRef(branch);
  const observationRef = useRef(observation);
  const snapshots = useRef(new Map<string, number>());
  const scrubRouteRef = useRef(scrubRoute);

  function putProject(value: NativeReplayProject | null): void {
    projectRef.current = value;
    setProject(value);
  }
  function putBranch(value: NativeWorkingBranch | null): void {
    branchRef.current = value;
    setBranch(value);
  }
  function putObservation(value: NativeObservation | null): void {
    observationRef.current = value;
    setObservation(value);
  }
  function putScrubRoute(value: { nodeId: string; maxTick: number } | null): void {
    scrubRouteRef.current = value;
    setScrubRoute(value);
  }
  async function exclusive(work: () => Promise<void>): Promise<void> {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try { await work(); }
    catch (error) { setNotice(`操作失败：${String(error)}`); }
    finally { busyRef.current = false; setBusy(false); }
  }

  async function rememberSnapshot(client: NativeBridge, nodeId: string, expectedTick: number): Promise<void> {
    const receipt = await client.post<{ handle: number; tick: number }>("/v1/snapshot-create");
    if (!Number.isSafeInteger(receipt.handle) || receipt.handle <= 0 || receipt.tick !== expectedTick) {
      if (Number.isSafeInteger(receipt.handle) && receipt.handle > 0) {
        await client.post("/v1/snapshot-release", { handle: receipt.handle }).catch(() => undefined);
      }
      throw new Error("节点快照与当前战斗 tick 不一致");
    }
    const previous = snapshots.current.get(nodeId);
    if (previous) await client.post("/v1/snapshot-release", { handle: previous }).catch(() => undefined);
    snapshots.current.delete(nodeId);
    snapshots.current.set(nodeId, receipt.handle);
    if (snapshots.current.size > 32) {
      const oldest = snapshots.current.entries().next().value as [string, number] | undefined;
      if (oldest) {
        snapshots.current.delete(oldest[0]);
        await client.post("/v1/snapshot-release", { handle: oldest[1] });
      }
    }
  }

  async function releaseSnapshots(client: NativeBridge): Promise<void> {
    const handles = [...snapshots.current.values()];
    snapshots.current.clear();
    for (const handle of handles) {
      await client.post("/v1/snapshot-release", { handle }).catch(() => undefined);
    }
  }

  async function replayTo(client: NativeBridge, source: NativeReplayProject, nodeId: string,
                          actions: NativeBattleAction[], targetTick: number,
                          expectedDigest?: string): Promise<NativeObservation> {
    const path = nativePathToNode(source, nodeId);
    const targetNode = expectedDigest && path.find((node) =>
      node.finalTick === targetTick && node.stateDigest === expectedDigest);
    const cacheTarget = async (): Promise<void> => {
      if (targetNode && !snapshots.current.has(targetNode.id)) {
        await rememberSnapshot(client, targetNode.id, targetNode.finalTick).catch(() => undefined);
      }
    };
    const base = nearestNativeSnapshotNode(source, nodeId, targetTick, new Set(snapshots.current.keys()));
    if (base) {
      try {
        const state = await client.replay(source.match.seed,
          actions.filter((action) => action.tick > base.finalTick && action.tick <= targetTick),
          targetTick, expectedDigest, snapshots.current.get(base.id), base.finalTick, base.stateDigest);
        const used = snapshots.current.get(base.id)!;
        snapshots.current.delete(base.id);
        snapshots.current.set(base.id, used);
        await cacheTarget();
        return state;
      } catch {
        // A new native battle invalidates every process-scoped snapshot handle.
      }
    }
    await releaseSnapshots(client);
    const state = await client.replay(source.match.seed,
      actions.filter((action) => action.tick <= targetTick), targetTick, expectedDigest);
    await cacheTarget();
    return state;
  }

  useEffect(() => {
    let alive = true;
    NativeBridge.launch().then((client) => {
      if (!alive) return;
      setBridge(client);
      void client.post<BridgeStatus>("/v1/start").then(setStatus).catch((error) => setNotice(String(error)));
    }).catch((error) => setNotice(String(error)));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!bridge) return;
    let alive = true;
    const refresh = async () => {
      try {
        const latest = await bridge.get<BridgeStatus>("/v1/status");
        if (!alive) return;
        setStatus(latest);
        if (latest.error) setNotice(latest.error);
        if (latest.ready && !initialized.current && !busyRef.current) {
          initialized.current = true;
          await exclusive(async () => {
            const loaded = projectRef.current;
            if (loaded) {
              const root = loaded.nodes[loaded.rootNodeId];
              let state = await bridge.replay(loaded.match.seed, [], root.finalTick, root.stateDigest);
              await rememberSnapshot(bridge, loaded.rootNodeId, root.finalTick);
              putObservation(state);
              putBranch(checkoutNativeNode(loaded, loaded.rootNodeId));
              putScrubRoute(null);
              const journal = await readNativeJournal(loaded);
              if (journal && window.confirm("发现未提交的战斗分支。恢复到上次确认的 tick 吗？")) {
                state = await replayTo(bridge, loaded, journal.checkedOutNodeId,
                  [...nativeActionsToNode(loaded, journal.checkedOutNodeId), ...journal.actions],
                  journal.workingTick);
                putObservation(state);
                putBranch(journal);
              } else if (journal) {
                await clearNativeJournal(loaded.projectId);
              }
              setNotice("项目已载入原生战斗引擎");
            } else {
              const seed = Math.floor(Math.random() * 2**30);
              const state = await bridge.post<NativeObservation>("/v1/reset", { seed });
              const next = createNativeProject(latest.identity,
                { seed, preset: "standard", deck0: [...STANDARD_NATIVE_DECK], deck1: [...STANDARD_NATIVE_DECK] },
                state.tick, state.digest);
              await rememberSnapshot(bridge, next.rootNodeId, state.tick);
              putProject(next);
              putBranch(checkoutNativeNode(next, next.rootNodeId));
              putObservation(state);
              putScrubRoute(null);
              setFilePath(await saveNativeProject(next, null));
              setNotice("原生战斗已就绪；选择任一方手牌，再点击竞技场出牌");
            }
          });
          if (!observationRef.current) initialized.current = false;
        }
        if (latest.ready && latest.playing && !busyRef.current && observationRef.current) {
          const state = await bridge.get<NativeObservation>("/v1/observe");
          if (!alive || busyRef.current) return;
          putObservation(state);
          const work = branchRef.current;
          if (work && state.tick >= work.workingTick) putBranch(advanceNativeWork(work, state.tick));
        }
      } catch (error) {
        if (alive) setNotice(`连接中断：${String(error)}`);
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 450);
    return () => { alive = false; window.clearInterval(interval); };
  }, [bridge]);

  const profile = status?.cropProfile ?? EMPTY_PROFILE;
  const currentNode = project && branch ? project.nodes[branch.checkedOutNodeId] : null;

  async function newProject(): Promise<void> {
    if (!bridge || !status?.ready) return;
    await exclusive(async () => {
      await releaseSnapshots(bridge);
      const seed = Math.floor(Math.random() * 2**30);
      const state = await bridge.post<NativeObservation>("/v1/reset", { seed });
      const next = createNativeProject(status.identity,
        { seed, preset: "standard", deck0: [...STANDARD_NATIVE_DECK], deck1: [...STANDARD_NATIVE_DECK] },
        state.tick, state.digest);
      await rememberSnapshot(bridge, next.rootNodeId, state.tick);
      putProject(next);
      putBranch(checkoutNativeNode(next, next.rootNodeId));
      putObservation(state);
      putScrubRoute(null);
      setFilePath(await saveNativeProject(next, null));
      setSelected(null);
      setNotice("已开始新战斗");
    });
  }

  async function openProject(): Promise<void> {
    await exclusive(async () => {
      const opened = await openNativeProject();
      if (!opened) return;
      if (status && Object.entries(status.identity).some(([key, value]) =>
        opened.project.engine[key as keyof typeof status.identity] !== value)) {
        throw new Error("项目的引擎身份与当前运行时不匹配");
      }
      if (bridge && status?.ready) {
        await releaseSnapshots(bridge);
        const root = opened.project.nodes[opened.project.rootNodeId];
        let state = await bridge.replay(opened.project.match.seed, [], root.finalTick, root.stateDigest);
        await rememberSnapshot(bridge, opened.project.rootNodeId, root.finalTick);
        const journal = await readNativeJournal(opened.project);
        if (journal && window.confirm("发现未提交的战斗分支。恢复到上次确认的 tick 吗？")) {
          state = await replayTo(bridge, opened.project, journal.checkedOutNodeId,
            [...nativeActionsToNode(opened.project, journal.checkedOutNodeId), ...journal.actions],
            journal.workingTick);
          putBranch(journal);
        } else {
          if (journal) await clearNativeJournal(opened.project.projectId);
          putBranch(checkoutNativeNode(opened.project, opened.project.rootNodeId));
        }
        putObservation(state);
      } else {
        putBranch(checkoutNativeNode(opened.project, opened.project.rootNodeId));
      }
      putProject(opened.project);
      putScrubRoute(null);
      setFilePath(opened.path);
      setSelected(null);
      setNotice(`已打开 ${opened.project.title}`);
    });
  }

  async function saveProject(): Promise<void> {
    if (!projectRef.current) return;
    await exclusive(async () => {
      const path = await saveNativeProject(projectRef.current!, filePath);
      setFilePath(path);
      setNotice(`已保存到 ${path}`);
    });
  }

  async function checkpoint(): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current) return;
    await exclusive(async () => {
      const state = await bridge.post<NativeObservation>("/v1/pause");
      putObservation(state);
      const work = advanceNativeWork(branchRef.current!, state.tick);
      const result = createNativeCheckpoint(projectRef.current!, work, state.digest);
      await rememberSnapshot(bridge, result.node.id, state.tick);
      const savedPath = await saveNativeProject(result.project, filePath);
      putProject(result.project);
      putBranch(result.branch);
      putScrubRoute(null);
      setFilePath(savedPath);
      await clearNativeJournal(result.project.projectId);
      setNotice(`已在 ${formatNativeTick(state.tick)} 创建节点；战斗保持暂停`);
    });
  }

  async function checkout(nodeId: string): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current ||
        (nodeId === branchRef.current.checkedOutNodeId && !scrubRouteRef.current)) return;
    const work = branchRef.current;
    if (!scrubRouteRef.current && work.actions.length &&
        !window.confirm("当前有未提交的出牌。先创建节点保存这条分支，再切换吗？")) return;
    await exclusive(async () => {
      let activeProject = projectRef.current!;
      if (!scrubRouteRef.current && work.actions.length) {
        const state = await bridge.post<NativeObservation>("/v1/pause");
        const saved = createNativeCheckpoint(activeProject, advanceNativeWork(work, state.tick), state.digest);
        await rememberSnapshot(bridge, saved.node.id, state.tick);
        const savedPath = await saveNativeProject(saved.project, filePath);
        activeProject = saved.project;
        putProject(activeProject);
        putBranch(saved.branch);
        putObservation(state);
        setFilePath(savedPath);
        await clearNativeJournal(activeProject.projectId);
      }
      const node = activeProject.nodes[nodeId];
      const state = await replayTo(bridge, activeProject, nodeId,
        nativeActionsToNode(activeProject, nodeId), node.finalTick, node.stateDigest);
      putObservation(state);
      putBranch(checkoutNativeNode(activeProject, nodeId));
      putScrubRoute(null);
      setSelected(null);
      setNotice(`已恢复节点「${node.title}」`);
    });
  }

  async function togglePlay(): Promise<void> {
    if (!bridge || !status?.ready) return;
    await exclusive(async () => {
      if (status.playing) {
        const state = await bridge.post<NativeObservation>("/v1/pause");
        putObservation(state);
        if (branchRef.current) {
          const work = advanceNativeWork(branchRef.current, state.tick);
          if (projectRef.current && work.actions.length) await writeNativeJournal(projectRef.current, work);
          putBranch(work);
        }
        setStatus({ ...status, playing: false });
      } else {
        await bridge.post("/v1/resume");
        putScrubRoute(null);
        setStatus({ ...status, playing: true });
      }
    });
  }

  async function stepForward(): Promise<void> {
    if (!bridge || !branchRef.current) return;
    await exclusive(async () => {
      const state = await bridge.post<NativeObservation>("/v1/step", { ticks: 1 });
      putObservation(state);
      const work = advanceNativeWork(branchRef.current!, state.tick);
      if (projectRef.current && work.actions.length) await writeNativeJournal(projectRef.current, work);
      putBranch(work);
      putScrubRoute(null);
      if (status) setStatus({ ...status, playing: false });
    });
  }

  async function seek(targetTick: number): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current || !observationRef.current) return;
    await exclusive(async () => {
      let source = projectRef.current!;
      const work = branchRef.current!;
      let route = scrubRouteRef.current;
      const rootTick = source.nodes[source.rootNodeId].finalTick;
      if (!Number.isSafeInteger(targetTick) || targetTick < rootTick ||
          targetTick > (route?.maxTick ?? work.workingTick)) {
        throw new Error("目标时间超出当前时间线");
      }
      if (!route) {
        if (work.actions.length && targetTick < work.workingTick) {
          const paused = await bridge.post<NativeObservation>("/v1/pause");
          const saved = createNativeCheckpoint(source, advanceNativeWork(work, paused.tick), paused.digest);
          await rememberSnapshot(bridge, saved.node.id, paused.tick);
          const savedPath = await saveNativeProject(saved.project, filePath);
          source = saved.project;
          putProject(source);
          putBranch(saved.branch);
          putObservation(paused);
          setFilePath(savedPath);
          await clearNativeJournal(source.projectId);
          route = { nodeId: saved.node.id, maxTick: paused.tick };
        } else {
          route = { nodeId: work.checkedOutNodeId, maxTick: work.workingTick };
        }
        putScrubRoute(route);
      }
      const path = nativePathToNode(source, route.nodeId);
      const base = [...path].reverse().find((node) => node.finalTick <= targetTick)!;
      const actions = nativeActionsToNode(source, route.nodeId).filter((action) => action.tick <= targetTick);
      const expected = targetTick === base.finalTick ? base.stateDigest : undefined;
      const state = await replayTo(bridge, source, route.nodeId, actions, targetTick, expected);
      putObservation(state);
      const nextWork = { checkedOutNodeId: base.id, workingTick: targetTick,
        actions: actions.filter((action) => action.tick > base.finalTick) };
      putBranch(nextWork);
      if (status) setStatus({ ...status, playing: false });
      setSelected(null);
    });
  }

  async function changeSpeed(value: number): Promise<void> {
    if (!bridge) return;
    await exclusive(async () => {
      await bridge.post("/v1/speed", { multiplier: value });
      setSpeed(value);
    });
  }

  function selectHand(owner: 0 | 1, pixel: { x: number; y: number }): void {
    const slot = handSlotAtPixel(profile, owner, pixel);
    if (slot === null) return;
    setSelected({ owner, slot });
    setNotice(`${owner === 1 ? "我方" : "敌方"}已选择第 ${slot + 1} 张手牌；点击竞技场落点`);
  }

  async function playAt(pixel: { x: number; y: number }): Promise<void> {
    if (!selected || !bridge || !projectRef.current || !branchRef.current) return;
    const world = arenaPixelToWorld(profile, pixel);
    await exclusive(async () => {
      const wasPlaying = status?.playing ?? false;
      const receipt = await bridge.post<{
        accepted: boolean; observation: NativeObservation; action?: Omit<NativeBattleAction, "id">;
      }>("/v1/play-card", { owner: selected.owner, slot: selected.slot, ...world });
      putObservation(receipt.observation);
      let work = advanceNativeWork(branchRef.current!, receipt.observation.tick);
      if (receipt.accepted && receipt.action) {
        work = appendConfirmedNativeAction(projectRef.current!, work,
          { ...receipt.action, id: crypto.randomUUID() });
        await writeNativeJournal(projectRef.current!, work);
        setNotice(`已确认 ${selected.owner === 1 ? "我方" : "敌方"}出牌；当前为新分支草稿`);
      } else if (work.actions.length) {
        await writeNativeJournal(projectRef.current!, work);
      } else {
        setNotice("出牌未被引擎接受；历史未记录此动作");
      }
      putBranch(work);
      putScrubRoute(null);
      setSelected(null);
      if (wasPlaying) {
        await bridge.post("/v1/resume");
        if (status) setStatus({ ...status, playing: true });
      } else if (status) setStatus({ ...status, playing: false });
    });
  }

  function editNode(patch: { title?: string; note?: string }): void {
    if (!projectRef.current || !branchRef.current) return;
    putProject(updateNativeNodeMetadata(projectRef.current, branchRef.current.checkedOutNodeId, patch));
  }

  async function removeNode(): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current) return;
    const id = branchRef.current.checkedOutNodeId;
    const node = projectRef.current.nodes[id];
    if (!node.parentId || !window.confirm(`删除「${node.title}」及其全部后代？`)) return;
    const parentId = node.parentId;
    await exclusive(async () => {
      const source = projectRef.current!;
      const removed = nativeSubtreeIds(source, id);
      const next = deleteNativeSubtree(source, id);
      const parent = next.nodes[parentId];
      const state = await replayTo(bridge, next, parentId,
        nativeActionsToNode(next, parentId), parent.finalTick, parent.stateDigest);
      for (const removedId of removed) {
        const handle = snapshots.current.get(removedId);
        if (handle) {
          snapshots.current.delete(removedId);
          await bridge.post("/v1/snapshot-release", { handle }).catch(() => undefined);
        }
      }
      putProject(next);
      putBranch(checkoutNativeNode(next, parentId));
      putObservation(state);
      putScrubRoute(null);
      setFilePath(await saveNativeProject(next, filePath));
      await clearNativeJournal(next.projectId);
      setNotice(`已删除 ${removed.length} 个节点`);
    });
  }

  function renderNode(id: string, depth = 0): React.ReactNode {
    if (!project) return null;
    const node = project.nodes[id];
    return <div key={id}>
      <button className={`tree-node ${branch?.checkedOutNodeId === id ? "active" : ""}`}
        style={{ paddingLeft: 14 + depth * 18 }} onClick={() => { void checkout(id); }}>
        <span className="tree-node-line">{node.title}</span>
        <small>{formatNativeTick(node.finalTick)} · {node.actionsFromParent.length} 动作</small>
      </button>
      {node.childIds.map((childId) => renderNode(childId, depth + 1))}
    </div>;
  }

  const ownElixir = observation?.players.find((player) => player.owner === 1)?.elixir;
  const enemyElixir = observation?.players.find((player) => player.owner === 0)?.elixir;
  const ready = Boolean(status?.ready && observation);

  return <div className="native-app">
    <header className="native-header">
      <div className="native-brand"><span className="native-brand-icon">♛</span>
        <div><strong>皇室战争 · 原生战斗复盘</strong><small>时间线与平行分支</small></div></div>
      <div className="native-header-actions">
        <button onClick={() => { void newProject(); }} disabled={!status?.ready || busy}>新战斗</button>
        <button onClick={() => { void openProject(); }} disabled={busy}>打开 v2</button>
        <button className="primary" onClick={() => { void saveProject(); }} disabled={!project || busy}>保存</button>
      </div>
    </header>
    <div className="native-workspace">
      <aside className="native-sidebar">
        <div className="panel-heading"><span>时间分支</span><span>{project ? Object.keys(project.nodes).length : 0} 节点</span></div>
        <div className="native-project-title">{project?.title ?? "等待战斗启动"}</div>
        <div className="native-tree">{project && renderNode(project.rootNodeId)}</div>
        <button className="wide-button" onClick={() => { void checkpoint(); }}
          disabled={!ready || busy || !branch || !currentNode || branch.workingTick <= currentNode.finalTick}>
          ＋ 在当前 tick 创建节点
        </button>
        {branch && branch.actions.length > 0 && <div className="draft-note">● 新分支草稿 · {branch.actions.length} 个已确认动作</div>}
      </aside>
      <main className="native-main">
        <div className="battle-toolbar">
          <div className="battle-title"><strong>原生战斗</strong><span>{status?.stage ?? "连接桥接服务"}</span></div>
          <div className="battle-clock">{formatNativeTick(observation?.tick ?? 0)}</div>
          <div className="battle-elixir" aria-label="双方圣水结构化读数">
            敌 {enemyElixir === undefined ? "—" : (enemyElixir / 10000).toFixed(1)}
            <span>·</span>
            我 {ownElixir === undefined ? "—" : (ownElixir / 10000).toFixed(1)}
          </div>
        </div>
        <div className="battle-stage"><NativeSurface bridge={bridge} ready={ready} profile={profile}
          selected={selected} onHudClick={selectHand} onArenaClick={(pixel) => { void playAt(pixel); }} /></div>
        <div className="playback-controls">
          <button onClick={() => { void seek(Math.max(project?.nodes[project.rootNodeId].finalTick ?? 90,
            (observation?.tick ?? 91) - 1)); }} disabled={!ready || busy}>◀│ 后退 1 tick</button>
          <button className="play-button" onClick={() => { void togglePlay(); }} disabled={!ready || busy}>
            {status?.playing ? "Ⅱ 暂停" : "▶ 播放"}</button>
          <button onClick={() => { void stepForward(); }} disabled={!ready || busy}>前进 1 tick │▶</button>
          <select aria-label="播放速度" value={speed} onChange={(event) => { void changeSpeed(Number(event.target.value)); }}
            disabled={!ready || busy}>{[0.25, 0.5, 1, 2, 4].map((value) =>
              <option value={value} key={value}>{value}×</option>)}</select>
        </div>
        <input className="timeline-scrubber" aria-label="时间跳转" type="range"
          min={project?.nodes[project.rootNodeId].finalTick ?? 90}
          max={Math.max(91, scrubRoute?.maxTick ?? branch?.workingTick ?? 91)}
          value={observation?.tick ?? 90} disabled={!ready || busy}
          onChange={(event) => { void seek(Number(event.target.value)); }} />
        <div className="native-notice" role="status">{notice}</div>
      </main>
      <aside className="native-inspector">
        <div className="panel-heading"><span>节点详情</span></div>
        {currentNode ? <>
          <label>标题<input value={currentNode.title} onChange={(event) => editNode({ title: event.target.value })} /></label>
          <div className="inspector-stat"><span>时间</span><strong>{formatNativeTick(currentNode.finalTick)}</strong></div>
          <div className="inspector-stat"><span>本段动作</span><strong>{currentNode.actionsFromParent.length}</strong></div>
          <label>备注<textarea rows={8} value={currentNode.note}
            onChange={(event) => editNode({ note: event.target.value })} placeholder="记录这一条时间线的判断…" /></label>
          <button className="danger-button" onClick={() => { void removeNode(); }} disabled={!currentNode.parentId || busy}>删除此分支及后代</button>
        </> : <p className="inspector-empty">战斗就绪后会显示节点信息。</p>}
        <div className="runtime-card"><strong>运行时</strong>
          <span>{status?.stage ?? "未连接"}</span>
          {status?.error && <p>{status.error}</p>}
          <small>引擎 {status?.identity.upstreamCommit.slice(0, 8) ?? "—"}</small>
        </div>
      </aside>
    </div>
  </div>;
}
