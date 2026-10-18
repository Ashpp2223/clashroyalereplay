import { EditHistory, structureEntry, undoStructure, isTextUndoTarget } from "./native/history.ts";
import { restoreUndoBranch, withoutLastDraftAction, UndoRecoveryError } from "./native/undo.ts";
import { useI18n, NativeUiError, stageText, errorText, message, type Message, HELP_KEY } from './native/i18n.ts';
import { useCallback, useEffect, useRef, useState } from "react";
import {
  advanceNativeWork, appendConfirmedNativeAction, checkoutNativeNode, createNativeCheckpoint, copyNativeNode,
  createNativeProject, deleteNativeSubtree, formatNativeTick, nativeActionsToNode,
  nativePathToNode, nativeSubtreeIds, nearestNativeSnapshotNode, canReuseNativeSnapshot, STANDARD_NATIVE_DECK, DEFAULT_NATIVE_LEVEL,
  updateNativeNodeMetadata, nativeEdgeProgress, nativeEdgeBranch, NATIVE_CARD_CATALOG, configureNativeCatalog, validateNativeDeck, validateNativeForms, validateNativeDeal, validateNativeTower, projectForCurrentEngine, type NativeBattleAction,
  type NativeReplayProject, type NativeWorkingBranch, type NativePlaybackRoute
} from "./domain/nativeReplay.ts";
import { NativeBridge, type BridgeStatus, type CropProfile, type NativeObservation } from "./native/bridge.ts";
import { arenaPixelToWorld } from "./native/geometry.ts";
import NativeSurface from "./native/NativeSurface.tsx";
import NativeNodeTree from "./native/NativeNodeTree.tsx";
import NativeMatchDialog, {type NativeCreationInput} from "./native/NativeMatchDialog.tsx";
import { openNativeProject, readNativeProject, listReplayArchives, saveNativeProject, deleteNativeProject, rememberNativeSession, reopenNativeSession, type SavedReplayEntry } from "./native/files.ts";
import { chooseSavesDirectory, getDefaultSavesDirectory } from "./utils/file.ts";
import { clearNativeJournal, readNativeJournal, writeNativeJournal } from "./native/journal.ts";
import "./native.css";

const EMPTY_PROFILE: CropProfile = {
  id: "stock-v15-1080x1920", nativeWidth: 1080, nativeHeight: 1920,
  enemyHud: [0, 0, 1080, 282], arena: [54, 282, 972, 1384], myHud: [0, 1666, 1080, 254],
  world: [111, 436, 856, 1230], handCentersX: [211, 354, 497, 640], enemyHandReversed: true
};

function desktopScale():number {
  // Keep the desktop proportions on short or DPI-scaled windows instead of
  // letting fixed-size hands and controls squeeze only the arena.
  return Math.min(1,window.innerHeight/960,window.innerWidth/1280);
}

