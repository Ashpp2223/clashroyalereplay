import assert from "node:assert/strict";
import { test } from "node:test";
import {
  advanceNativeWork,
  appendConfirmedNativeAction,
  checkoutNativeNode,
  createNativeCheckpoint,
  createNativeProject,
  deleteNativeSubtree,
  nativeActionsToNode,
  STANDARD_NATIVE_DECK,
  validateNativeProject
} from "../src/domain/nativeReplay.ts";

const identity = {
  upstreamCommit: "f92ccbec",
  probeSha256: "probe",
  apkSha256: "apk",
  libgSha256: "libg",
  runtimeFingerprintSha256: "runtime",
  protocolVersion: "v1",
  cropProfileId: "1080x1920"
};
const match = { seed: 41, preset: "standard", deck0: [...STANDARD_NATIVE_DECK], deck1: [...STANDARD_NATIVE_DECK] };

function play(tick, sequence, owner = 1) {
  return { id: `${tick}-${sequence}`, tick, sequence, owner, kind: "play-card",
    nativeCardId: 26000000, slot: 0, nativeX: 3500, nativeY: 13000 };
}

test("historical checkout creates sibling worlds without mutating original descendants", () => {
  const root = createNativeProject(identity, match, 90, "root");
  const base = checkoutNativeNode(root, root.rootNodeId);
  const firstWork = appendConfirmedNativeAction(root, advanceNativeWork(base, 91), play(91, 0));
  const first = createNativeCheckpoint(root, firstWork, "first");
  const grandchild = createNativeCheckpoint(first.project, advanceNativeWork(first.branch, 120), "grandchild");
  const newWork = appendConfirmedNativeAction(grandchild.project,
    advanceNativeWork(checkoutNativeNode(grandchild.project, root.rootNodeId), 92), play(92, 0, 0));
  const alternate = createNativeCheckpoint(grandchild.project, newWork, "alternate");
  assert.deepEqual(alternate.project.nodes[root.rootNodeId].childIds, [first.node.id, alternate.node.id]);
  assert.deepEqual(alternate.project.nodes[first.node.id].childIds, [grandchild.node.id]);
  assert.deepEqual(nativeActionsToNode(alternate.project, grandchild.node.id), [play(91, 0)]);
  assert.deepEqual(nativeActionsToNode(alternate.project, alternate.node.id), [play(92, 0, 0)]);
  validateNativeProject(alternate.project);
});

test("invalid time order and legacy projects are rejected", () => {
  const project = createNativeProject(identity, match, 90, "root");
  const branch = checkoutNativeNode(project, project.rootNodeId);
  assert.throws(() => appendConfirmedNativeAction(project, branch, play(91, 0)));
  assert.throws(() => createNativeCheckpoint(project, branch, "same tick"));
  assert.throws(() => validateNativeProject({ version: 1, nodes: {} }), /旧版/);
});

test("delete subtree leaves other branches intact", () => {
  const root = createNativeProject(identity, match, 90, "root");
  const one = createNativeCheckpoint(root, advanceNativeWork(checkoutNativeNode(root, root.rootNodeId), 100), "one");
  const two = createNativeCheckpoint(one.project,
    advanceNativeWork(checkoutNativeNode(one.project, root.rootNodeId), 110), "two");
  const deleted = deleteNativeSubtree(two.project, one.node.id);
  assert.deepEqual(deleted.nodes[root.rootNodeId].childIds, [two.node.id]);
  assert.equal(deleted.nodes[one.node.id], undefined);
  validateNativeProject(deleted);
});
