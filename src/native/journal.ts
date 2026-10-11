import { NativeUiError } from "./i18n.ts";
import { advanceNativeWork, appendConfirmedNativeAction, checkoutNativeNode,
  validateNativeProject, type NativeBattleAction, type NativeReplayProject, type NativeWorkingBranch } from "../domain/nativeReplay.ts";

interface NativeJournal {
  schema: "cr-native-journal.v1";
  projectId: string;
  engine: NativeReplayProject["engine"];
  checkedOutNodeId: string;
  workingTick: number;
  actions: NativeBattleAction[];
  undoRecovery?: { project: NativeReplayProject; digest: string };
}

const pendingWrites = new Map<string, Promise<unknown>>();

async function invokeJournal<T>(command: string, args: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  const key = String(args.projectId);
  const operation = (pendingWrites.get(key) ?? Promise.resolve()).catch(() => undefined)
    .then(() => invoke<T>(command, args));
  pendingWrites.set(key, operation);
  try { return await operation; }
  finally { if (pendingWrites.get(key) === operation) pendingWrites.delete(key); }
}

export async function writeNativeJournal(project: NativeReplayProject, branch: NativeWorkingBranch, undoRecovery?: NativeJournal["undoRecovery"]): Promise<void> {
  const entry: NativeJournal = {
    schema: "cr-native-journal.v1",
    projectId: project.projectId,
    engine: project.engine,
    checkedOutNodeId: branch.checkedOutNodeId,
    workingTick: branch.workingTick,
    actions: branch.actions,
    ...(undoRecovery ? { undoRecovery } : {})
  };
  await invokeJournal("write_native_journal", { projectId: project.projectId, data: JSON.stringify(entry) });
}

export async function readNativeJournal(project: NativeReplayProject, includeDraft = true): Promise<(NativeWorkingBranch & { recoveryProject?: NativeReplayProject; recoveryDigest?: string }) | null> {
  const text = await invokeJournal<string | null>("read_native_journal", { projectId: project.projectId });
  if (!text) return null;
  let entry: NativeJournal;
  try { entry = JSON.parse(text) as NativeJournal; }
  catch (error) { if (!includeDraft) return null; throw error; }
  // A fresh open never restores a previous draft. An interrupted structural
  // transaction still carries a confirmed project that must be recovered.
  if (!includeDraft && !entry?.undoRecovery) return null;
  if (!entry || typeof entry !== "object") throw new NativeUiError("journalMismatch");
  let source = project;
  if (entry.undoRecovery) {
    source = validateNativeProject(entry.undoRecovery.project);
    if (source.projectId !== project.projectId || JSON.stringify(source.engine) !== JSON.stringify(project.engine) ||
        JSON.stringify(source.match) !== JSON.stringify(project.match) || typeof entry.undoRecovery.digest !== "string" || !entry.undoRecovery.digest) throw new NativeUiError("journalMismatch");
  }
  if (entry.schema !== "cr-native-journal.v1" || entry.projectId !== project.projectId ||
      JSON.stringify(entry.engine) !== JSON.stringify(project.engine) ||
      !source.nodes[entry.checkedOutNodeId] || !Number.isSafeInteger(entry.workingTick) ||
      entry.workingTick < source.nodes[entry.checkedOutNodeId].finalTick || !Array.isArray(entry.actions)) {
    throw new NativeUiError("journalMismatch");
  }
  let branch = advanceNativeWork(checkoutNativeNode(source, entry.checkedOutNodeId), entry.workingTick);
  for (const action of entry.actions) branch = appendConfirmedNativeAction(source, branch, action);
  return entry.undoRecovery ? { ...branch, recoveryProject: source, recoveryDigest: entry.undoRecovery.digest } : branch;
}

export async function clearNativeJournal(projectId: string): Promise<void> {
  await invokeJournal("clear_native_journal", { projectId });
}
