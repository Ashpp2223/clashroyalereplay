import { useCallback, useEffect, useRef, useState } from "react";
import {
  advanceNativeWork, appendConfirmedNativeAction, checkoutNativeNode, createNativeCheckpoint, copyNativeNode,
  createNativeProject, deleteNativeSubtree, formatNativeTick, nativeActionsToNode,
  nativePathToNode, nativeSubtreeIds, nearestNativeSnapshotNode, STANDARD_NATIVE_DECK, DEFAULT_NATIVE_LEVEL,
  updateNativeNodeMetadata, NATIVE_CARD_CATALOG, validateNativeDeck, validateNativeForms, projectForCurrentEngine, type NativeBattleAction,
  type NativeReplayProject, type NativeWorkingBranch
} from "./domain/nativeReplay.ts";
import { NativeBridge, type BridgeStatus, type CropProfile, type NativeObservation } from "./native/bridge.ts";
import { arenaPixelToWorld } from "./native/geometry.ts";
import NativeSurface from "./native/NativeSurface.tsx";
import NativeNodeTree from "./native/NativeNodeTree.tsx";
import { openNativeProject, readNativeProject, listReplayArchives, saveNativeProject, rememberNativeSession, reopenNativeSession, type SavedReplayEntry } from "./native/files.ts";
import { chooseSavesDirectory, getDefaultSavesDirectory } from "./utils/file.ts";
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
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [acceptSdkTerms, setAcceptSdkTerms] = useState(false);
  const [installDirectory, setInstallDirectory] = useState<string | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archives, setArchives] = useState<SavedReplayEntry[]>([]);
  const [archiveDirectory, setArchiveDirectory] = useState("");
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [deckOpen, setDeckOpen] = useState(false);
  const [draftDecks, setDraftDecks] = useState<number[][]>([[...STANDARD_NATIVE_DECK], [...STANDARD_NATIVE_DECK]]);
  const [draftForms, setDraftForms] = useState<number[][]>([Array(8).fill(0),Array(8).fill(0)]);
  const [deckSide, setDeckSide] = useState<0 | 1>(1);
  const [cardSearch, setCardSearch] = useState("");
  const initialized = useRef(false);
  const busyRef = useRef(false);
  const projectRef = useRef(project);
  const branchRef = useRef(branch);
  const observationRef = useRef(observation);
  const snapshots = useRef(new Map<string, number>());
  const scrubRouteRef = useRef(scrubRoute);
  const connectionErrorRef = useRef<string | null>(null);
  const bridgeRef = useRef<NativeBridge | null>(null);
  const recoveryNode = useRef<string | null>(null);

  const interrupt = useCallback((reason: string): void => {
    if (connectionErrorRef.current) return;
    connectionErrorRef.current = reason;
    setConnectionError(reason);
    setSelected(null);
    const source = projectRef.current;
    const work = branchRef.current;
    if (source && work?.actions.length) {
      void writeNativeJournal(source, work).catch((error) => setNotice(`草稿写入失败，请保留窗口：${String(error)}`));
    }
    void bridgeRef.current?.post("/v1/suspend").catch(() => undefined);
  }, []);

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
    setNotice(`正在恢复至 ${formatNativeTick(targetTick)}，请稍候…`);
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
          targetTick, expectedDigest, snapshots.current.get(base.id), base.finalTick, base.stateDigest, source.match);
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
      actions.filter((action) => action.tick <= targetTick), targetTick, expectedDigest, undefined, undefined, undefined, source.match);
    await cacheTarget();
    return state;
  }

  useEffect(() => {
    let alive = true;
    NativeBridge.launch().then(async (client) => {
      if (!alive) return;
      try {
        const previous = await reopenNativeSession();
        if (!alive) return;
        if (previous) {
          putProject(previous.project);
          putBranch(checkoutNativeNode(previous.project, previous.nodeId));
          setFilePath(previous.path);
          recoveryNode.current = previous.nodeId;
        }
      } catch (error) { setNotice(`上次项目无法读取，请重新打开：${String(error)}`); }
      bridgeRef.current = client;
      setBridge(client);
      void client.get<BridgeStatus>("/v1/status").then(async (latest) => {
        setStatus(latest);
        if (latest.environmentConfigured && latest.resourcesConfigured) {
          setStatus(await client.post<BridgeStatus>("/v1/start"));
        }
      }).catch((error) => setNotice(String(error)));
    }).catch((error) => { setNotice(String(error)); interrupt("战斗服务未启动，请设置运行环境后重试"); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (filePath && branch?.checkedOutNodeId) rememberNativeSession(filePath, branch.checkedOutNodeId);
  }, [filePath, branch?.checkedOutNodeId]);

  useEffect(() => {
    if (!bridge) return;
    let alive = true;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const latest = await bridge.get<BridgeStatus>("/v1/status");
        if (!alive) return;
        setStatus(latest);
        if (latest.error) { setNotice(latest.error); interrupt(latest.error); }
        if (latest.ready && !initialized.current && !busyRef.current && !connectionErrorRef.current) {
          initialized.current = true;
          await exclusive(async () => {
            let loaded = projectRef.current;
            if (loaded) {
              const original=loaded;
              const upgraded=projectForCurrentEngine(loaded,latest.identity);
              loaded=upgraded.project;
              const root = loaded.nodes[recoveryNode.current ?? loaded.rootNodeId] ?? loaded.nodes[loaded.rootNodeId];
              setNotice(`正在恢复已保存节点「${root.title}」，请稍候…`);
              let state = await bridge.replay(loaded.match.seed, nativeActionsToNode(loaded, root.id), root.finalTick, root.stateDigest, undefined, undefined, undefined, loaded.match);
              await rememberSnapshot(bridge, root.id, root.finalTick);
              if(upgraded.upgraded) {
                const path=await saveNativeProject(loaded,null);
                putProject(loaded);setFilePath(path);
              }
              putObservation(state);
              putBranch(checkoutNativeNode(loaded, root.id));
              recoveryNode.current = null;
              putScrubRoute(null);
              const journal = await readNativeJournal(original);
              if (journal && window.confirm("发现未提交的战斗分支。恢复到上次确认的 tick 吗？")) {
                state = await replayTo(bridge, loaded, journal.checkedOutNodeId,
                  [...nativeActionsToNode(loaded, journal.checkedOutNodeId), ...journal.actions],
                  journal.workingTick);
                putObservation(state);
                putBranch(journal);
                if(upgraded.upgraded)await writeNativeJournal(loaded,journal);
              } else if (journal) {
                await clearNativeJournal(loaded.projectId);
              }
              setNotice("项目已载入原生战斗引擎");
            } else {
              const seed = Math.floor(Math.random() * 2**30);
              const state = await bridge.post<NativeObservation>("/v1/reset", { seed, level:DEFAULT_NATIVE_LEVEL });
              const next = createNativeProject(latest.identity,
                { seed, preset: "standard", deck0: [...STANDARD_NATIVE_DECK], deck1: [...STANDARD_NATIVE_DECK], level:DEFAULT_NATIVE_LEVEL },
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
        if (latest.ready && latest.playing && !busyRef.current && observationRef.current && !connectionErrorRef.current) {
          const state = await bridge.get<NativeObservation>("/v1/observe");
          if (!alive || busyRef.current) return;
          putObservation(state);
          const work = branchRef.current;
          if (work && state.tick >= work.workingTick) {
            const next = advanceNativeWork(work, state.tick);
            putBranch(next);
            if (projectRef.current && next.actions.length) await writeNativeJournal(projectRef.current, next);
          }
        }
      } catch (error) {
        if (alive) { setNotice(`连接中断：${String(error)}`); interrupt("连接中断，请恢复到已保存节点"); }
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 450);
    return () => { alive = false; window.clearInterval(interval); };
  }, [bridge, interrupt]);

  async function reconnect(): Promise<void> {
    await exclusive(async () => {
      recoveryNode.current = branchRef.current?.checkedOutNodeId ?? null;
      snapshots.current.clear();
      initialized.current = false;
      putObservation(null);
      const client = await NativeBridge.launch();
      bridgeRef.current = client;
      await client.post<BridgeStatus>("/v1/start");
      connectionErrorRef.current = null;
      setConnectionError(null);
      setBridge(client);
      setNotice("正在恢复到已保存节点，草稿将从恢复日志读取");
    });
  }

  async function configureRuntime(): Promise<void> {
    await exclusive(async () => {
      const [{ open }, { invoke }] = await Promise.all([
        import("@tauri-apps/plugin-dialog"), import("@tauri-apps/api/core")
      ]);
      const directory = await open({ directory: true, multiple: false,
        title: "选择已准备好的战斗运行环境目录" });
      if (!directory || Array.isArray(directory)) return;
      if (projectRef.current && branchRef.current?.actions.length) {
        await writeNativeJournal(projectRef.current, branchRef.current);
      }
      await invoke("configure_native_runtime", { directory });
      recoveryNode.current = branchRef.current?.checkedOutNodeId ?? null;
      snapshots.current.clear();
      initialized.current = false;
      putObservation(null);
      const client = await NativeBridge.launch();
      bridgeRef.current = client;
      await client.post("/v1/start");
      connectionErrorRef.current = null;
      setConnectionError(null);
      setBridge(client);
      setNotice("运行环境已设置，正在连接战斗");
    });
  }

  async function exportDiagnostics(): Promise<void> {
    await exclusive(async () => {
      const [{ save }, { invoke }] = await Promise.all([
        import("@tauri-apps/plugin-dialog"), import("@tauri-apps/api/core")
      ]);
      const record = bridgeRef.current ? await bridgeRef.current.get("/v1/diagnostics").catch(() => null) : null;
      const path = await save({ title: "保存诊断记录", defaultPath: "复盘器诊断.json",
        filters: [{ name: "诊断记录", extensions: ["json"] }] });
      if (!path) return;
      await invoke("write_project_file", { path, data: JSON.stringify(record ?? {
        schema: "cr-native-diagnostics.v1", ready: false, error: connectionErrorRef.current ?? "战斗服务未连接"
      }, null, 2) });
      setNotice("诊断记录已保存，未包含会话口令或项目内容");
    });
  }

  async function installEnvironment(): Promise<void> {
    if (!bridgeRef.current) return;
    await exclusive(async () => {
      await bridgeRef.current!.post("/v1/setup/install", { acceptLicense: acceptSdkTerms, directory: installDirectory });
      setNotice("正在准备安卓运行环境，可以在页面查看进度");
    });
  }

  async function chooseInstallDirectory(): Promise<void> {
    await exclusive(async () => {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const directory = await open({ directory: true, multiple: false, title: "选择安卓环境安装目录（建议英文路径）" });
      if (typeof directory === "string") setInstallDirectory(directory);
    });
  }

  async function importResources(): Promise<void> {
    if (!bridgeRef.current) return;
    await exclusive(async () => {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const path = await open({ title: "选择本地战斗资源包", multiple: false,
        filters: [{ name: "战斗资源包", extensions: ["zip"] }] });
      if (!path || Array.isArray(path)) return;
      await bridgeRef.current!.post("/v1/setup/resources", { path });
      setNotice("正在校验并导入战斗资源");
    });
  }

  const profile = status?.cropProfile ?? EMPTY_PROFILE;
  const currentNode = project && branch ? project.nodes[branch.checkedOutNodeId] : null;

  async function newProject(deck0: number[] = [...STANDARD_NATIVE_DECK], deck1: number[] = [...STANDARD_NATIVE_DECK], forms0:number[]=Array(8).fill(0), forms1:number[]=Array(8).fill(0)): Promise<void> {
    if (!bridge || !status?.ready) return;
    await exclusive(async () => {
      validateNativeDeck(deck0);
      validateNativeDeck(deck1);
      validateNativeForms(deck0,forms0);
      validateNativeForms(deck1,forms1);
      if (projectRef.current) {
        await saveNativeProject(projectRef.current, filePath);
        if (branchRef.current?.actions.length) await writeNativeJournal(projectRef.current, branchRef.current);
      }
      await releaseSnapshots(bridge);
      const seed = Math.floor(Math.random() * 2**30);
      const state = await bridge.post<NativeObservation>("/v1/reset", { seed, deck0, deck1, forms0, forms1, level:DEFAULT_NATIVE_LEVEL });
      const next = createNativeProject(status.identity,
        { seed, preset: [deck0, deck1].every(deck => JSON.stringify(deck) === JSON.stringify(STANDARD_NATIVE_DECK)) && ![...forms0,...forms1].some(Boolean) ? "standard" : "custom", deck0, deck1, forms0, forms1, level:DEFAULT_NATIVE_LEVEL },
        state.tick, state.digest);
      await rememberSnapshot(bridge, next.rootNodeId, state.tick);
      putProject(next);
      putBranch(checkoutNativeNode(next, next.rootNodeId));
      putObservation(state);
      putScrubRoute(null);
      setFilePath(await saveNativeProject(next, null));
      setSelected(null);
      setDeckOpen(false);
      setNotice("已开始新战斗");
    });
  }

  async function refreshArchives(): Promise<void> {
    setArchiveLoading(true);
    setArchiveError(null);
    try {
      setArchiveDirectory(await getDefaultSavesDirectory() ?? "");
      setArchives(await listReplayArchives());
    } catch (error) { setArchiveError(String(error)); }
    finally { setArchiveLoading(false); }
  }

  async function showArchives(): Promise<void> {
    if (bridgeRef.current && status?.playing) {
      await exclusive(async () => {
        const state = await bridgeRef.current!.post<NativeObservation>("/v1/pause");
        putObservation(state);
        if (branchRef.current) putBranch(advanceNativeWork(branchRef.current, state.tick));
        setStatus({ ...status, playing: false });
      });
    }
    setArchiveOpen(true);
    await refreshArchives();
  }

  async function changeArchiveDirectory(): Promise<void> {
    try { if (await chooseSavesDirectory()) await refreshArchives(); }
    catch (error) { setArchiveError(String(error)); }
  }

  async function openProject(path?: string): Promise<void> {
    await exclusive(async () => {
      const opened = path ? await readNativeProject(path) : await openNativeProject();
      if (!opened) return;
      const active = projectRef.current;
      if (active) {
        await saveNativeProject(active, filePath);
        if (branchRef.current?.actions.length) await writeNativeJournal(active, branchRef.current);
      }
      const original=opened.project;
      const upgrade=status?projectForCurrentEngine(opened.project,status.identity):{project:opened.project,upgraded:false};
      opened.project=upgrade.project;
      if (bridge && status?.ready) {
        await releaseSnapshots(bridge);
        const root = opened.project.nodes[opened.project.rootNodeId];
        let state = await bridge.replay(opened.project.match.seed, [], root.finalTick, root.stateDigest, undefined, undefined, undefined, opened.project.match);
        await rememberSnapshot(bridge, opened.project.rootNodeId, root.finalTick);
        const journal = await readNativeJournal(original);
        if (journal && window.confirm("发现未提交的战斗分支。恢复到上次确认的 tick 吗？")) {
          state = await replayTo(bridge, opened.project, journal.checkedOutNodeId,
            [...nativeActionsToNode(opened.project, journal.checkedOutNodeId), ...journal.actions],
            journal.workingTick);
          putBranch(journal);
          if(upgrade.upgraded)await writeNativeJournal(opened.project,journal);
        } else {
          if (journal) await clearNativeJournal(opened.project.projectId);
          putBranch(checkoutNativeNode(opened.project, opened.project.rootNodeId));
        }
        putObservation(state);
        connectionErrorRef.current=null;setConnectionError(null);initialized.current=true;
      } else {
        putBranch(checkoutNativeNode(opened.project, opened.project.rootNodeId));
      }
      putProject(opened.project);
      putScrubRoute(null);
      setFilePath(upgrade.upgraded?await saveNativeProject(opened.project,null):opened.path);
      setSelected(null);
      setArchiveOpen(false);
      setNotice(`已打开 ${opened.project.title}`);
    });
  }

  async function saveProject(): Promise<void> {
    if (!projectRef.current) return;
    if(bridge && status?.ready && branchRef.current && !connectionErrorRef.current && (status.playing || branchRef.current.workingTick>projectRef.current.nodes[branchRef.current.checkedOutNodeId].finalTick)) {
      await checkpoint();return;
    }
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
      if(status)setStatus({...status,playing:false});
      if(work.workingTick===projectRef.current!.nodes[work.checkedOutNodeId].finalTick) {
        setFilePath(await saveNativeProject(projectRef.current!,filePath));setNotice('当前节点已保存');return;
      }
      const updated=Boolean(projectRef.current!.nodes[work.checkedOutNodeId].editableCopy);
      const result = createNativeCheckpoint(projectRef.current!, work, state.digest);
      await rememberSnapshot(bridge, result.node.id, state.tick);
      const savedPath = await saveNativeProject(result.project, filePath);
      putProject(result.project);
      putBranch(result.branch);
      putScrubRoute(null);
      setFilePath(savedPath);
      await clearNativeJournal(result.project.projectId);
      setNotice(`已${updated?'更新当前节点':'保存为子节点'} · ${formatNativeTick(state.tick)}；战斗保持暂停`);
    });
  }

  async function checkout(nodeId: string): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current ||
        (nodeId === branchRef.current.checkedOutNodeId && !scrubRouteRef.current)) return;
    const work = branchRef.current;

    await exclusive(async () => {
      let activeProject = projectRef.current!;
      if (!scrubRouteRef.current && (work.actions.length || (activeProject.nodes[work.checkedOutNodeId].editableCopy && work.workingTick>activeProject.nodes[work.checkedOutNodeId].finalTick))) {
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
      if(status)setStatus({...status,playing:false});
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
      setNotice(`已跳转至 ${formatNativeTick(state.tick)}；战斗保持暂停`);
    });
  }

  async function changeSpeed(value: number): Promise<void> {
    if (!bridge) return;
    await exclusive(async () => {
      await bridge.post("/v1/speed", { multiplier: value });
      setSpeed(value);
    });
  }

  function selectHand(owner:0|1,slot:number):void {
    if(connectionErrorRef.current || busyRef.current || !status?.ready || !observationRef.current?.players.find(player=>player.owner===owner)?.hand.some(card=>card.slot===slot))return;
    setSelected({owner,slot});
    setNotice(`${owner===1?'我方':'敌方'}已选择第 ${slot+1} 张手牌；点击竞技场落点`);
  }

  async function copyNode():Promise<void> {
    if(!bridge || !projectRef.current || !branchRef.current)return;
    await exclusive(async()=>{
      const state=await bridge.post<NativeObservation>('/v1/pause');
      let source=projectRef.current!,work=advanceNativeWork(branchRef.current!,state.tick);
      if(source.nodes[work.checkedOutNodeId].editableCopy && work.workingTick>source.nodes[work.checkedOutNodeId].finalTick) {
        const saved=createNativeCheckpoint(source,work,state.digest);
        source=saved.project;work=saved.branch;
        await rememberSnapshot(bridge,saved.node.id,state.tick);
      }
      const copied=copyNativeNode(source,work,state.digest);
      await rememberSnapshot(bridge,copied.node.id,state.tick);
      setFilePath(await saveNativeProject(copied.project,filePath));
      putProject(copied.project);putBranch(copied.branch);putObservation(state);putScrubRoute(null);setSelected(null);
      await clearNativeJournal(copied.project.projectId);
      if(status)setStatus({...status,playing:false});
      setNotice('已复制为子节点；在此继续出牌，保存会更新这个节点');
    });
  }

  function moveNode(id:string,position:{x:number;y:number}):void {
    if(!projectRef.current)return;
    const next=updateNativeNodeMetadata(projectRef.current,id,{position});putProject(next);
    void saveNativeProject(next,filePath).catch(error=>setNotice(`节点位置保存失败：${String(error)}`));
  }

  async function playAt(pixel: { x: number; y: number }): Promise<void> {
    if (connectionErrorRef.current || busyRef.current || !status?.ready) return;
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
        putBranch(work);
        try { await writeNativeJournal(projectRef.current!, work); }
        catch (error) { interrupt("草稿保存失败，请恢复连接后重试保存"); throw error; }
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

  async function activateAbility(owner:0|1,entityId:number,abilityId:number):Promise<void> {
    if (!bridge || !ready || !projectRef.current || !branchRef.current) return;
    await exclusive(async()=>{
      const receipt=await bridge.post<{accepted:boolean;observation:NativeObservation;action?:Omit<NativeBattleAction,'id'>}>('/v1/activate-ability',{owner,entityId,abilityId});
      putObservation(receipt.observation);
      let work=advanceNativeWork(branchRef.current!,receipt.observation.tick);
      if(receipt.accepted && receipt.action) {
        work=appendConfirmedNativeAction(projectRef.current!,work,{...receipt.action,id:crypto.randomUUID()});
        await writeNativeJournal(projectRef.current!,work);
        setNotice('技能已由原生引擎确认，记录到当前分支');
      } else setNotice('技能执行未得到确认，未记录动作');
      putBranch(work);putScrubRoute(null);setSelected(null);
      if(status)setStatus({...status,playing:false});
    });
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

  const ownElixir = observation?.players.find((player) => player.owner === 1)?.elixir;
  const enemyElixir = observation?.players.find((player) => player.owner === 0)?.elixir;
  const ready = Boolean(status?.ready && observation && !connectionError);
  const settingUp = status?.setup?.state === "installing" || status?.setup?.state === "importing";
  let deckError:string|null=null;
  if(deckOpen) {
    try { draftDecks.forEach(validateNativeDeck); draftDecks.forEach((deck,owner)=>validateNativeForms(deck,draftForms[owner])); }
    catch(error) { deckError=String(error).replace(/^Error: /,''); }
  }

  return <div className="native-app">
    <header className="native-header">
      <div className="native-brand"><span className="native-brand-icon">♛</span>
        <div><strong>皇室战争 · 原生战斗复盘</strong><small>时间线与平行分支</small></div></div>
      <div className="native-header-actions">
        <button onClick={() => { void newProject(); }} disabled={!status?.ready || busy}>新战斗</button>
        <button onClick={() => { void showArchives(); }} disabled={busy}>存档</button>
        <button onClick={() => { setDraftDecks(project ? [[...project.match.deck0], [...project.match.deck1]] : [[...STANDARD_NATIVE_DECK], [...STANDARD_NATIVE_DECK]]); setDraftForms([project?.match.forms0?.slice()??Array(8).fill(0),project?.match.forms1?.slice()??Array(8).fill(0)]); setDeckOpen(true); }} disabled={busy}>选择初始卡组</button>
        <button onClick={() => { void openProject(); }} disabled={busy}>打开项目</button>
        <button onClick={() => { void configureRuntime(); }} disabled={busy}>设置运行环境</button>
        <button onClick={() => { void exportDiagnostics(); }} disabled={busy}>导出诊断</button>
        <button className="primary" onClick={() => { void saveProject(); }} disabled={!project || busy}>保存</button>
      </div>
    </header>
    <div className="native-workspace">
      <aside className="native-sidebar">
        <div className="panel-heading"><span>时间分支</span><span>{project ? Object.keys(project.nodes).length : 0} 节点</span></div>
        <div className="native-project-title">{project ? <input aria-label="战斗名称" value={project.title}
          onChange={(event) => putProject({ ...project, title: event.target.value, updatedAt: new Date().toISOString() })} /> : "等待战斗启动"}</div>
        {project && branch ? <NativeNodeTree project={project} selectedId={branch.checkedOutNodeId} disabled={!ready||busy} onSelect={id=>{void checkout(id);}} onMove={moveNode}/> : <div className="native-tree"/>}
        <button className="wide-button" onClick={()=>{void copyNode();}} disabled={!ready||busy}>＋ 从当前节点创建子节点</button>
        <button className="wide-button" onClick={() => { void checkpoint(); }}
          disabled={!ready || busy || !branch || !currentNode || branch.workingTick <= currentNode.finalTick}>
          保存当前节点
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
        <div className="ability-toolbar">{observation?.players.flatMap(player=>(player.runtime?.abilityRuntime??[]).map(ability=>{
          const entity=ability.championEntityKeys.find(key=>key);
          return <button key={`${player.owner}-${ability.controllerSlot}`} disabled={!ready || busy || !entity || !ability.available || ability.remainingCooldownMs>0}
            onClick={()=>{if(entity)void activateAbility(player.owner as 0|1,entity.secondaryIndex,ability.actionDataGlobalId);}}>{player.owner===1?'我方':'敌方'} · {NATIVE_CARD_CATALOG.find(card=>card.abilityNames.includes(ability.actionDataName))?.name??'技能'}{ability.remainingCooldownMs>0?` · ${(ability.remainingCooldownMs/1000).toFixed(1)}s`:''}</button>;
        }))}</div>
        <div className="battle-stage">{!observation ? <section className="first-run-panel">
          <div className="first-run-symbol">♛</div>
          <h2>{status?.environmentConfigured && status?.resourcesConfigured ? "连接战斗环境" : "首次使用 · 准备战斗环境"}</h2>
          <p>画面与战斗规则由原生游戏提供。没有模拟器的电脑，可在这里安装所需的最小安卓环境。</p>
          <div className="setup-step"><strong>1 · 安卓运行环境</strong>
            <span>{status?.environmentConfigured ? "已有环境已识别" : "首次下载约 1.9 GB，需要至少 28 GB 可用空间"}</span>
            {!status?.environmentConfigured && <>
              <label className="sdk-terms"><input type="checkbox" checked={acceptSdkTerms} onChange={(event) => setAcceptSdkTerms(event.target.checked)} />
                <span>我已阅读并接受 <a href="https://developer.android.com/studio/terms" target="_blank" rel="noreferrer">Android SDK 使用条款</a></span></label>
              <button className="setup-primary" onClick={() => { void installEnvironment(); }} disabled={!acceptSdkTerms || busy || settingUp}>安装安卓运行环境</button>
              <button onClick={() => { void chooseInstallDirectory(); }} disabled={busy || settingUp}>更改安装位置</button>
              <span>{installDirectory ?? "默认安装在当前用户的数据目录"}</span>
            </>}
          </div>
          <div className="setup-step"><strong>2 · 本地战斗资源</strong>
            <span>{status?.resourcesConfigured ? "战斗资源已连接" : "选择复盘器战斗资源包；游戏文件不会随安装程序分发"}</span>
            <button onClick={() => { void importResources(); }} disabled={!status?.environmentConfigured || settingUp || busy}>{status?.resourcesConfigured?'更新战斗资源包':'导入战斗资源包'}</button>
          </div>
          {settingUp && <div className="setup-progress"><span>{status?.setup?.stage}</span>
            {status?.setup?.state === "installing" && <progress max={status.setup.total} value={status.setup.downloaded} />}
          </div>}
          {status?.setup?.error && <p className="setup-error">{status.setup.error}</p>}
          <div className="setup-actions"><button onClick={() => { void configureRuntime(); }} disabled={busy || settingUp}>选择已有运行环境</button>
            <button className="setup-primary" onClick={() => { void reconnect(); }} disabled={busy || settingUp || !status?.environmentConfigured || !status?.resourcesConfigured}>开始连接</button></div>
          <small>若电脑未启用虚拟化，安装向导会给出具体检查结果。</small>
        </section> : <NativeSurface bridge={bridge} ready={ready} profile={profile}
          selected={selected} observation={observation} disabled={busy} onSelectCard={selectHand} onArenaClick={(pixel) => { void playAt(pixel); }} onUnavailable={interrupt} />}</div>
        {connectionError && <div className="native-recovery" role="alert">
          <span>{connectionError}</span><button onClick={() => { void reconnect(); }} disabled={busy}>恢复连接</button>
        </div>}
        <div className="playback-controls">
          <button onClick={() => { void seek(Math.max(project?.nodes[project.rootNodeId].finalTick ?? 90,
            (observation?.tick ?? 91) - 1)); }} disabled={!ready || busy}>◀│ 后退一步</button>
          <button className="play-button" onClick={() => { void togglePlay(); }} disabled={!ready || busy}>
            {status?.playing ? "Ⅱ 暂停" : "▶ 播放"}</button>
          <button onClick={() => { void stepForward(); }} disabled={!ready || busy}>前进一步 │▶</button>
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
          <button className="danger-button" onClick={() => { void removeNode(); }} disabled={!ready || !currentNode.parentId || busy}>删除此分支及后代</button>
        </> : <p className="inspector-empty">战斗就绪后会显示节点信息。</p>}
        <div className="runtime-card"><strong>战斗连接</strong>
          <span>{status?.stage ?? "未连接"}</span>
          {status?.error && <p>{status.error}</p>}
          <small>{project?.match.preset==='custom'?'自定义卡组':'标准卡组'} · {project?.match.level?`${project.match.level} 级`:'旧存档等级'} · 原生战斗</small>
        </div>
      </aside>
    </div>
    {archiveOpen && <div className="native-dialog-backdrop"><section className="native-dialog archive-dialog" role="dialog" aria-modal="true" aria-labelledby="archive-heading">
      <div className="dialog-heading"><h2 id="archive-heading">复盘存档</h2><button onClick={() => setArchiveOpen(false)} disabled={busy}>关闭</button></div>
      <p>选择已保存的原生战斗项目。第一版存档保留在列表中，需要用旧版记谱器打开。</p>
      <div className="archive-tools"><button onClick={() => { void refreshArchives(); }} disabled={archiveLoading || busy}>刷新</button>
        <button onClick={() => { void changeArchiveDirectory(); }} disabled={archiveLoading || busy}>选择存档目录</button>
        <button onClick={() => { void openProject(); }} disabled={busy}>打开其他文件</button></div>
      <div className="archive-directory">{archiveDirectory}</div>
      {archiveError && <p role="alert" className="setup-error">{archiveError}</p>}
      <div className="archive-list">{archiveLoading ? <p>正在读取存档…</p> : !archives.length ? <p>这个目录还没有复盘存档。新建战斗并保存后，会显示在这里。</p> : archives.map(entry => <article className="archive-entry" key={entry.path}>
        <div><strong>{entry.title}</strong><span>{entry.kind === "native" ? "原生战斗" : entry.kind === "legacy" ? "第一版存档" : "无法读取"} · {entry.nodeCount} 节点{entry.updatedAt ? ` · ${new Date(entry.updatedAt).toLocaleString()}` : ""}</span>
          <small>{entry.name}</small>{entry.error && <small>{entry.error}</small>}</div>
        <button disabled={busy || entry.kind !== "native"} onClick={() => { void openProject(entry.path); }}>{entry.kind === "native" ? "打开" : entry.kind === "legacy" ? "请使用旧版" : "格式无效"}</button>
      </article>)}</div>
    </section></div>}
    {deckOpen && <div className="native-dialog-backdrop"><section className="native-dialog deck-dialog" role="dialog" aria-modal="true" aria-labelledby="deck-heading">
      <div className="dialog-heading"><h2 id="deck-heading">选择初始卡组</h2><button onClick={() => setDeckOpen(false)} disabled={busy}>关闭</button></div>
      <p>双方各选择八张不同的卡牌，可设置觉醒与精英形态。应用后开始新战斗，当前项目先保存。卡牌和国王塔默认 16 级，起手牌由原生引擎按随机种子生成。</p>
      <div className="deck-tabs">{([1,0] as const).map(owner => <button className={deckSide === owner ? 'active' : ''} onClick={() => setDeckSide(owner)} key={owner}>{owner === 1 ? '我方卡组' : '敌方卡组'} · {draftDecks[owner].length}/8</button>)}
        <button onClick={() => {setDraftDecks([[...STANDARD_NATIVE_DECK], [...STANDARD_NATIVE_DECK]]);setDraftForms([Array(8).fill(0),Array(8).fill(0)]);}}>恢复标准卡组</button></div>
      <div className="selected-deck">{draftDecks[deckSide].map((id,index) => { const card = NATIVE_CARD_CATALOG.find(card => card.id === id)!; return <div className="selected-deck-card" key={id}><button disabled={busy} onClick={() => {setDraftDecks(decks => decks.map((deck,owner) => owner === deckSide ? deck.filter(cardId => cardId !== id) : deck));setDraftForms(forms=>forms.map((side,owner)=>owner===deckSide?side.filter((_,slot)=>slot!==index):side));}} title="点击移除">
        {((draftForms[deckSide][index]===1?card.evolutionImage:draftForms[deckSide][index]===2?card.heroImage:card.image)??card.image) ? <img src={(draftForms[deckSide][index]===1?card.evolutionImage:draftForms[deckSide][index]===2?card.heroImage:card.image)??card.image??undefined} alt=""/> : <span className="hand-card-fallback">{card.name.slice(0,2)}</span>}<strong>{card.name}</strong><small>{card.cost} 圣水 · 移除</small><span>{index+1}</span></button>
        <select aria-label={`${deckSide===1?'我方':'敌方'}${card.name}形态`} value={draftForms[deckSide][index]??0} onChange={event=>setDraftForms(forms=>forms.map((side,owner)=>owner===deckSide?side.map((mode,slot)=>slot===index?Number(event.target.value):mode):side))}>
          <option value={0}>普通</option>{Boolean(card.forms&1)&&<option value={1}>觉醒</option>}{Boolean(card.forms&2)&&<option value={2}>精英</option>}</select></div>; })}</div>
      <input className="card-search" aria-label="搜索普通卡牌" placeholder="搜索卡牌名称…" value={cardSearch} onChange={event => setCardSearch(event.target.value)} />
      <div className="card-catalog">{NATIVE_CARD_CATALOG.filter(card => `${card.name} ${card.engineName} ${card.forms&1?'觉醒':''} ${card.forms&2?'精英 英雄':''}`.toLowerCase().includes(cardSearch.trim().toLowerCase())).map(card => <button key={card.id}
        className={draftDecks[deckSide].includes(card.id) ? 'selected' : ''} disabled={busy || draftDecks[deckSide].includes(card.id) || draftDecks[deckSide].length >= 8}
        onClick={() => {setDraftDecks(decks => decks.map((deck,owner) => owner === deckSide ? [...deck,card.id] : deck));setDraftForms(forms=>forms.map((side,owner)=>owner===deckSide?[...side,0]:side));}}>{card.image && <img src={card.image} alt="" />}<strong>{card.name}</strong><small>{card.cost} 圣水</small><small>{card.forms&1?'觉醒 ':''}{card.forms&2?'精英':''}</small></button>)}</div>
      <div className="deck-footer"><span>{deckError??'每方最多两张觉醒、两张精英，特殊形态合计最多三张'}</span><button className="setup-primary" disabled={!status?.ready || busy || Boolean(deckError)} onClick={() => { void newProject(draftDecks[0],draftDecks[1],draftForms[0],draftForms[1]); }}>{busy ? '正在创建战斗…' : '应用卡组并开始新战斗'}</button></div>
    </section></div>}
  </div>;
}
