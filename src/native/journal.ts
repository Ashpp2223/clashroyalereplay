import { advanceNativeWork, appendConfirmedNativeAction, checkoutNativeNode,
  type NativeBattleAction, type NativeReplayProject, type NativeWorkingBranch } from "../domain/nativeReplay.ts";

interface NativeJournal {
  schema: "cr-native-journal.v1";
  projectId: string;
  engine: NativeReplayProject["engine"];
  checkedOutNodeId: string;
  workingTick: number;
  actions: NativeBattleAction[];
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

export async function writeNativeJournal(project: NativeReplayProject, branch: NativeWorkingBranch): Promise<void> {
  const entry: NativeJournal = {
    schema: "cr-native-journal.v1",
    projectId: project.projectId,
    engine: project.engine,
    checkedOutNodeId: branch.checkedOutNodeId,
    workingTick: branch.workingTick,
    actions: branch.actions
  };
  await invokeJournal("write_native_journal", { projectId: project.projectId, data: JSON.stringify(entry) });
}

export async function readNativeJournal(project: NativeReplayProject): Promise<NativeWorkingBranch | null> {
  const text = await invokeJournal<string | null>("read_native_journal", { projectId: project.projectId });
  if (!text) return null;
  const entry = JSON.parse(text) as NativeJournal;
  if (entry.schema !== "cr-native-journal.v1" || entry.projectId !== project.projectId ||
      JSON.stringify(entry.engine) !== JSON.stringify(project.engine) ||
      !project.nodes[entry.checkedOutNodeId] || !Number.isSafeInteger(entry.workingTick) ||
      entry.workingTick < project.nodes[entry.checkedOutNodeId].finalTick || !Array.isArray(entry.actions)) {
    throw new Error("恢复日志与当前项目或引擎版本不匹配");
  }
  let branch = advanceNativeWork(checkoutNativeNode(project, entry.checkedOutNodeId), entry.workingTick);
  for (const action of entry.actions) branch = appendConfirmedNativeAction(project, branch, action);
  return branch;
}

export async function clearNativeJournal(projectId: string): Promise<void> {
  await invokeJournal("clear_native_journal", { projectId });
}
