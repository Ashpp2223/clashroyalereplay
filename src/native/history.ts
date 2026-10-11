import type { NativeReplayProject, NativeTimelineNode, NativeWorkingBranch } from '../domain/nativeReplay.ts';

type Position = { x: number; y: number };
export type EditEntry =
  | { kind: 'metadata'; nodeId: string; field: 'title' | 'note'; before: string; merge: string }
  | { kind: 'position'; nodeId: string; before: Position }
  | { kind: 'action'; nodeId: string; actionId: string }
  | { kind: 'structure'; nodes: Record<string, { before?: NativeTimelineNode; after?: NativeTimelineNode }>;
      layout: Record<string, Position | undefined>; branch: NativeWorkingBranch; digest: string };

export class EditHistory {
  entries: EditEntry[] = [];
  private merge: string | null = null;
  push(entry: EditEntry): void {
    if (entry.kind === 'metadata' && this.merge === entry.merge && this.peek()?.kind === 'metadata') return;
    this.entries.push(entry);
    this.entries = this.entries.slice(-100);
    this.merge = entry.kind === 'metadata' ? entry.merge : null;
  }
  endText(): void { this.merge = null; }
  peek(): EditEntry | undefined { return this.entries[this.entries.length - 1]; }
  pop(): void { this.entries.pop(); this.endText(); }
  clear(): void { this.entries = []; this.endText(); }
}

export function structureEntry(before: NativeReplayProject, after: NativeReplayProject,
  branch: NativeWorkingBranch, digest: string): EditEntry {
  const nodes: Extract<EditEntry, {kind: 'structure'}>['nodes'] = {};
  const layout: Record<string, Position | undefined> = {};
  for (const id of new Set([...Object.keys(before.nodes), ...Object.keys(after.nodes)])) {
    if (before.nodes[id] !== after.nodes[id]) {
      nodes[id] = { before: before.nodes[id], after: after.nodes[id] };
      layout[id] = before.layout[id];
    }
  }
  return { kind: 'structure', nodes, layout, branch, digest };
}

export function undoStructure(project: NativeReplayProject, entry: Extract<EditEntry, {kind: 'structure'}>): NativeReplayProject {
  const next = { ...project, nodes: { ...project.nodes }, layout: { ...project.layout }, updatedAt: new Date().toISOString() };
  for (const [id, change] of Object.entries(entry.nodes)) {
    // Saving or adding descendants seals a structure operation. Never rewrite that history.
    if (JSON.stringify(project.nodes[id]) !== JSON.stringify(change.after)) throw new Error('UNDO_SAVED_HISTORY');
    if (change.before) next.nodes[id] = change.before;
    else delete next.nodes[id];
    if (entry.layout[id]) next.layout[id] = entry.layout[id]!;
    else delete next.layout[id];
  }
  return next;
}

export function isTextUndoTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}
