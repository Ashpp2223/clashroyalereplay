import { type NativeReplayProject, type NativeWorkingBranch } from '../domain/nativeReplay.ts';
import type { NativeObservation } from './bridge.ts';

export class UndoRecoveryError extends Error {
  readonly recovered: boolean;
  readonly cause: unknown;
  constructor(recovered: boolean, cause: unknown) {
    super(recovered ? 'UNDO_FAILED_RESTORED' : 'UNDO_RECOVERY_REQUIRED');
    this.recovered=recovered;this.cause=cause;
  }
}

/** No checkout/seek/checkpoint calls: history stays immutable throughout this transaction. */
export async function restoreUndoBranch(
  project: NativeReplayProject, before: NativeWorkingBranch, paused: NativeObservation,
  after: NativeWorkingBranch,
  replay: (project: NativeReplayProject, branch: NativeWorkingBranch, digest?: string) => Promise<NativeObservation>,
  journal: (project: NativeReplayProject, branch: NativeWorkingBranch) => Promise<void>,
  targetProject = project, expectedDigest?: string,
  persist?: (project: NativeReplayProject) => Promise<void>,
): Promise<NativeObservation> {
  if (paused.tick !== before.workingTick || !paused.digest) throw new Error('UNDO_PAUSE_MISMATCH');
  let persisted = false;
  try {
    const original = await replay(project,before,paused.digest);
    if(original.tick !== paused.tick || original.digest !== paused.digest) throw new Error("UNDO_ORIGINAL_MISMATCH");
    const state = await replay(targetProject, after, expectedDigest);
    if (state.tick !== after.workingTick || !state.digest) throw new Error('UNDO_REPLAY_MISMATCH');
    // Independently reproduce the resulting digest before publishing it or its journal.
    const verified = await replay(targetProject, after, state.digest);
    if (verified.tick !== state.tick || verified.digest !== state.digest) throw new Error('UNDO_DIGEST_MISMATCH');
    if (persist) { persisted = true; await persist(targetProject); }
    await journal(targetProject, after);
    return verified;
  } catch (error) {
    let recovered = false;
    try {
      const restored = await replay(project, before, paused.digest);
      if (restored.tick !== paused.tick || restored.digest !== paused.digest) throw new Error('UNDO_ROLLBACK_MISMATCH');
      if (persisted) await persist!(project);
      await journal(project, before);
      recovered = true;
    } catch { /* Caller freezes interaction until normal recovery succeeds. */ }
    throw new UndoRecoveryError(recovered, error);
  }
}

export function withoutLastDraftAction(project: NativeReplayProject, branch: NativeWorkingBranch, actionId: string): NativeWorkingBranch {
  const last = branch.actions[branch.actions.length - 1];
  if (!last || last.id !== actionId || Object.values(project.nodes).some(node=>node.actionsFromParent.some(action=>action.id===actionId))) {
    throw new Error('UNDO_SAVED_HISTORY');
  }
  return { ...branch, actions: branch.actions.slice(0, -1) };
}
