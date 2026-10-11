import assert from "node:assert/strict";
import { test } from "node:test";
import { createNativeProject, checkoutNativeNode, advanceNativeWork, STANDARD_NATIVE_DECK } from "../src/domain/nativeReplay.ts";
import { writeNativeJournal, clearNativeJournal, readNativeJournal } from "../src/native/journal.ts";

const identity = { upstreamCommit: "commit", probeSha256: "probe", apkSha256: "apk", libgSha256: "engine",
  runtimeFingerprintSha256: "content", protocolVersion: "v1", cropProfileId: "stock" };
const source = createNativeProject(identity, { seed: 1, preset: "standard", deck0: [...STANDARD_NATIVE_DECK], deck1: [...STANDARD_NATIVE_DECK] }, 90, "root");
const branch = advanceNativeWork(checkoutNativeNode(source, source.rootNodeId), 91);

test("journal clear waits for the preceding write, so stale writes cannot resurrect a committed draft", async () => {
  const calls = [];
  let release;
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async (command) => {
    calls.push(command);
    if (command === "write_native_journal") await new Promise((resolve) => { release = resolve; });
  } } };
  const write = writeNativeJournal(source, branch);
  const clear = clearNativeJournal(source.projectId);
  while (!release) await new Promise((resolve) => setTimeout(resolve, 1));
  assert.deepEqual(calls, ["write_native_journal"]);
  release();
  await Promise.all([write, clear]);
  assert.deepEqual(calls, ["write_native_journal", "clear_native_journal"]);
});

test("corrupt journal actions are rejected before they reach the engine", async () => {
  const entry = { schema: "cr-native-journal.v1", projectId: source.projectId, engine: source.engine,
    checkedOutNodeId: source.rootNodeId, workingTick: 91,
    actions: [{ id: "bad", tick: 91, sequence: 1, owner: 1, kind: "play-card", nativeCardId: 26000000, slot: 9, nativeX: 500, nativeY: 500 }] };
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => JSON.stringify(entry) } };
  await assert.rejects(readNativeJournal(source), /出牌动作/);
});

test("a matching confirmed draft is restored with its last confirmed time", async () => {
  const entry = { schema: "cr-native-journal.v1", projectId: source.projectId, engine: source.engine,
    checkedOutNodeId: source.rootNodeId, workingTick: 100,
    actions: [{ id: "accepted", tick: 91, sequence: 1, owner: 1, kind: "play-card", nativeCardId: 26000000, slot: 0, nativeX: 500, nativeY: 500 }] };
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => JSON.stringify(entry) } };
  assert.deepEqual(await readNativeJournal(source), { checkedOutNodeId: source.rootNodeId, workingTick: 100, actions: entry.actions });
});


test("fresh open ignores old drafts and corrupt cache instead of replaying them", async () => {
  const entry = { schema: "cr-native-journal.v1", projectId: source.projectId, engine: source.engine,
    checkedOutNodeId: source.rootNodeId, workingTick: 100,
    actions: [{ id: "old", tick: 91, sequence: 1, owner: 1, kind: "play-card", nativeCardId: 26000000, slot: 0, nativeX: 500, nativeY: 500 }] };
  for (const stored of [JSON.stringify(entry), '{broken', JSON.stringify({...entry,engine:{},actions:'invalid'}), 'null']) {
    globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => stored } };
    assert.equal(await readNativeJournal(source, false), null);
  }
});

test("fresh open preserves the confirmed project of an interrupted structural transaction", async () => {
  const entry = { schema: "cr-native-journal.v1", projectId: source.projectId, engine: source.engine,
    checkedOutNodeId: source.rootNodeId, workingTick: 91, actions: [],
    undoRecovery: {project:source,digest:'confirmed'} };
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => JSON.stringify(entry) } };
  const recovered = await readNativeJournal({...source,title:'Interrupted write'}, false);
  assert.deepEqual(recovered.recoveryProject, source);
  assert.equal(recovered.recoveryDigest,'confirmed');
});

test("fresh open refuses a transaction backup belonging to another project", async () => {
  const entry = { schema: "cr-native-journal.v1", projectId: source.projectId, engine: source.engine,
    checkedOutNodeId: source.rootNodeId, workingTick: 91, actions: [],
    undoRecovery: {project:{...source,projectId:'other-project'},digest:'confirmed'} };
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => JSON.stringify(entry) } };
  await assert.rejects(readNativeJournal(source, false), error=>error.code==='journalMismatch');
});
