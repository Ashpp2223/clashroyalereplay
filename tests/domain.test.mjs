import assert from "node:assert/strict";
import { test } from "node:test";
import { applyPlayCardToSnapshot, playCardInCycle } from "../src/domain/cycle.ts";
import {
  canConnectNodeToParent,
  connectNodeToParent,
  createChildNode,
  disconnectNode
} from "../src/domain/project.ts";

test("playCardInCycle updates hand and draw queue in real play order", () => {
  const initial = {
    hand: ["hog_rider", "fireball", "skeletons", "ice_golem"],
    drawQueue: ["cannon", "the_log", "musketeer", "valkyrie"]
  };

  const afterHog = playCardInCycle(initial, "hog_rider");
  assert.deepEqual(afterHog, {
    hand: ["cannon", "fireball", "skeletons", "ice_golem"],
    drawQueue: ["the_log", "musketeer", "valkyrie", "hog_rider"]
  });

  const afterFireball = playCardInCycle(afterHog, "fireball");
  assert.deepEqual(afterFireball, {
    hand: ["cannon", "the_log", "skeletons", "ice_golem"],
    drawQueue: ["musketeer", "valkyrie", "hog_rider", "fireball"]
  });
});

test("applyPlayCardToSnapshot treats positive cost delta as my advantage", () => {
  const snapshot = {
    myState: {
      hand: ["hog_rider", "fireball", "skeletons", "ice_golem"],
      drawQueue: ["cannon", "the_log", "musketeer", "valkyrie"]
    },
    enemyState: {
      hand: ["knight", "the_log", "cannon", "fireball"],
      drawQueue: ["musketeer", "skeletons", "valkyrie", "ice_golem"]
    },
    costDelta: 0.5
  };

  const myPlay = applyPlayCardToSnapshot(snapshot, "my", { id: "hog_rider", cost: 4 });
  assert.equal(myPlay.costDelta, -3.5);

  const enemyPlay = applyPlayCardToSnapshot(myPlay, "enemy", { id: "knight", cost: 3 });
  assert.equal(enemyPlay.costDelta, -0.5);
});

test("createChildNode copies snapshot and unit markers but starts with empty actions and note", () => {
  const parent = {
    id: "node_parent",
    parentId: null,
    childrenIds: [],
    title: "根局面",
    graphPosition: { x: 100, y: 200 },
    timerSeconds: 173,
    snapshot: {
      myState: { hand: ["a", "b", "c", "d"], drawQueue: ["e", "f", "g", "h"] },
      enemyState: { hand: ["i", "j", "k", "l"], drawQueue: ["m", "n", "o", "p"] },
      costDelta: 1.5
    },
    actions: [{ id: "action_1" }],
    unitMarkers: [{ id: "unit_1", note: "残血" }],
    note: "父节点备注"
  };

  const child = createChildNode(parent, 0);
  assert.deepEqual(child.snapshot, parent.snapshot);
  assert.deepEqual(child.unitMarkers, parent.unitMarkers);
  assert.equal(child.timerSeconds, 173);
  assert.deepEqual(child.actions, []);
  assert.equal(child.note, "");

  child.snapshot.myState.hand[0] = "changed";
  child.unitMarkers[0].note = "changed";
  assert.equal(parent.snapshot.myState.hand[0], "a");
  assert.equal(parent.unitMarkers[0].note, "残血");
});

test("disconnectNode and connectNodeToParent keep parent links consistent", () => {
  const root = {
    id: "root",
    parentId: null,
    childrenIds: ["a"],
    title: "root",
    graphPosition: { x: 0, y: 0 },
    snapshot: {
      myState: { hand: [], drawQueue: [] },
      enemyState: { hand: [], drawQueue: [] },
      costDelta: 0
    },
    actions: [],
    unitMarkers: [],
    note: ""
  };
  const nodeA = {
    ...root,
    id: "a",
    parentId: "root",
    childrenIds: ["b"]
  };
  const nodeB = {
    ...root,
    id: "b",
    parentId: "a",
    childrenIds: []
  };
  const nodeC = {
    ...root,
    id: "c",
    parentId: null,
    childrenIds: []
  };
  const project = {
    version: 1,
    projectName: "test",
    createdAt: "",
    updatedAt: "",
    rootNodeId: "root",
    cards: {},
    myDeck: [],
    enemyDeck: [],
    nodes: {
      root,
      a: nodeA,
      b: nodeB,
      c: nodeC
    }
  };

  const disconnected = disconnectNode(project, "a");
  assert.equal(disconnected.nodes.a.parentId, null);
  assert.deepEqual(disconnected.nodes.root.childrenIds, []);
  assert.equal(canConnectNodeToParent(disconnected, "a", "b"), false);

  const connected = connectNodeToParent(disconnected, "a", "c");
  assert.equal(connected.nodes.a.parentId, "c");
  assert.deepEqual(connected.nodes.c.childrenIds, ["a"]);
});
