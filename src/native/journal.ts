import type { NativeBattleAction, NativeReplayProject, NativeWorkingBranch } from "../domain/nativeReplay.ts";

interface NativeJournal {
  schema: "cr-native-journal.v1";
  projectId: string;
  engine: NativeReplayProject["engine"];
  checkedOutNodeId: string;
  workingTick: number;
  actions: NativeBattleAction[];
}

async function invokeJournal<T>(command: string, args: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
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
  return { checkedOutNodeId: entry.checkedOutNodeId, workingTick: entry.workingTick,
    actions: entry.actions };
}

export async function clearNativeJournal(projectId: string): Promise<void> {
  await invokeJournal("clear_native_journal", { projectId });
}