export default function NativeApp() {
  const {t, language, toggle, renderMessage} = useI18n();
  const [uiScale,setUiScale]=useState(desktopScale);
  useEffect(()=>{
    const resize=()=>setUiScale(desktopScale());
    window.addEventListener('resize',resize);
    return ()=>window.removeEventListener('resize',resize);
  },[]);
  const history = useRef(new EditHistory());
  const [historyRevision, setHistoryRevision] = useState(0);
  const refreshHistory = () => setHistoryRevision(value => value + 1);
  const [confirmation,setConfirmation] = useState<{message:Message;resolve:(accepted:boolean)=>void}|null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [screen, setScreen] = useState<"home" | "setup" | "workspace">("home");
  const pendingCreation = useRef<NativeCreationInput | null>(null);
  const [bridge, setBridge] = useState<NativeBridge | null>(null);
  const [status, setStatus] = useState<BridgeStatus | null>(null);
  function acceptStatus(value:BridgeStatus):void {
    configureNativeCatalog(value.engineCatalog,value.identity);
    setStatus(value);
  }
  const [project, setProject] = useState<NativeReplayProject | null>(null);
  const [branch, setBranch] = useState<NativeWorkingBranch | null>(null);
  const [observation, setObservation] = useState<NativeObservation | null>(null);
  const [selected, setSelected] = useState<{ owner: 0 | 1; slot: number } | null>(null);
  const selectionAudio = useRef<AudioContext | null>(null);
  useEffect(() => () => {
    void selectionAudio.current?.close().catch(() => undefined);
    selectionAudio.current = null;
  }, []);
  const [speed, setSpeed] = useState(1);
  const [scrubRoute, setScrubRoute] = useState<NativePlaybackRoute | null>(null);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [notice, setNotice] = useState<Message | string>(message("choose"));
  const [busy, setBusy] = useState(false);
  const [framePending,setFramePending]=useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [acceptSdkTerms, setAcceptSdkTerms] = useState(false);
  const [installDirectory, setInstallDirectory] = useState<string | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archives, setArchives] = useState<SavedReplayEntry[]>([]);
  const [archiveDirectory, setArchiveDirectory] = useState("");
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [matchDialog,setMatchDialog]=useState<'create'|'view'|null>(null);
  const initialized = useRef(false);
  const runtimeStartRequested = useRef(false);
  const runtimePreparation = useRef<Promise<void>|null>(null);
  const busyRef = useRef(false);
  const controlRevision = useRef(0);
  const projectRef = useRef(project);
  const branchRef = useRef(branch);
  const observationRef = useRef(observation);
  const snapshots = useRef(new Map<string, number>());
  const scrubRouteRef = useRef(scrubRoute);
  const connectionErrorRef = useRef<string | null>(null);
  const bridgeRef = useRef<NativeBridge | null>(null);
  const recoveryNode = useRef<string | null>(null);
  const restoreCurrentDraft = useRef(false);

  const interrupt = useCallback((reason: string): void => {
    setFramePending(false);
    if (connectionErrorRef.current) return;
    connectionErrorRef.current = reason;
    setConnectionError(reason);
    setSelected(null);
    const source = projectRef.current;
    const work = branchRef.current;
    if (source && work?.actions.length && !scrubRouteRef.current) {
      void writeNativeJournal(source, work).catch((error) => setNotice(message("journalFailed",{detail:errorText(error,language)})));
    }
    void bridgeRef.current?.post("/v1/suspend").catch(() => undefined);
  }, []);

  function putProject(value: NativeReplayProject | null): void {
    if (value?.projectId !== projectRef.current?.projectId) { history.current.clear(); refreshHistory(); }
    projectRef.current = value;
    setProject(value);
  }
  function putBranch(value: NativeWorkingBranch | null): void {
    branchRef.current = value;
    setBranch(value);
  }
  function putObservation(value: NativeObservation | null): void {
    if(value && busyRef.current)setFramePending(true);
    observationRef.current = value;
    setObservation(value);
  }
  const frameRendered=useCallback((revision:number):void=>{
    if(revision===controlRevision.current && !busyRef.current)setFramePending(false);
  },[]);
  function putScrubRoute(value:NativePlaybackRoute | null): void {
    scrubRouteRef.current = value;
    setScrubRoute(value);
  }
  async function exclusive(work: () => Promise<void>): Promise<void> {
    if (busyRef.current) return;
    busyRef.current = true;
    controlRevision.current++;
    setBusy(true);
    try { await work(); }
    catch (error) {
      setFramePending(false);
      if(!(error instanceof NativeUiError && ['requestFailed','replayFailed','frameFailed'].includes(error.code))) {
        await bridgeRef.current?.recordFailure('desktop-action',error);
      }
      if(error instanceof NativeUiError && error.code==='engineMismatch') {
        setScreen('home');setArchiveOpen(false);
        putObservation(null);initialized.current=false;
      }
      if(error instanceof NativeUiError && ["requestFailed","replayFailed","frameFailed"].includes(error.code))interrupt(t("interrupted"));
      setNotice(error instanceof UndoRecoveryError ? message(error.recovered?"undoRestored":"undoFailed") : message("failed",{detail:errorText(error,language)}));
    }
    finally { controlRevision.current++; busyRef.current = false; setBusy(false); }
  }

  async function rememberSnapshot(client: NativeBridge, nodeId: string, expectedTick: number): Promise<void> {
    const receipt = await client.post<{ handle: number; tick: number }>("/v1/snapshot-create");
    if (!Number.isSafeInteger(receipt.handle) || receipt.handle <= 0 || receipt.tick !== expectedTick) {
      if (Number.isSafeInteger(receipt.handle) && receipt.handle > 0) {
        await client.post("/v1/snapshot-release", { handle: receipt.handle }).catch(() => undefined);
      }
      throw new Error(t("snapshotMismatch"));
    }
    await storeSnapshot(client, nodeId, receipt.handle);
  }

  async function storeSnapshot(client: NativeBridge, nodeId: string, handle: number): Promise<void> {
    const previous = snapshots.current.get(nodeId);
    if (previous && previous !== handle) await client.post("/v1/snapshot-release", { handle: previous }).catch(() => undefined);
    snapshots.current.delete(nodeId);
    snapshots.current.set(nodeId, handle);
    if (snapshots.current.size > 32) {
      const oldest = [...snapshots.current.entries()].find(([id])=>id!==projectRef.current?.rootNodeId);
      if (oldest) {
        snapshots.current.delete(oldest[0]);
        await client.post("/v1/snapshot-release", { handle: oldest[1] }).catch(() => undefined);
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

  async function retainSnapshots(client: NativeBridge, previous: NativeReplayProject | null, next: NativeReplayProject): Promise<void> {
    for (const [id, handle] of snapshots.current) {
      if (previous && canReuseNativeSnapshot(previous, next, id)) continue;
      snapshots.current.delete(id);
      await client.post("/v1/snapshot-release", { handle }).catch(() => undefined);
    }
  }

  async function replayTo(client: NativeBridge, source: NativeReplayProject, nodeId: string,
                          actions: NativeBattleAction[], targetTick: number,
                          expectedDigest?: string): Promise<NativeObservation> {
    setNotice(message("restoring",{time:formatNativeTick(targetTick)}));
    const path = nativePathToNode(source, nodeId);
    const targetNode = expectedDigest && [...path].reverse().find((node) =>
      node.finalTick === targetTick && node.stateDigest === expectedDigest);
    const cacheTarget = async (): Promise<void> => {
      if (targetNode && !snapshots.current.has(targetNode.id)) {
        await rememberSnapshot(client, targetNode.id, targetNode.finalTick).catch(() => undefined);
      }
    };
    const restore = async (base?: (typeof path)[number]): Promise<NativeObservation> => {
      // Cache nodes while already traversing their history. Never replay other
      // branches in the background or make the user visit every ancestor first.
      const missing = path.filter(node => node.finalTick >= (base?.finalTick ?? 0) &&
        node.finalTick <= targetTick && !snapshots.current.has(node.id));
      const root = missing.find(node => node.id === source.rootNodeId);
      const candidates = root ? [root, ...missing.filter(node => node !== root).slice(-31)] :
        missing.slice(-(snapshots.current.has(source.rootNodeId) ? 31 : 32));
      const checkpoints = candidates
        .map(node => ({nodeId:node.id, tick:node.finalTick, digest:node.stateDigest}));
      const {snapshots: receipts = [], ...state} = await client.replay(source.match.seed,
        actions.filter(action => action.tick > (base?.finalTick ?? 0) && action.tick <= targetTick),
        targetTick, expectedDigest, base ? snapshots.current.get(base.id) : undefined,
        base?.finalTick, base?.stateDigest, source.match, checkpoints);
      if (base) {
        const used = snapshots.current.get(base.id)!;
        snapshots.current.delete(base.id);
        snapshots.current.set(base.id, used);
      }
      for (const receipt of receipts) {
        const expected = checkpoints.find(checkpoint => checkpoint.nodeId === receipt.nodeId);
        if (expected && receipt.tick === expected.tick && receipt.digest === expected.digest &&
            Number.isSafeInteger(receipt.handle) && receipt.handle > 0) {
          await storeSnapshot(client, receipt.nodeId, receipt.handle);
        }
      }
      await cacheTarget();
      return state;
    };
    const base = nearestNativeSnapshotNode(source, nodeId, targetTick, new Set(snapshots.current.keys()));
    if (base) {
      try {
        return await restore(base);
      } catch {
        // A new native battle invalidates every process-scoped snapshot handle.
      }
    }
    await releaseSnapshots(client);
    return restore();
  }

  useEffect(() => { void refreshArchives(); }, []);

  useEffect(() => {
    if (screen === "workspace" && observation && !busy && !localStorage.getItem(HELP_KEY)) setHelpOpen(true);
  }, [screen, Boolean(observation), busy]);

  function confirmOperation(prompt:Message):Promise<boolean> {
    return new Promise(resolve=>setConfirmation({message:prompt,resolve}));
  }

  function finishConfirmation(accepted:boolean):void {
    confirmation?.resolve(accepted);
    setConfirmation(null);
  }

  function closeHelp(): void {
    localStorage.setItem(HELP_KEY, "1");
    setHelpOpen(false);
  }

  async function startRuntime(): Promise<void> {
    if(runtimePreparation.current)return runtimePreparation.current;
    const preparation=(async()=>{
      setNotice(message("autoConnectLead"));
      try {
        const client = bridgeRef.current ?? await NativeBridge.launch();
        bridgeRef.current = client;
        const latest = await client.get<BridgeStatus>("/v1/status");
        acceptStatus(latest);
        setBridge(client);
        connectionErrorRef.current = null;
        setConnectionError(null);
        runtimeStartRequested.current = Boolean(latest.environmentConfigured && latest.resourcesConfigured) &&
          latest.setup?.state !== "installing" && latest.setup?.state !== "importing";
        if ((latest.ready && latest.error) || (runtimeStartRequested.current && !latest.ready)) {
          acceptStatus(await client.post<BridgeStatus>("/v1/start"));
        }
      } catch (error) {
        runtimeStartRequested.current = false;
        interrupt(errorText(error,language));
        throw error;
      }
    })();
    runtimePreparation.current=preparation;
    try {await preparation;}
    finally {if(runtimePreparation.current===preparation)runtimePreparation.current=null;}
  }

  function beginCreation():void {
    setMatchDialog('create');
    if(screen==='home'&&!bridgeRef.current&&!connectionErrorRef.current) {
      // Preparing while the user chooses cards overlaps the cold connection
      // without creating a match, a file, or another game process.
      void startRuntime().catch(error=>setNotice(message('failed',{detail:errorText(error,language)})));
    }
  }

  async function showRuntime(): Promise<void> {
    await exclusive(async()=>{if(connectionErrorRef.current && projectRef.current && branchRef.current) {
        // Keep the last confirmed graph available while fixing a broken runtime.
        setFilePath(await saveNativeProject(projectRef.current,filePath));
      } else await preserveWorkspace();
      setScreen("setup");await startRuntime();});
  }

  async function preserveWorkspace(): Promise<void> {
    const source = projectRef.current;
    let work = branchRef.current;
    if (!source || !work) return;
    if (connectionErrorRef.current && observationRef.current) throw new NativeUiError("interrupted");
    if (bridgeRef.current && observationRef.current) {
      const state = await bridgeRef.current.post<NativeObservation>("/v1/pause");
      const edgeId=scrubRouteRef.current?.edgeId;
      work = edgeId?nativeEdgeBranch(source,edgeId,state.tick):advanceNativeWork(work, state.tick);
      putObservation(state); putBranch(work);
      setStatus(previous => previous ? { ...previous, playing: false } : previous);
    }
    // Retain the draft as a journal; returning home never creates a checkpoint.
    const path = await saveNativeProject(source, filePath);
    if (!scrubRouteRef.current && work.actions.length) await writeNativeJournal(source, work);
    setFilePath(path);
    rememberNativeSession(path, work.checkedOutNodeId);
  }

  async function returnHome(): Promise<void> {
    await exclusive(async () => {
      await preserveWorkspace();
      pendingCreation.current = null;
      setSelected(null); setMatchDialog(null); setScreen("home");
      await refreshArchives();
    });
  }

  useEffect(() => {
    if (filePath && branch?.checkedOutNodeId) rememberNativeSession(filePath, branch.checkedOutNodeId);
  }, [filePath, branch?.checkedOutNodeId]);

  useEffect(() => {
    if (!bridge || (screen === "home" && matchDialog !== 'create')) return;
    let alive = true;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing || busyRef.current) return;
      refreshing = true;
      try {
        const revision = controlRevision.current;
        const latest = await bridge.get<BridgeStatus>("/v1/status");
        if (!alive || busyRef.current || revision !== controlRevision.current) return;
        configureNativeCatalog(latest.engineCatalog,latest.identity);
        // A completed interval still needs its endpoint observation before the
        // UI can stop or continue. Keep Pause available during that short gap.
        setStatus(previous => {
          const next=scrubRouteRef.current?.edgeId && latest.playback?.completed
            ? {...latest,playing:Boolean(previous?.playing)} : latest;
          return JSON.stringify(previous)===JSON.stringify(next)?previous:next;
        });
        if (latest.error) { setNotice(latest.error); interrupt(latest.error); }
        if(screen==='home')return;
        if (!latest.ready && !runtimeStartRequested.current && !connectionErrorRef.current &&
            (screen === "setup" || pendingCreation.current || (projectRef.current && !initialized.current)) &&
            latest.environmentConfigured && latest.resourcesConfigured &&
            latest.setup?.state !== "installing" && latest.setup?.state !== "importing" && latest.setup?.state !== "failed") {
          runtimeStartRequested.current = true;
          acceptStatus(await bridge.post<BridgeStatus>("/v1/start"));
          return;
        }
        if (latest.ready && pendingCreation.current) {
          const input = pendingCreation.current;
          pendingCreation.current = null;
          await newProject(input);
          return;
        }
        if (latest.ready && !initialized.current && !busyRef.current && !connectionErrorRef.current) {
          initialized.current = true;
          await exclusive(async () => {
            let loaded = projectRef.current;
            if (loaded) {
              const original=loaded;
              const journal = await readNativeJournal(original, restoreCurrentDraft.current);
              if(journal?.recoveryProject) {
                setFilePath(await saveNativeProject(journal.recoveryProject,filePath));
              }
              const upgraded=projectForCurrentEngine(journal?.recoveryProject ?? loaded,latest.identity);
              loaded=upgraded.project;
              await retainSnapshots(bridge, projectRef.current, loaded);
              if(journal?.recoveryProject)putProject(journal.recoveryProject);
              const root = loaded.nodes[loaded.rootNodeId];
              const savedNode = loaded.nodes[recoveryNode.current ?? loaded.rootNodeId] ?? root;
              setNotice(message("restoringNode",{title:savedNode.title}));
              let state: NativeObservation;
              const cachedBase = nearestNativeSnapshotNode(loaded, savedNode.id, savedNode.finalTick, new Set(snapshots.current.keys()));
              if (cachedBase) {
                state = await replayTo(bridge, loaded, savedNode.id, nativeActionsToNode(loaded, savedNode.id), savedNode.finalTick, savedNode.stateDigest);
              } else {
                await releaseSnapshots(bridge);
                state = await bridge.replay(loaded.match.seed, [], root.finalTick, root.stateDigest, undefined, undefined, undefined, loaded.match);
                await rememberSnapshot(bridge, root.id, root.finalTick);
                if (savedNode.id !== root.id) {
                  state = await replayTo(bridge, loaded, savedNode.id, nativeActionsToNode(loaded, savedNode.id), savedNode.finalTick, savedNode.stateDigest);
                }
              }
              if(upgraded.upgraded) {
                const path=await saveNativeProject(loaded,null);
                putProject(loaded);setFilePath(path);
              }
              let work=checkoutNativeNode(loaded,savedNode.id);
              if (journal && restoreCurrentDraft.current) {
                state = await replayTo(bridge, loaded, journal.checkedOutNodeId,
                  [...nativeActionsToNode(loaded, journal.checkedOutNodeId), ...journal.actions],
                  journal.workingTick, journal.recoveryDigest);
                work={checkedOutNodeId:journal.checkedOutNodeId,workingTick:journal.workingTick,actions:journal.actions};
                if(upgraded.upgraded || journal.recoveryProject) {
                  if(!work.actions.length && work.workingTick===loaded.nodes[work.checkedOutNodeId].finalTick)await clearNativeJournal(loaded.projectId);
                  else await writeNativeJournal(loaded,work);
                }
              } else {
                await clearNativeJournal(original.projectId);
              }
              putObservation(state);putBranch(work);putProject(loaded);
              putScrubRoute(restoreCurrentDraft.current ? null : {nodeId:savedNode.id,maxTick:savedNode.finalTick});
              recoveryNode.current=null;restoreCurrentDraft.current=false;
              setScreen("workspace");
              setNotice(message("loaded"));
            } else {
              setNotice(message("ready"));
            }
          });
          if (projectRef.current && !observationRef.current) {initialized.current=false;interrupt(t("interrupted"));}
        }
        if (latest.ready && (latest.playing || (scrubRouteRef.current?.edgeId && latest.playback?.completed)) && !busyRef.current && observationRef.current && !connectionErrorRef.current) {
          const state = await bridge.get<NativeObservation>("/v1/observe");
          if (!alive || busyRef.current || revision !== controlRevision.current) return;
          putObservation(state);
          const edgeId=scrubRouteRef.current?.edgeId;
          if(edgeId && projectRef.current) {
            const child=projectRef.current.nodes[edgeId];
            putBranch(nativeEdgeBranch(projectRef.current,edgeId,state.tick));
            if(latest.playback?.completed && latest.playback.targetTick===child.finalTick) {
              await exclusive(()=>arriveAtEdgeEnd(state,true));
            }
            return;
          }
          const work = branchRef.current;
          if (work && state.tick >= work.workingTick) {
            const next = advanceNativeWork(work, state.tick);
            putBranch(next);
            if (projectRef.current && next.actions.length) await writeNativeJournal(projectRef.current, next);
          }
        }
      } catch (error) {
        if (alive) { setNotice(message("failed",{detail:errorText(error,language)})); interrupt(t("interrupted")); }
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 100);
    return () => { alive = false; window.clearInterval(interval); };
  }, [bridge, interrupt, screen, language, status?.ready, matchDialog]);

  async function reconnect(): Promise<void> {
    await exclusive(async () => {
      if(projectRef.current && branchRef.current) {
        setFilePath(await saveNativeProject(projectRef.current,filePath));
        if(!scrubRouteRef.current && branchRef.current.actions.length)await writeNativeJournal(projectRef.current,branchRef.current);
      }
      restoreCurrentDraft.current = !scrubRouteRef.current && Boolean(observationRef.current || branchRef.current?.actions.length);
      recoveryNode.current = branchRef.current?.checkedOutNodeId ?? null;
      snapshots.current.clear();
      initialized.current = false;
      putObservation(null);
      bridgeRef.current = null;
      await startRuntime();
      setNotice(message("recovery"));
    });
  }

  async function configureRuntime(): Promise<void> {
    await exclusive(async () => {
      const [{ open }, { invoke }] = await Promise.all([
        import("@tauri-apps/plugin-dialog"), import("@tauri-apps/api/core")
      ]);
      const directory = await open({ directory: true, multiple: false,
        title: t("chooseRuntime") });
      if (!directory || Array.isArray(directory)) return;
      if (projectRef.current && branchRef.current) {
        setFilePath(await saveNativeProject(projectRef.current,filePath));
        if(!scrubRouteRef.current && branchRef.current.actions.length)await writeNativeJournal(projectRef.current, branchRef.current);
      }
      await invoke("configure_native_runtime", { directory }).catch(error=>{throw new NativeUiError("runtimeFailed",String(error));});
      restoreCurrentDraft.current = !scrubRouteRef.current && Boolean(observationRef.current || branchRef.current?.actions.length);
      recoveryNode.current = branchRef.current?.checkedOutNodeId ?? null;
      snapshots.current.clear();
      initialized.current = false;
      putObservation(null);
      const client = await NativeBridge.launch();
      bridgeRef.current = client;
      runtimeStartRequested.current=true;
      setBridge(client);
      acceptStatus(await client.post<BridgeStatus>("/v1/start"));
      connectionErrorRef.current = null;
      setConnectionError(null);
      setBridge(client);
      setNotice(message("configured"));
    });
  }

  async function exportDiagnostics(): Promise<void> {
    await exclusive(async () => {
      const [{ save }, { invoke }] = await Promise.all([
        import("@tauri-apps/plugin-dialog"), import("@tauri-apps/api/core")
      ]);
      const record = bridgeRef.current ? await bridgeRef.current.get("/v1/diagnostics").catch(() => null) : null;
      const path = await save({ title: t("saveDiagnostics"), defaultPath: "native-replay-diagnostics.json",
        filters: [{ name: t("diagnosticsFile"), extensions: ["json"] }] });
      if (!path) return;
      await invoke("write_project_file", { path, data: JSON.stringify(record ?? {
        schema: "cr-native-diagnostics.v1", ready: false, error: connectionErrorRef.current ?? t("disconnected")
      }, null, 2) });
      setNotice(message("diagnosticsSaved"));
    });
  }

  async function installEnvironment(accepted = acceptSdkTerms): Promise<void> {
    if (!bridgeRef.current) return;
    await exclusive(async () => {
      await bridgeRef.current!.post("/v1/setup/install", { acceptLicense: accepted, directory: installDirectory });
      setNotice(message("installing"));
    });
  }

  async function chooseInstallDirectory(): Promise<void> {
    await exclusive(async () => {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const directory = await open({ directory: true, multiple: false, title: t("chooseInstall") });
      if (typeof directory === "string") setInstallDirectory(directory);
    });
  }

  async function importResources(): Promise<void> {
    if (!bridgeRef.current) return;
    await exclusive(async () => {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const path = await open({ title: t("chooseResources"), multiple: false,
        filters: [{ name: t("resourcesFile"), extensions: ["zip"] }] });
      if (!path || Array.isArray(path)) return;
      if(projectRef.current && branchRef.current) {
        setFilePath(await saveNativeProject(projectRef.current,filePath));
        if(branchRef.current.actions.length)await writeNativeJournal(projectRef.current,branchRef.current);
      }
      restoreCurrentDraft.current = Boolean(observationRef.current || branchRef.current?.actions.length);
      recoveryNode.current=branchRef.current?.checkedOutNodeId ?? null;
      initialized.current=false;runtimeStartRequested.current=false;
      connectionErrorRef.current=null;setConnectionError(null);
      putObservation(null);snapshots.current.clear();
      await bridgeRef.current!.post("/v1/setup/resources", { path });
      setNotice(message("importing"));
    });
  }

  const profile = observation?.cropProfile ?? status?.cropProfile ?? EMPTY_PROFILE;
  const currentNode = project && branch ? project.nodes[branch.checkedOutNodeId] : null;
  const currentEdge = project && scrubRoute?.edgeId ? project.nodes[scrubRoute.edgeId] : null;

  async function newProject(input:NativeCreationInput):Promise<void> {
    restoreCurrentDraft.current = false;
    if(!bridge || !status?.ready) {
      pendingCreation.current = input;
      setMatchDialog(null); setScreen("setup");
      await exclusive(startRuntime);
      return;
    }
    await exclusive(async()=>{
      const {title,deck0,deck1,forms0,forms1,tower0,tower1}=input;
      [deck0,deck1].forEach(validateNativeDeck);
      validateNativeForms(deck0,forms0);validateNativeForms(deck1,forms1);
      [deck0,deck1].forEach(validateNativeDeal);
      [tower0,tower1].forEach(validateNativeTower);
      if(connectionErrorRef.current && observationRef.current)throw new NativeUiError("interrupted");
      const previous=projectRef.current;
      let previousWork=branchRef.current;
      let previousState=observationRef.current;
      if(previous && previousWork && previousState) {
        previousState=await bridge.post<NativeObservation>('/v1/pause');
        previousWork=advanceNativeWork(previousWork,previousState.tick);
        putBranch(previousWork);putObservation(previousState);
        await saveNativeProject(previous,filePath);
        if(!scrubRouteRef.current && previousWork.actions.length)await writeNativeJournal(previous,previousWork);
      }
      await releaseSnapshots(bridge);
      // A fixed verified deal seed keeps native omission rules reproducible.
      const seed=41;
      let state:NativeObservation;
      let next:NativeReplayProject;
      let nextPath:string;
      try {
        setNotice(message("validating"));
        state=await bridge.post<NativeObservation>('/v1/reset',{seed,deck0,deck1,forms0,forms1,tower0,tower1,level:DEFAULT_NATIVE_LEVEL,dealMode:'ordered'});
        next=createNativeProject(status.identity,{seed,preset:[deck0,deck1].every(deck=>JSON.stringify(deck)===JSON.stringify(STANDARD_NATIVE_DECK))&&![...forms0,...forms1].some(Boolean)?'standard':'custom',deck0,deck1,forms0,forms1,tower0,tower1,level:DEFAULT_NATIVE_LEVEL,dealMode:'ordered'},state.tick,state.digest,title);
        await rememberSnapshot(bridge,next.rootNodeId,state.tick);
        nextPath=await saveNativeProject(next,null);
      } catch(error) {
        if(previous && previousWork && previousState) {
          try {
            const restored=await bridge.replay(previous.match.seed,[...nativeActionsToNode(previous,previousWork.checkedOutNodeId),...previousWork.actions],previousWork.workingTick,previousState.digest,undefined,undefined,undefined,previous.match);
            putObservation(restored);putBranch(previousWork);
          } catch {interrupt(t("interrupted"));}
        }
        throw error;
      }
      putProject(next);putBranch(checkoutNativeNode(next,next.rootNodeId));putObservation(state);putScrubRoute(null);
      setFilePath(nextPath);setSelected(null);setMatchDialog(null);
      initialized.current=true; setScreen("workspace");
      setStatus({...status,playing:false});setNotice(message("created"));
    });
  }

  async function refreshArchives(): Promise<void> {
    setArchiveLoading(true);
    setArchiveError(null);
    try {
      setArchiveDirectory(await getDefaultSavesDirectory() ?? "");
      setArchives(await listReplayArchives());
    } catch (error) { setArchiveError(errorText(error,language)); }
    finally { setArchiveLoading(false); }
  }

  async function showArchives(): Promise<void> {
    if (bridgeRef.current && status?.playing) {
      await exclusive(async () => {
        const state = await bridgeRef.current!.post<NativeObservation>("/v1/pause");
        putObservation(state);
        const edgeId=scrubRouteRef.current?.edgeId;
        if (branchRef.current) putBranch(edgeId?nativeEdgeBranch(projectRef.current!,edgeId,state.tick):advanceNativeWork(branchRef.current, state.tick));
        if(edgeId && state.tick===projectRef.current!.nodes[edgeId].finalTick)await arriveAtEdgeEnd(state,false);
        setStatus({ ...status, playing: false });
      });
    }
    setArchiveOpen(true);
    await refreshArchives();
  }

  async function changeArchiveDirectory(): Promise<void> {
    try { if (await chooseSavesDirectory()) await refreshArchives(); }
    catch (error) { setArchiveError(errorText(error,language)); }
  }

  async function removeArchive(entry: SavedReplayEntry): Promise<void> {
    if (busyRef.current || confirmation) return;
    const directory = archiveDirectory;
    if (!await confirmOperation(message('confirmDeleteArchive', {title:entry.title,name:entry.name}))) return;
    await exclusive(async () => {
      try {
        await deleteNativeProject(entry, directory);
        // Drop the loaded copy so a later save/navigation cannot recreate the file.
        if (filePath?.replace(/\\/g,'/').toLowerCase() === entry.path.replace(/\\/g,'/').toLowerCase()) {
          putProject(null);putBranch(null);putObservation(null);putScrubRoute(null);
          initialized.current=false;recoveryNode.current=null;restoreCurrentDraft.current=false;
          pendingCreation.current=null;setFilePath(null);setSelected(null);setFramePending(false);
          setArchiveOpen(false);setScreen('home');
          if (bridgeRef.current) await releaseSnapshots(bridgeRef.current);
        }
        await refreshArchives();
        setNotice(message('archiveDeleted',{title:entry.title}));
      } catch (error) { setArchiveError(errorText(error,language)); }
    });
  }

  async function openProject(path?: string, continueSession = false): Promise<void> {
    await exclusive(async () => {
      const opened = continueSession ? await reopenNativeSession() : path ? await readNativeProject(path) : await openNativeProject();
      if (!opened) return;
      await preserveWorkspace();
      if (bridgeRef.current) {
        await retainSnapshots(bridgeRef.current, projectRef.current, opened.project);
      }
      const nodeId = "nodeId" in opened && typeof opened.nodeId === "string" ? opened.nodeId : opened.project.rootNodeId;
      putProject(opened.project); putBranch(checkoutNativeNode(opened.project, nodeId));
      restoreCurrentDraft.current = false;
      recoveryNode.current = nodeId;
      initialized.current = false;
      putObservation(null); putScrubRoute(null);
      setFilePath(opened.path); setSelected(null); setArchiveOpen(false);
      setScreen("setup");
      await startRuntime();
    });
  }

  async function saveProject(): Promise<void> {
    if (!projectRef.current) return;
    if(!scrubRouteRef.current && bridge && status?.ready && branchRef.current && !connectionErrorRef.current && (status.playing || branchRef.current.workingTick>projectRef.current.nodes[branchRef.current.checkedOutNodeId].finalTick)) {
      await checkpoint();return;
    }
    await exclusive(async () => {
      const path = await saveNativeProject(projectRef.current!, filePath);
      setFilePath(path);
      setNotice(message("saved",{path}));
    });
  }

  async function checkpoint(): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current || scrubRouteRef.current) return;
    await exclusive(async () => {
      const state = await bridge.post<NativeObservation>("/v1/pause");
      putObservation(state);
      const work = advanceNativeWork(branchRef.current!, state.tick);
      if(status)setStatus({...status,playing:false});
      if(work.workingTick===projectRef.current!.nodes[work.checkedOutNodeId].finalTick) {
        setFilePath(await saveNativeProject(projectRef.current!,filePath));setNotice(message("nodeSaved"));return;
      }
      const updated=Boolean(projectRef.current!.nodes[work.checkedOutNodeId].editableCopy);
      const result = createNativeCheckpoint(projectRef.current!, work, state.digest);
      await rememberSnapshot(bridge, result.node.id, state.tick);
      const savedPath = await saveNativeProject(result.project, filePath);
      if (!updated) { history.current.push(structureEntry(projectRef.current!, result.project, work, state.digest)); refreshHistory(); }
      putProject(result.project);
      putBranch(result.branch);
      putScrubRoute(null);
      setFilePath(savedPath);
      await clearNativeJournal(result.project.projectId);
      setNotice(message("checkpointSaved",{kind:{key:updated?"updatedNode":"savedChild"},time:formatNativeTick(state.tick)}));
    });
  }

  // Called inside one exclusive action, including when selecting an edge.
  async function restoreNode(nodeId:string):Promise<void> {
      const client=bridgeRef.current!;
      const work=branchRef.current!;
      let activeProject = projectRef.current!;
      if (!scrubRouteRef.current && work.workingTick>activeProject.nodes[work.checkedOutNodeId].finalTick) {
        const state = await client.post<NativeObservation>("/v1/pause");
        const saved = createNativeCheckpoint(activeProject, advanceNativeWork(work, state.tick), state.digest);
        await rememberSnapshot(client, saved.node.id, state.tick);
        const savedPath = await saveNativeProject(saved.project, filePath);
        activeProject = saved.project;
        putProject(activeProject);
        putBranch(saved.branch);
        putObservation(state);
        setFilePath(savedPath);
        await clearNativeJournal(activeProject.projectId);
      }
      setStatus(previous=>previous?{...previous,playing:false}:previous);
      const node = activeProject.nodes[nodeId];
      const state = await replayTo(client, activeProject, nodeId,
        nativeActionsToNode(activeProject, nodeId), node.finalTick, node.stateDigest);
      putObservation(state);
      putBranch(checkoutNativeNode(activeProject, nodeId));
      putScrubRoute({nodeId:node.id,maxTick:node.finalTick});
      setSelected(null);
      setNotice(message("restoredNode",{title:node.title}));
  }

  async function checkout(nodeId: string): Promise<void> {
    if (!bridge || !projectRef.current?.nodes[nodeId] || !branchRef.current) return;
    await exclusive(async()=>{
      await restoreNode(nodeId);
      await continueFromNode();
    });
  }

  async function continueFromNode():Promise<void> {
    if(observationRef.current!.finalized)return;
    await bridgeRef.current!.post('/v1/resume');
    putScrubRoute(null);
    setStatus(previous=>previous?{...previous,playing:true}:previous);
    setNotice(message('simulatingNode',{title:projectRef.current!.nodes[branchRef.current!.checkedOutNodeId].title}));
  }

  async function beginEdge(childId:string,before:NativeObservation):Promise<void> {
    const source=projectRef.current!,target=source.nodes[childId],parent=source.nodes[target.parentId!];
    if(before.tick<parent.finalTick || before.tick>target.finalTick ||
       (before.tick===parent.finalTick && before.digest!==parent.stateDigest))throw new NativeUiError('replayFailed',t('edgeBaseMismatch'));
    const state=await bridgeRef.current!.post<NativeObservation&{playing:boolean}>('/v1/playback',{
      actions:target.actionsFromParent.filter(action=>action.tick>before.tick),targetTick:target.finalTick,
      expectedTick:before.tick,baseDigest:before.digest,expectedDigest:target.stateDigest});
    putScrubRoute({nodeId:target.id,maxTick:target.finalTick,edgeId:target.id});
    putObservation(state);putBranch(nativeEdgeBranch(source,target.id,state.tick));setSelected(null);
    setStatus(previous=>previous?{...previous,playing:state.playing}:previous);
    setNotice(message('playingEdge',{from:parent.title,to:target.title}));
  }

  async function arriveAtEdgeEnd(state:NativeObservation,continueSimulation:boolean):Promise<void> {
    const source=projectRef.current!,child=source.nodes[scrubRouteRef.current!.edgeId!];
    if(state.tick!==child.finalTick || state.digest!==child.stateDigest)throw new NativeUiError('replayFailed',t('edgeBaseMismatch'));
    putBranch(checkoutNativeNode(source,child.id));
    const waitForChoice=child.childIds.length>1;
    putScrubRoute(waitForChoice||state.finalized?{nodeId:child.id,maxTick:child.finalTick}:null);
    setStatus(previous=>previous?{...previous,playing:false}:previous);
    if(continueSimulation && !waitForChoice && !state.finalized) {
      await continueFromNode();
    } else {
      setNotice(message(state.finalized?'replayEnded':waitForChoice?'chooseEdge':'edgeArrived',{title:child.title}));
    }
  }

  async function playEdge(childId:string):Promise<void> {
    const child=projectRef.current?.nodes[childId];
    if(!bridge||!child?.parentId||busyRef.current)return;
    await exclusive(async()=>{
      if(scrubRouteRef.current?.edgeId===childId && observationRef.current!.tick<child.finalTick) {
        if(!status?.playing)await beginEdge(childId,observationRef.current!);
        return;
      }
      const parent=projectRef.current!.nodes[child.parentId!],before=observationRef.current;
      if(status?.playing || !before || before.tick!==parent.finalTick || before.digest!==parent.stateDigest)await restoreNode(parent.id);
      await beginEdge(childId,observationRef.current!);
    });
  }

  async function togglePlay(): Promise<void> {
    if (!bridge || !status?.ready) return;
    await exclusive(async () => {
      if (status.playing) {
        setStatus({ ...status, playing: false });
        let state:NativeObservation;
        try { state = await bridge.post<NativeObservation>("/v1/pause"); }
        catch(error) { interrupt(t("interrupted")); throw error; }
        putObservation(state);
        setStatus({ ...status, playing: false });
        if (branchRef.current) {
          const edgeId=scrubRouteRef.current?.edgeId;
          const work = edgeId&&projectRef.current?nativeEdgeBranch(projectRef.current,edgeId,state.tick):advanceNativeWork(branchRef.current, state.tick);
          if (!scrubRouteRef.current && projectRef.current && work.actions.length) await writeNativeJournal(projectRef.current, work);
          putBranch(work);
          if(edgeId && state.tick===projectRef.current!.nodes[edgeId].finalTick)await arriveAtEdgeEnd(state,false);
        }
      } else {
        const route=scrubRouteRef.current,source=projectRef.current,work=branchRef.current;
        if(route?.edgeId && observationRef.current && observationRef.current.tick<route.maxTick) {
          await beginEdge(route.edgeId,observationRef.current);
          return;
        } else {
          const node=source&&work?source.nodes[work.checkedOutNodeId]:null;
          if(route && node) {
            if(!observationRef.current!.finalized && node.childIds.length<=1)await continueFromNode();
            else setNotice(message(observationRef.current!.finalized?'replayEnded':'chooseEdge',{title:node.title}));
            return;
          }
          await bridge.post("/v1/resume");
          putScrubRoute(null);
        }
        setStatus({ ...status, playing: true });
      }
    });
  }

  async function stepForward(): Promise<void> {
    if (!bridge || !branchRef.current || !observationRef.current || observationRef.current.finalized || status?.playing || framePending) return;
    const beforeTick=observationRef.current.tick;
    const route=scrubRouteRef.current;
    const node=projectRef.current?.nodes[branchRef.current.checkedOutNodeId];
    if(route && !route.edgeId && node && beforeTick===node.finalTick && node.childIds.length>1) {
      setNotice(message('chooseEdge',{title:node.title}));
      return;
    }
    if(route?.edgeId && beforeTick<route.maxTick) {
      await seek(beforeTick+1);
      return;
    }
    await exclusive(async () => {
      const state = await bridge.post<NativeObservation>("/v1/step", { ticks: 1,expectedTick:beforeTick });
      if(state.tick!==beforeTick+1) {
        interrupt(t("interrupted"));
        throw new Error(t("stepMismatch"));
      }
      putObservation(state);
      const work = advanceNativeWork(branchRef.current!, state.tick);
      if (projectRef.current && work.actions.length) await writeNativeJournal(projectRef.current, work);
      putBranch(work);
      putScrubRoute(null);
      if (status) setStatus({ ...status, playing: false });
      setNotice(message("stepped",{time:formatNativeTick(state.tick)}));
    });
  }

  async function seek(targetTick: number,destinationId?:string): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current || !observationRef.current) return;
    await exclusive(async () => {
      let source = projectRef.current!;
      const work = branchRef.current!;
      let route = destinationId?{nodeId:destinationId,maxTick:source.nodes[destinationId].finalTick}:scrubRouteRef.current;
      const rootTick = source.nodes[source.rootNodeId].finalTick;
      if (!Number.isSafeInteger(targetTick) || targetTick < rootTick ||
          targetTick > (route?.maxTick ?? work.workingTick)) {
        throw new Error(t("seekInvalid"));
      }
      if (!route) {
        if(targetTick===work.workingTick)return;
        if (work.workingTick>source.nodes[work.checkedOutNodeId].finalTick) {
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
      const atNode=path.some(node=>node.finalTick===targetTick);
      const edge=atNode?null:path.find(node=>node.parentId&&source.nodes[node.parentId].finalTick<targetTick&&targetTick<node.finalTick);
      putScrubRoute({nodeId:route.nodeId,maxTick:route.maxTick,...(edge?{edgeId:edge.id}:{})});
      if (status) setStatus({ ...status, playing: false });
      setSelected(null);
      setNotice(message("sought",{time:formatNativeTick(state.tick)}));
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
    if(scrubRouteRef.current || connectionErrorRef.current || busyRef.current || framePending || !status?.ready)return;
    const player=observationRef.current?.players.find(player=>player.owner===owner);
    const card=player?.hand.find(card=>card.slot===slot);
    if(!player||!card||player.elixir<card.cost*10000)return;
    setSelected({owner,slot});
    // Start audio from the click gesture so browser autoplay rules permit it.
    try {
      const audio=selectionAudio.current??(selectionAudio.current=new AudioContext());
      if(audio.state==='suspended')void audio.resume().catch(()=>undefined);
      const tone=audio.createOscillator(),volume=audio.createGain(),now=audio.currentTime;
      tone.type='triangle';
      tone.frequency.setValueAtTime(520,now);
      tone.frequency.exponentialRampToValueAtTime(880,now+.075);
      volume.gain.setValueAtTime(0,now);
      volume.gain.linearRampToValueAtTime(.055,now+.008);
      volume.gain.exponentialRampToValueAtTime(.001,now+.11);
      volume.gain.linearRampToValueAtTime(0,now+.12);
      tone.connect(volume);
      volume.connect(audio.destination);
      tone.onended=()=>{tone.disconnect();volume.disconnect();};
      tone.start(now);
      tone.stop(now+.12);
    } catch {
      // Audio availability must not interrupt card selection.
    }
    setNotice(message("selectedCard",{side:{key:owner===1?"own":"enemy"},slot:slot+1}));
  }

  async function copyNode():Promise<void> {
    if(!bridge || !projectRef.current || !branchRef.current)return;
    await exclusive(async()=>{
      const state=await bridge.post<NativeObservation>('/v1/pause');
      const beforeProject=projectRef.current!;
      const edgeId=scrubRouteRef.current?.edgeId;
      const beforeWork=edgeId?nativeEdgeBranch(beforeProject,edgeId,state.tick):advanceNativeWork(branchRef.current!,state.tick);
      let source=beforeProject,work=beforeWork;
      if(!edgeId && source.nodes[work.checkedOutNodeId].editableCopy && work.workingTick>source.nodes[work.checkedOutNodeId].finalTick) {
        const saved=createNativeCheckpoint(source,work,state.digest);
        source=saved.project;work=saved.branch;
        await rememberSnapshot(bridge,saved.node.id,state.tick);
      }
      const copied=copyNativeNode(source,work,state.digest);
      await rememberSnapshot(bridge,copied.node.id,state.tick);
      setFilePath(await saveNativeProject(copied.project,filePath));
      history.current.push(structureEntry(beforeProject,copied.project,beforeWork,state.digest,scrubRouteRef.current??undefined)); refreshHistory();
      putProject(copied.project);putBranch(copied.branch);putObservation(state);putScrubRoute(null);setSelected(null);
      await clearNativeJournal(copied.project.projectId);
      if(status)setStatus({...status,playing:false});
      setNotice(message("copied"));
    });
  }

  function moveNode(id:string,position:{x:number;y:number}):void {
    if(busyRef.current || !projectRef.current)return;
    const source=projectRef.current;
    const before=source.layout[id]??{x:0,y:0};
    if(before.x===position.x && before.y===position.y)return;
    void exclusive(async()=>{
      const next=updateNativeNodeMetadata(source,id,{position});
      const path=await saveNativeProject(next,filePath);
      history.current.push({kind:"position",nodeId:id,before});refreshHistory();
      putProject(next);setFilePath(path);
    });
  }

  async function playAt(pixel: { x: number; y: number }): Promise<void> {
    if (scrubRouteRef.current || connectionErrorRef.current || busyRef.current || framePending || !status?.ready) return;
    if (!selected || !bridge || !projectRef.current || !branchRef.current) return;
    const player=observationRef.current?.players.find(player=>player.owner===selected.owner);
    const card=player?.hand.find(card=>card.slot===selected.slot);
    if(!player||!card||player.elixir<card.cost*10000) { setSelected(null); return; }
    const world = arenaPixelToWorld(profile, pixel);
    await exclusive(async () => {
      const wasPlaying = status?.playing ?? false;
      const receipt = await bridge.post<{
        accepted: boolean; observation: NativeObservation; action?: Omit<NativeBattleAction, "id">;
      }>("/v1/play-card", { owner: selected.owner, slot: selected.slot, ...world,
        expectedTick:wasPlaying?undefined:observationRef.current?.tick });
      putObservation(receipt.observation);
      let work = advanceNativeWork(branchRef.current!, receipt.observation.tick);
      if (receipt.accepted && receipt.action) {
        work = appendConfirmedNativeAction(projectRef.current!, work,
          { ...receipt.action, id: crypto.randomUUID() });
        history.current.push({kind:"action",nodeId:work.checkedOutNodeId,actionId:work.actions[work.actions.length-1].id});refreshHistory();
        putBranch(work);
        try { await writeNativeJournal(projectRef.current!, work); }
        catch (error) { interrupt(t("interrupted")); throw error; }
        setNotice(message("cardConfirmed",{side:{key:selected.owner===1?"own":"enemy"}}));
      } else if (work.actions.length) {
        await writeNativeJournal(projectRef.current!, work);
      } else {
        setNotice(message("cardRejected"));
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
    if (busyRef.current || !projectRef.current || !branchRef.current) return;
    const id=branchRef.current.checkedOutNodeId;
    const node=projectRef.current.nodes[id];
    for(const field of ["title","note"] as const) {
      if(patch[field]!==undefined && patch[field]!==node[field]) {
        history.current.push({kind:"metadata",nodeId:id,field,before:node[field],merge:`${id}:${field}`});refreshHistory();
      }
    }
    putProject(updateNativeNodeMetadata(projectRef.current,id,patch));
  }

  async function activateAbility(owner:0|1,entityId:number,abilityId:number):Promise<void> {
    if (scrubRouteRef.current || !bridge || !ready || framePending || !projectRef.current || !branchRef.current) return;
    await exclusive(async()=>{
      const receipt=await bridge.post<{accepted:boolean;observation:NativeObservation;action?:Omit<NativeBattleAction,'id'>}>('/v1/activate-ability',{owner,entityId,abilityId});
      putObservation(receipt.observation);
      let work=advanceNativeWork(branchRef.current!,receipt.observation.tick);
      if(receipt.accepted && receipt.action) {
        work=appendConfirmedNativeAction(projectRef.current!,work,{...receipt.action,id:crypto.randomUUID()});
        putBranch(work);
        history.current.push({kind:"action",nodeId:work.checkedOutNodeId,actionId:work.actions[work.actions.length-1].id});refreshHistory();
        try { await writeNativeJournal(projectRef.current!,work); } catch(error) {interrupt(t("interrupted"));throw error;}
        setNotice(message("abilityConfirmed"));
      } else setNotice(message("abilityRejected"));
      putBranch(work);putScrubRoute(null);setSelected(null);
      if(status)setStatus({...status,playing:false});
    });
  }

  async function removeNode(): Promise<void> {
    if (!bridge || !projectRef.current || !branchRef.current || scrubRouteRef.current?.edgeId) return;
    const id = branchRef.current.checkedOutNodeId;
    const node = projectRef.current.nodes[id];
    if (!node.parentId || !await confirmOperation(message("confirmDelete",{title:node.title}))) return;
    const parentId = node.parentId;
    await exclusive(async () => {
      const source = projectRef.current!;
      const paused = await bridge.post<NativeObservation>("/v1/pause");
      const beforeWork = advanceNativeWork(branchRef.current!,paused.tick);
      putBranch(beforeWork);putObservation(paused);
      setStatus(previous=>previous?{...previous,playing:false}:previous);
      const removed = nativeSubtreeIds(source,id);
      const next = deleteNativeSubtree(source,id);
      const after = checkoutNativeNode(next,parentId);
      const state = await runUndoRestore(source,beforeWork,paused,after,next,next.nodes[parentId].stateDigest,
        async(p)=>{await saveNativeProject(p,filePath);});
      history.current.push(structureEntry(source,next,beforeWork,paused.digest,scrubRouteRef.current??undefined));refreshHistory();
      putProject(next);putBranch(after);putObservation(state);putScrubRoute({nodeId:parentId,maxTick:next.nodes[parentId].finalTick});setSelected(null);
      setNotice(message("deleted",{count:removed.length}));
    });
  }

  async function runUndoRestore(source:NativeReplayProject, before:NativeWorkingBranch, paused:NativeObservation,
    after:NativeWorkingBranch,target=source,digest?:string,persist?:(project:NativeReplayProject)=>Promise<void>):Promise<NativeObservation> {
    try {
      // Write ahead the original metadata and confirmed state. This bridges the two
      // independently atomic project/journal files without changing project format.
      await writeNativeJournal(source,before,{project:source,digest:paused.digest});
      let replayCount = 0;
      return await restoreUndoBranch(source,before,paused,after,
        async (p,w,expected)=> {
          const actions=[...nativeActionsToNode(p,w.checkedOutNodeId),...w.actions];
          if(replayCount++ === 0) return replayTo(bridgeRef.current!,p,w.checkedOutNodeId,actions,w.workingTick,expected);
          // Each replay restores and validates the immutable base snapshot again.
          // Do not swallow a failed undo replay: rollback must keep the original branch.
          const base=nearestNativeSnapshotNode(p,w.checkedOutNodeId,w.workingTick,new Set(snapshots.current.keys()));
          if(base)return bridgeRef.current!.replay(p.match.seed,actions.filter(action=>action.tick>base.finalTick),w.workingTick,
            expected,snapshots.current.get(base.id),base.finalTick,base.stateDigest,p.match);
          await releaseSnapshots(bridgeRef.current!);
          return bridgeRef.current!.replay(p.match.seed,actions,w.workingTick,expected,undefined,undefined,undefined,p.match);
        },async(p,w)=>{
          if(!w.actions.length && w.workingTick===p.nodes[w.checkedOutNodeId].finalTick)await clearNativeJournal(p.projectId);
          else await writeNativeJournal(p,w);
        },target,digest,persist);
    } catch(error) {
      if(error instanceof UndoRecoveryError && !error.recovered) interrupt(t("undoFailed"));
      throw error;
    }
  }

  async function undo():Promise<void> {
    if(screen!=="workspace" || busyRef.current || !projectRef.current || !branchRef.current || connectionErrorRef.current)return;
    const entry=history.current.peek();if(!entry)return;
    history.current.endText();
    await exclusive(async()=>{
      const source=projectRef.current!;
      if(entry.kind==="metadata" || entry.kind==="position") {
        if(bridge && branchRef.current) {
          const state=await bridge.post<NativeObservation>("/v1/pause");
          const edgeId=scrubRouteRef.current?.edgeId;
          const work=edgeId?nativeEdgeBranch(source,edgeId,state.tick):advanceNativeWork(branchRef.current,state.tick);
          putObservation(state);putBranch(work);setStatus(previous=>previous?{...previous,playing:false}:previous);
          if(edgeId && state.tick===source.nodes[edgeId].finalTick)await arriveAtEdgeEnd(state,false);
          if(!scrubRouteRef.current && work.actions.length)await writeNativeJournal(source,work);
        }
        if(!source.nodes[entry.nodeId])throw new Error(t("undoBlocked"));
        const next=updateNativeNodeMetadata(source,entry.nodeId,entry.kind==="position"?{position:entry.before}:{[entry.field]:entry.before});
        setFilePath(await saveNativeProject(next,filePath));
        putProject(next);
      } else {
        if(!bridge || !observationRef.current) return;
        let target=source,after:NativeWorkingBranch,digest:string|undefined;
        try {
          if(entry.kind==="action") {
            if(entry.nodeId!==branchRef.current!.checkedOutNodeId || scrubRouteRef.current)throw new Error('UNDO_SAVED_HISTORY');
            after=withoutLastDraftAction(source,branchRef.current!,entry.actionId);
          } else {
            target=undoStructure(source,entry);
            after=entry.branch;digest=entry.digest;
          }
        } catch {history.current.pop();refreshHistory();setNotice(message("undoBlocked"));return;}
        let paused:NativeObservation;
        try {paused=await bridge.post<NativeObservation>("/v1/pause");} catch(error) {interrupt(t("interrupted"));throw error;}
        const before=advanceNativeWork(branchRef.current!,paused.tick);
        putBranch(before);putObservation(paused);setStatus(previous=>previous?{...previous,playing:false}:previous);
        if(entry.kind==="action")after={...after,workingTick:paused.tick};
        const state=await runUndoRestore(source,before,paused,after,target,digest,
          entry.kind==="structure"?async(p)=>{await saveNativeProject(p,filePath);}:undefined);
        putProject(target);putBranch(after);putObservation(state);
        putScrubRoute(entry.kind==='structure'?entry.replay??null:null);setSelected(null);
        if(entry.kind==='structure' && entry.replay)await clearNativeJournal(target.projectId);
      }
      history.current.pop();refreshHistory();setNotice(message("undone"));
    });
  }

  useEffect(()=>{
    const keydown=(event:KeyboardEvent)=>{
      if (confirmation && event.key === 'Escape') { event.preventDefault();finishConfirmation(false);return; }
      if((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==="z" && !event.shiftKey && !isTextUndoTarget(event.target) && screen==="workspace" && !helpOpen && !matchDialog && !archiveOpen && !confirmation) {
        event.preventDefault();void undo();
      }
    };
    window.addEventListener("keydown",keydown);
    return ()=>window.removeEventListener("keydown",keydown);
  });

  const ownElixir = observation?.players.find((player) => player.owner === 1)?.elixir;
  const enemyElixir = observation?.players.find((player) => player.owner === 0)?.elixir;
  const ready = Boolean(screen === "workspace" && status?.ready && observation && !connectionError);
  // A saved checkpoint and its paused draft share the same verified picture.
  // Tick/digest changes still force a fresh capture before controls are enabled.
  const nodeFrameKey = project && observation && !status?.playing
    ? JSON.stringify([project.projectId,project.engine,observation.tick,observation.digest,profile.id,profile.world,profile.arena]) : null;
  const connectingRuntime = Boolean(runtimeStartRequested.current && !status?.ready && !connectionError);
  const restoringBattle = Boolean(status?.ready && project && !observation);
  const connectionReady = Boolean(status?.ready && !restoringBattle);
  const settingUp = status?.setup?.state === "installing" || status?.setup?.state === "importing";

  const archiveContents = <>
      <div className="archive-tools"><button onClick={()=>{void refreshArchives();}} disabled={archiveLoading}>{t("refresh")}</button><button onClick={()=>{void changeArchiveDirectory();}} disabled={busy}>{t("folder")}</button><button onClick={()=>{void openProject();}} disabled={busy}>{t("otherFile")}</button></div>
      <div className="archive-directory">{archiveDirectory}</div>
      {archiveError && <p role="alert">{archiveError}</p>}
      <div className="archive-list">{archiveLoading ? <p>{t("loading")}</p> : !archives.length ? <p>{t("empty")}</p> : archives.map(entry=><article className="archive-entry" key={entry.path}><div><strong>{entry.title}</strong><span>{entry.nodeCount} {t("nodes")} · {entry.updatedAt ? new Date(entry.updatedAt).toLocaleString(language) : "—"}</span><small>{entry.name}</small></div><div className="archive-entry-actions"><button disabled={busy||Boolean(confirmation)} onClick={()=>{void openProject(entry.path);}}>{t("open")}</button><button className="archive-delete" disabled={busy||Boolean(confirmation)} onClick={()=>{void removeArchive(entry);}}>{t("deleteArchive")}</button></div></article>)}</div>
  </>;

  return <div className="native-app" style={{zoom:uiScale,height:`${100/uiScale}vh`}}>
    <header className="native-header">
      <div className="native-brand"><img className="native-brand-icon" src="/app-icon.png" alt="" />
        <div><strong>{t("brand")}</strong><small>{t("subtitle")}</small></div></div>
      <div className="native-header-actions">
        {screen === "workspace" && <button title="Ctrl+Z" onClick={()=>{void undo();}} disabled={busy || !ready || !history.current.peek()} data-history-revision={historyRevision}>{t("undo")}</button>}
        <button onClick={toggle} aria-label="Language">{language === "zh-CN" ? "English" : "简体中文"}</button>
        <button onClick={()=>setHelpOpen(true)}>{t("help")}</button>
        {screen !== "home" && <button onClick={()=>{void returnHome();}} disabled={busy}>{t("home")}</button>}
        {screen === "home" && <button onClick={()=>{void openProject(undefined, true);}} disabled={busy || !localStorage.getItem("clash-native.last-session")}>{t("continue")}</button>}
        <button onClick={beginCreation} disabled={busy}>{t("new")}</button>
        {screen === "workspace" && <button onClick={() => { void showArchives(); }} disabled={busy}>{t("archives")}</button>}
        {screen === "workspace" && <button onClick={()=>setMatchDialog('view')} disabled={!project||busy}>{t("config")}</button>}
        {screen !== "setup" && <button onClick={() => { void showRuntime(); }} disabled={busy}>{t("runtime")}</button>}
        {screen === "workspace" && <button className="primary" onClick={() => { void saveProject(); }} disabled={!project || busy}>{t("save")}</button>}
      </div>
    </header>
    {screen === "home" ? <main className="native-home">
      <h1>{t("archiveTitle")}</h1><p>{t("homeLead")}</p>
      {archiveContents}
      <div className="native-notice" role="status">{renderMessage(notice)}</div>
    </main> : <div className={`native-workspace${screen === "setup" ? " native-setup" : ""}`}>
      <aside className="native-sidebar">
        <div className="panel-heading"><span>{t("branch")}</span><span>{project ? Object.keys(project.nodes).length : 0} {t("nodes")}</span></div>
        <div className="native-project-title">{project ? <input aria-label={t("projectName")} value={project.title} disabled={!ready||busy}
          onChange={(event) => putProject({ ...project, title: event.target.value, updatedAt: new Date().toISOString() })} /> : t("waiting")}</div>
        {project && branch ? <NativeNodeTree project={project} selectedId={scrubRoute?.edgeId?null:branch.checkedOutNodeId} disabled={!ready||busy} onSelect={id=>{void checkout(id);}} onMove={moveNode}
          onSelectEdge={id=>{void playEdge(id);}} speed={speed} activeEdge={scrubRoute?.edgeId&&observation?{childId:scrubRoute.edgeId,progress:nativeEdgeProgress(project,scrubRoute.edgeId,observation.tick),playing:Boolean(status?.playing)}:null}/> : <div className="native-tree"/>}
        <button className="wide-button" onClick={()=>{void copyNode();}} disabled={!ready||busy}>{t("copy")}</button>
        <button className="wide-button" onClick={() => { void checkpoint(); }}
          disabled={!ready || busy || Boolean(scrubRoute) || !branch || !currentNode || branch.workingTick <= currentNode.finalTick}>
          {t("checkpoint")}
        </button>
        <button className="wide-button danger-button" onClick={() => { void removeNode(); }} disabled={!ready || !currentNode?.parentId || busy || Boolean(scrubRoute?.edgeId)}>{t("delete")}</button>
        {!scrubRoute && branch && branch.actions.length > 0 && <div className="draft-note">● {t("draft")} · {branch.actions.length} {t("confirmedActions")}</div>}
      </aside>
      <main className="native-main">
        <div className="battle-toolbar">
          <div className="battle-title"><strong>{t("battle")}</strong><span>{stageText(language,status?.stageCode,status?.stage ?? t("bridge"))}</span></div>
          <div className="battle-clock">{formatNativeTick(observation?.tick ?? 0)}</div>
          <div className="battle-elixir" aria-label={t("elixirReading")}>
            {t("enemy")} {enemyElixir === undefined ? "—" : (enemyElixir / 10000).toFixed(1)}
            <span>·</span>
            {t("own")} {ownElixir === undefined ? "—" : (ownElixir / 10000).toFixed(1)}
          </div>
        </div>
        <div className="battle-stage">{(!observation || screen === "setup") ? <section className="first-run-panel">
          <div className={`first-run-symbol${connectionReady || connectionError || status?.setup?.error ? "" : " is-connecting"}`}>{connectionReady ? "✓" : <img src="/app-icon.png" alt="" />}</div>
          <h2>{restoringBattle ? t("runtimeRestoring") : status?.ready ? t("runtimeReady") : t("autoConnecting")}</h2>
          <p>{restoringBattle ? renderMessage(notice) : status?.ready ? t("environmentReadyLead") : t("autoConnectLead")}</p>
          <ol className="connection-steps" aria-label={t("connection")}>
            <li data-complete={Boolean(status?.environmentConfigured)}><span>{status?.environmentConfigured ? "✓" : "1"}</span><div><strong>{t("android")}</strong><small>{status?.environmentConfigured ? t("detected") : t("download")}</small></div></li>
            <li data-complete={Boolean(status?.resourcesConfigured)}><span>{status?.resourcesConfigured ? "✓" : "2"}</span><div><strong>{t("resources")}</strong><small>{status?.resourcesConfigured ? t("resourcesReady") : t("resourcesLead")}</small></div></li>
            <li data-complete={connectionReady} aria-current={!connectionReady && status?.environmentConfigured && status?.resourcesConfigured ? "step" : undefined}><span>{connectionReady ? "✓" : "3"}</span><div><strong>{t("connection")}</strong><small role="status">{restoringBattle ? t("runtimeRestoring") : status?.ready ? t("runtimeReady") : stageText(language,status?.stageCode,status?.stage ?? t("bridge"))}</small></div></li>
          </ol>
          {status && !status.environmentConfigured && !settingUp && <div className="setup-required">
            <label className="sdk-terms"><input type="checkbox" checked={acceptSdkTerms} disabled={busy} onChange={(event) => {setAcceptSdkTerms(event.target.checked);if(event.target.checked)void installEnvironment(true);}} />
              <span>{t("acceptAutoInstall")} <a href="https://developer.android.com/studio/terms" target="_blank" rel="noreferrer">{t("sdkTerms")}</a></span></label>
            {acceptSdkTerms && status.setup?.state === "failed" && <button className="setup-primary" onClick={() => { void installEnvironment(); }} disabled={busy}>{t("retry")}</button>}
          </div>}
          {status?.environmentConfigured && !status?.resourcesConfigured && !settingUp && <div className="setup-actions">
            <button className="setup-primary" onClick={() => { void importResources(); }} disabled={busy}>{t("importResources")}</button>
          </div>}
          {settingUp && <div className="setup-progress" role="status"><span>{stageText(language,status?.setup?.stageCode,status?.setup?.stage)}</span>
            {status?.setup?.state === "installing" && <progress max={status.setup.total} value={status.setup.downloaded} />}
          </div>}
          {(status?.setup?.error || connectionError) && <p className="setup-error" role="alert">{t("runtimeFailed")}<small>{status?.setup?.error ?? connectionError}</small></p>}
          {connectionError && <div className="setup-actions"><button className="setup-primary" onClick={() => { void reconnect(); }} disabled={busy || settingUp}>{t("retry")}</button></div>}
          {status?.ready && !project && !pendingCreation.current && <div className="setup-actions"><button className="setup-primary" onClick={beginCreation} disabled={busy}>{t("new")}</button></div>}
          {status?.ready && initialized.current && observation && <div className="setup-actions"><button className="setup-primary" onClick={()=>setScreen("workspace")} disabled={busy}>{t("battle")}</button></div>}
          <details className="setup-advanced"><summary>{t("advancedOptions")}</summary>
            <div className="setup-actions"><button onClick={() => { void configureRuntime(); }} disabled={busy || settingUp || connectingRuntime}>{t("existingRuntime")}</button>
              {status?.resourcesConfigured && <button onClick={() => { void importResources(); }} disabled={!status?.environmentConfigured || settingUp || busy || connectingRuntime}>{t("updateResources")}</button>}
              <button onClick={()=>{void exportDiagnostics();}} disabled={busy}>{t("diagnostics")}</button>
              {!status?.environmentConfigured && <button onClick={() => { void chooseInstallDirectory(); }} disabled={busy || settingUp}>{t("installLocation")}</button>}
            </div>
            {!status?.environmentConfigured && <small>{installDirectory ?? t("defaultLocation")}</small>}
            <p>{t("virtualization")}</p>
          </details>
        </section> : <NativeSurface bridge={bridge} ready={ready} profile={profile}
          selected={selected} observation={observation} disabled={busy} readOnly={Boolean(scrubRoute)} paused={!status?.playing} framePending={framePending} frameRevision={controlRevision.current} frameCacheKey={nodeFrameKey} onFrameRendered={frameRendered} onSelectCard={selectHand} onActivateAbility={(owner,entityId,abilityId)=>{void activateAbility(owner,entityId,abilityId);}} onArenaClick={(pixel) => { void playAt(pixel); }} onUnavailable={interrupt} />}</div>
        {connectionError && observation && screen === "workspace" && <div className="native-recovery" role="alert">
          <span>{t("interrupted")}<small>{connectionError}</small></span><button onClick={() => { void reconnect(); }} disabled={busy}>{t("reconnect")}</button>
        </div>}
        <div className="playback-controls">
          <button onClick={() => { void seek(Math.max(project?.nodes[project.rootNodeId].finalTick ?? 90,
            (observation?.tick ?? 91) - 1)); }} disabled={!ready || busy || framePending || Boolean(status?.playing)} title={status?.playing?t("pauseFirst"):t("backFrame")}>{t("back")}</button>
          <button className="play-button" onClick={() => { void togglePlay(); }} disabled={!ready || busy}>
            {status?.playing ? t("pause") : t("play")}</button>
          <button onClick={() => { void stepForward(); }} disabled={!ready || busy || framePending || Boolean(status?.playing) || Boolean(observation?.finalized)} title={status?.playing?t("pauseFirst"):t("nextFrame")}>{t("forward")}</button>
          <select aria-label={t("speed")} value={speed} onChange={(event) => { void changeSpeed(Number(event.target.value)); }}
            disabled={!ready || busy}>{[0.25, 0.5, 1, 2, 4].map((value) =>
              <option value={value} key={value}>{value}×</option>)}</select>
        </div>
        <input className="timeline-scrubber" aria-label={t("seek")} type="range"
          min={project?.nodes[project.rootNodeId].finalTick ?? 90}
          max={Math.max(91, scrubRoute?.maxTick ?? branch?.workingTick ?? 91)}
          value={observation?.tick ?? 90} disabled={!ready || busy || framePending}
          onChange={(event) => { void seek(Number(event.target.value)); }} />
        <div className="native-notice" role="status">{renderMessage(notice)}</div>
      </main>
      <aside className="native-inspector">
        <div className="panel-heading"><span>{t("details")}</span></div>
        {currentEdge && project ? <>
          <p>{project.nodes[currentEdge.parentId!].title} → {currentEdge.title}</p>
          <div className="inspector-stat"><span>{t("time")}</span><strong>{formatNativeTick(project.nodes[currentEdge.parentId!].finalTick)} → {formatNativeTick(currentEdge.finalTick)}</strong></div>
          <div className="inspector-stat"><span>{t("segmentActions")}</span><strong>{currentEdge.actionsFromParent.length}</strong></div>
        </> : currentNode ? <>
          <label>{t("title")}<input disabled={!ready||busy} onBlur={()=>history.current.endText()} value={currentNode.title} onChange={(event) => editNode({ title: event.target.value })} /></label>
          <div className="inspector-stat"><span>{t("time")}</span><strong>{formatNativeTick(currentNode.finalTick)}</strong></div>
          <div className="inspector-stat"><span>{t("segmentActions")}</span><strong>{currentNode.actionsFromParent.length}</strong></div>
          <label>{t("note")}<textarea disabled={!ready||busy} onBlur={()=>history.current.endText()} rows={8} value={currentNode.note}
            onChange={(event) => editNode({ note: event.target.value })} placeholder={t("noteHint")} /></label>
        </> : <p className="inspector-empty">{t("nodeEmpty")}</p>}
        <div className="runtime-card"><strong>{t("connection")}</strong>
          <span>{stageText(language,status?.stageCode,status?.stage ?? t("disconnected"))}</span>
          {status?.error && <p>{t("runtimeFailed")}<small>{status.error}</small></p>}
          <small>{project?.match.preset==='custom'?t("customDeck"):t("standardDeck")} · {project?.match.level?t("levelValue",{level:project.match.level}):t("originalLevel")} · {t("battle")}</small>
        </div>
      </aside>
    </div>
    }
    {archiveOpen && <div className="native-dialog-backdrop"><section className="native-dialog archive-dialog" role="dialog" aria-modal="true" aria-labelledby="archive-heading">
      <div className="dialog-heading"><h2 id="archive-heading">{t("archiveTitle")}</h2><button onClick={() => setArchiveOpen(false)} disabled={busy}>{t("close")}</button></div>
      <p>{t("nativeOnly")}</p>
      {archiveContents}
    </section></div>}
    {confirmation && <div className="native-dialog-backdrop"><section className="native-dialog confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-heading">
      <h2 id="confirm-heading">{t("confirm")}</h2><p>{renderMessage(confirmation.message)}</p>
      <div className="setup-actions"><button autoFocus onClick={()=>finishConfirmation(false)}>{t("cancel")}</button><button className="setup-primary" onClick={()=>finishConfirmation(true)}>{t("confirm")}</button></div>
    </section></div>}
    {helpOpen && <div className="native-dialog-backdrop"><section className="native-dialog help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-heading">
      <div className="dialog-heading"><h2 id="help-heading">{t("help")}</h2><button onClick={toggle}>{language === "zh-CN" ? "English" : "简体中文"}</button><button onClick={closeHelp}>{t("close")}</button></div>
      <p>{t("helpLead")}</p>
      {(["helpStart", "helpDeck", "helpBattle", "helpTime", "helpNodes", "helpUndo", "helpSave", "helpKeys", "helpRuntime"] as const).map(key=><section key={key}><h3>{t(key)}</h3><p>{t(`${key}Body`)}</p></section>)}
    </section></div>}
    {matchDialog && <NativeMatchDialog key={status?.identity.protocolVersion??'native-bridge.v1'} match={matchDialog==='view'?project?.match:undefined} busy={busy} ready={Boolean(status)||!runtimePreparation.current} onClose={()=>setMatchDialog(null)} onCreate={input=>{void newProject(input);}}/>}
  </div>;
}
