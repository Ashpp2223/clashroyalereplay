import assert from "node:assert/strict";
import { test } from "node:test";
import {
  advanceNativeWork,
  appendConfirmedNativeAction,
  checkoutNativeNode,
  createNativeCheckpoint,
  copyNativeNode,
  createNativeProject,
  deleteNativeSubtree,
  nativeActionsToNode,
  nearestNativeSnapshotNode,
  STANDARD_NATIVE_DECK,
  NATIVE_CARD_CATALOG,
  nativeDeckSpecialSlots,
  validateNativeForms,
  NATIVE_TOWER_CATALOG,
  DEFAULT_NATIVE_TOWER,
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

test('card collection sorts by elixir, then rarity, with stable ties',()=>{
  const rarity=['common','rare','epic','legendary','champion'];
  assert(NATIVE_CARD_CATALOG.every(card=>rarity.includes(card.rarity)));
  for(let index=1;index<NATIVE_CARD_CATALOG.length;index++) {
    const a=NATIVE_CARD_CATALOG[index-1],b=NATIVE_CARD_CATALOG[index];
    assert(a.cost<b.cost || a.cost===b.cost && (rarity.indexOf(a.rarity)<rarity.indexOf(b.rarity) || a.rarity===b.rarity && a.id<b.id));
  }
});

test('one Evolution, one Hero and one Wild slot include Champions and ignore hand order',()=>{
  const evolutions=NATIVE_CARD_CATALOG.filter(card=>card.forms&1).slice(0,3);
  const heroes=NATIVE_CARD_CATALOG.filter(card=>card.forms&2&&!evolutions.includes(card)).slice(0,3);
  const champions=NATIVE_CARD_CATALOG.filter(card=>card.rarity==='champion').slice(0,2);
  const ordinary=NATIVE_CARD_CATALOG.filter(card=>card.rarity!=='champion'&&!evolutions.includes(card)&&!heroes.includes(card));
  for(let e=0;e<=3;e++)for(let h=0;h<=3;h++)for(let c=0;c<=2;c++) {
    const special=[...evolutions.slice(0,e),...heroes.slice(0,h),...champions.slice(0,c)];
    const deck=[...special,...ordinary.slice(0,8-special.length)].map(card=>card.id);
    const forms=[...Array(e).fill(1),...Array(h).fill(2),...Array(8-e-h).fill(0)];
    const valid=Math.max(0,e-1)+Math.max(0,h+c-1)<=1;
    for(const [cards,modes] of [[deck,forms],[deck.toReversed(),forms.toReversed()]]) {
      if(valid) {
        assert.doesNotThrow(()=>validateNativeForms(cards,modes));
        const occupied=nativeDeckSpecialSlots(cards,modes).filter(Boolean);
        assert.equal(occupied.length,e+h+c);
        assert.equal(new Set(occupied).size,occupied.length);
      }else assert.throws(()=>validateNativeForms(cards,modes),/万能位/);
    }
  }
  const deck=[...STANDARD_NATIVE_DECK],forms=[1,0,0,0,0,0,2,0];
  assert.doesNotThrow(()=>validateNativeForms(deck,forms));
  forms[0]=3;assert.throws(()=>validateNativeForms(deck,forms),/形态/);
});

test('tower selection survives saving and legacy projects keep their original default',()=>{
  const legacy=createNativeProject(identity,match,90,'root');
  assert.equal(validateNativeProject(JSON.parse(JSON.stringify(legacy))).match.tower0,undefined);
  assert.equal(DEFAULT_NATIVE_TOWER,159000000);
  for(const tower of NATIVE_TOWER_CATALOG) {
    const config={...match,tower0:tower.id,tower1:159000004};
    const project=createNativeProject(identity,config,90,'root');
    const restored=validateNativeProject(JSON.parse(JSON.stringify(project)));
    assert.equal(restored.match.tower0,tower.id);
    assert.equal(restored.match.tower1,159000004);
    assert(Object.isFrozen(restored.match));
    const copy=copyNativeNode(restored,checkoutNativeNode(restored,restored.rootNodeId),'root');
    assert.deepEqual(copy.project.match,restored.match);
  }
  for(const invalid of [159000003,26000000,null,true,'159000001',159000001.5]) {
    const project=structuredClone(legacy);project.match.tower1=invalid;
    assert.throws(()=>validateNativeProject(project),/塔楼牌/);
  }
});

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

test('copy at the same tick, edit and save keeps one child; a later fork seals its history',()=>{
  const root=createNativeProject(identity,match,90,'root');
  const copy=copyNativeNode(root,checkoutNativeNode(root,root.rootNodeId),'root');
  assert.equal(copy.node.finalTick,90);
  validateNativeProject(copy.project);
  const work=appendConfirmedNativeAction(copy.project,advanceNativeWork(copy.branch,91),play(91,0));
  const saved=createNativeCheckpoint(copy.project,work,'played');
  assert.equal(saved.node.id,copy.node.id);
  assert.equal(Object.keys(saved.project.nodes).length,2);
  assert.deepEqual(nativeActionsToNode(saved.project,saved.node.id),[play(91,0)]);
  const next=copyNativeNode(saved.project,saved.branch,'played');
  assert.equal(next.project.nodes[saved.node.id].editableCopy,false);
  assert.equal(next.node.finalTick,91);
  const alternative=createNativeCheckpoint(next.project,advanceNativeWork(checkoutNativeNode(next.project,saved.node.id),93),'alternate');
  assert.deepEqual(alternative.project.nodes[saved.node.id].actionsFromParent,[play(91,0)]);
  assert.equal(alternative.project.nodes[saved.node.id].finalTick,91);
  validateNativeProject(alternative.project);
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

test("time seek chooses the nearest cached ancestor, never a sibling or future node", () => {
  const root = createNativeProject(identity, match, 90, "root");
  const one = createNativeCheckpoint(root, advanceNativeWork(checkoutNativeNode(root, root.rootNodeId), 100), "one");
  const two = createNativeCheckpoint(one.project, advanceNativeWork(one.branch, 130), "two");
  const sibling = createNativeCheckpoint(two.project,
    advanceNativeWork(checkoutNativeNode(two.project, root.rootNodeId), 110), "sibling");
  const cached = new Set([root.rootNodeId, one.node.id, two.node.id, sibling.node.id]);
  assert.equal(nearestNativeSnapshotNode(sibling.project, two.node.id, 120, cached)?.id, one.node.id);
  assert.equal(nearestNativeSnapshotNode(sibling.project, two.node.id, 90, cached)?.id, root.rootNodeId);
  assert.equal(nearestNativeSnapshotNode(sibling.project, two.node.id, 130, cached)?.id, two.node.id);
  assert.equal(nearestNativeSnapshotNode(sibling.project, two.node.id, 120, new Set([sibling.node.id])), null);
});

test("unsupported abilities, cards, seeds and missing engine identity are rejected before replay", () => {
  const project = createNativeProject(identity, match, 90, "root");
  const work = advanceNativeWork(checkoutNativeNode(project, project.rootNodeId), 91);
  assert.throws(() => appendConfirmedNativeAction(project, work, { ...play(91, 1), nativeCardId: 26000999 }));
  assert.throws(() => appendConfirmedNativeAction(project, work, { ...play(91, 1), kind: "activate-ability", nativeCardId:undefined, nativeEntityId: -1, nativeAbilityId: 2 }));
  assert.throws(() => validateNativeProject({ ...project, match: { ...match, seed: -1 } }));
  assert.throws(() => validateNativeProject({ ...project, engine: { upstreamCommit: "only-one-field" } }));
});

test("custom base decks survive serialization and reject cards outside the owner's deck", () => {
  const deck0 = [...STANDARD_NATIVE_DECK]; deck0[7] = 27000000;
  const project = createNativeProject(identity, { ...match, preset: 'custom', deck0 }, 90, 'root');
  const restored = validateNativeProject(JSON.parse(JSON.stringify(project)));
  assert.deepEqual(restored.match.deck0, deck0);
  const work = advanceNativeWork(checkoutNativeNode(restored, restored.rootNodeId), 91);
  assert.doesNotThrow(() => appendConfirmedNativeAction(restored, work, { ...play(91,1), owner:0, nativeCardId:27000000 }));
  assert.throws(() => appendConfirmedNativeAction(restored, work, { ...play(91,1), owner:1, nativeCardId:27000000 }));
  assert.throws(() => validateNativeProject({ ...project, match:{...project.match, deck0:Array(8).fill(26000000)} }));
});

test('level-16 matches retain their level while legacy archives retain the old template',()=>{
  const legacy=createNativeProject(identity,match,90,'legacy');
  assert.equal(validateNativeProject(legacy).match.level,undefined);
  const current=createNativeProject(identity,{...match,level:16},90,'current');
  assert.equal(validateNativeProject(JSON.parse(JSON.stringify(current))).match.level,16);
  for(const level of [0,17,true,16.5])assert.throws(()=>validateNativeProject({...current,match:{...current.match,level}}));
});

test('initial configuration is frozen at creation and after reading an archive',()=>{
  const project=createNativeProject(identity,{...match,level:16,dealMode:'ordered'},90,'root');
  assert.throws(()=>{project.match.deck0.reverse();},TypeError);
  assert.throws(()=>{project.match.level=11;},TypeError);
  const restored=validateNativeProject(JSON.parse(JSON.stringify(project)));
  assert.throws(()=>{restored.match.deck1[0]=27000000;},TypeError);
  const invalid=[...STANDARD_NATIVE_DECK];invalid[0]=28000006;
  assert.throws(()=>validateNativeProject({...project,match:{...project.match,deck0:invalid}}),/牌序规则/);
});
