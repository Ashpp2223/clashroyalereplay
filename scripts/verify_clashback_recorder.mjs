import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {dirname,join,resolve} from 'node:path';
import {openDesktopHarness} from './desktop_control.mjs';
import {NativeBridge} from '../src/native/bridge.ts';
import {
  advanceNativeWork,appendConfirmedNativeAction,checkoutNativeNode,configureNativeCatalog,
  createNativeCheckpoint,STANDARD_NATIVE_DECK,validateNativeProject,nativeActionsToNode,projectForCurrentEngine
} from '../src/domain/nativeReplay.ts';
import profile from '../src/data/clashbackEngineProfile.json' with {type:'json'};

const runtime=resolve(process.env.CR_CLASHBACK_ROOT??'.local-runtime/clashback-160635001');
const app=resolve(process.argv[2]??'release/native-desktop/clash-replay-tree.exe');
const home=resolve(process.env.CR_CLASHBACK_ACCEPTANCE_HOME??join(runtime,'desktop-acceptance'));
const prior=process.argv.includes('--reopen')?JSON.parse(await readFile(join(home,'report.json'),'utf8')):null;
const title='Clashback acceptance '+Date.now();
const report={status:'running',identity:profile.identity,title,checks:[],pageErrors:[]};
const hash=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
report.appSha256=await hash(app);
report.bridgeSha256=await hash(join(dirname(app),'native-bridge','native-bridge.exe'));
await mkdir(join(home,'saves'),{recursive:true});
let archive,branches;

let harness;
async function launch() {
  harness=await openDesktopHarness({app,home,runtime,engineProfile:profile.id});
  const page=harness.page;
  page.on('pageerror',error=>report.pageErrors.push(String(error)));
  globalThis.window={__TAURI_INTERNALS__:{invoke:(command,args)=>page.evaluate(
    ({command,args})=>window.__TAURI_INTERNALS__.invoke(command,args),{command,args})}};
  await page.locator('.native-home').waitFor();
  return page;
}

async function waitForArena(page) {
  await page.waitForFunction(()=>{
    const surface=document.querySelector('.native-surface');
    const canvas=document.querySelector('canvas[aria-label="原生竞技场"]');
    return surface&&canvas&&canvas.dataset.frameRevision&&
      surface.dataset.controlRevision===canvas.dataset.frameRevision;
  },null,{timeout:180000});
}

try {
  if(prior){
    assert.equal(prior.appSha256,report.appSha256);
    assert.equal(prior.bridgeSha256,report.bridgeSha256);
    assert.deepEqual(prior.identity,profile.identity);
    assert.equal(prior.checks.length,2);
    const name=(await readdir(join(home,'saves'))).find(name=>name.startsWith(prior.title+'_'));
    archive=join(home,'saves',name);
    const project=validateNativeProject(JSON.parse(await readFile(archive,'utf8')));
    branches=project.nodes[project.rootNodeId].childIds.map(id=>{
      const {finalTick:tick,stateDigest:digest}=project.nodes[id];
      return {id,tick,digest};
    });
    assert.equal(branches.length,2);
    assert.notEqual(branches[0].digest,branches[1].digest);
    Object.assign(report,{title:prior.title,checks:prior.checks,catalogue:prior.catalogue,opening:prior.opening});
  }else{
  const page=await launch();
  await page.evaluate(()=>{
    localStorage.setItem('clash-native.language','zh-CN');
    localStorage.setItem('clash-native.help-seen.v1','1');
  });
  await page.reload();
  await page.getByRole('button',{name:'新建存档',exact:true}).press('Enter');
  const dialog=page.locator('.deck-dialog');
  await dialog.locator('.card-catalog [data-card-id="26000107"]').waitFor();
  assert.equal(await dialog.locator('.card-catalog button').count(),123);
  while(await dialog.locator('.selected-deck-card').count()){
    await dialog.locator('.initial-order-tools button').first().press('Enter');
  }
  const deck=[26000107,26000003,26000001,26000005,28000001,28000000,26000014,28000006];
  for(const id of deck)await dialog.locator(`.card-catalog [data-card-id="${id}"]`).press('Enter');
  for(const [id,art] of [[26000003,'evolved-giant.png'],[28000006,'evolved-mirror.png']]){
    const entry=dialog.locator(`.selected-deck [data-card-id="${id}"]`);
    await entry.locator('select').selectOption('1');
    assert((await entry.locator('img').getAttribute('src')).endsWith('/'+art));
    await entry.locator('img').evaluate(image=>image.decode());
  }
  await dialog.getByRole('button',{name:/^敌方 ·/}).press('Enter');
  for(const id of STANDARD_NATIVE_DECK){
    const card=dialog.locator(`.card-catalog [data-card-id="${id}"]`);
    if(await card.isEnabled())await card.press('Enter');
  }
  await dialog.getByRole('button',{name:/^我方 ·/}).press('Enter');
  await dialog.getByLabel('新存档名称').fill(title);
  report.catalogue=await harness.screenshot('clashback-catalogue');
  const create=dialog.getByRole('button',{name:'创建存档并开始',exact:true});
  assert(await create.isEnabled(),'Both sides must have valid decks');
  await create.press('Enter');
  await waitForArena(page);
  const bridge=await NativeBridge.launch();
  const status=await bridge.get('/v1/status');
  assert.deepEqual(status.identity,profile.identity);
  assert(status.capabilities.nativeRenderer&&status.capabilities.snapshots);
  configureNativeCatalog(status.engineCatalog,status.identity);
  const root=await bridge.get('/v1/observe');
  assert.equal(root.players[1].hand[0].card,26000107);
  report.opening=await harness.screenshot('clashback-opening');
  report.checks.push('Packaged app creates an ordered deck with both evolutions and the native arena');

  const names=await readdir(join(home,'saves'));
  archive=join(home,'saves',names.find(name=>name.startsWith(title+'_')));
  let project=validateNativeProject(JSON.parse(await readFile(archive,'utf8')));
  const base=await bridge.post('/v1/snapshot-create');
  branches=[];
  for(const x of [9500,2500]){
    await bridge.post('/v1/step',{ticks:350-root.tick});
    const result=await bridge.post('/v1/play-card',{owner:1,slot:0,x,y:26500,expectedTick:350});
    assert(result.accepted);
    assert.equal(result.action.nativeCardId,26000107);
    const state=await bridge.post('/v1/step',{ticks:64});
    let work=advanceNativeWork(checkoutNativeNode(project,project.rootNodeId),state.tick);
    work=appendConfirmedNativeAction(project,work,{id:randomUUID(),...result.action});
    const saved=createNativeCheckpoint(project,work,state.digest,`Landing ${x}`);
    project=saved.project;
    branches.push({id:saved.node.id,tick:state.tick,digest:state.digest});
    const restored=await bridge.post('/v1/snapshot-restore',{handle:base.handle});
    assert.equal(restored.digest,root.digest);
  }
  assert.notEqual(branches[0].digest,branches[1].digest);
  await window.__TAURI_INTERNALS__.invoke('write_project_file',{path:archive,data:JSON.stringify(project,null,2)});
  await page.evaluate(({path,nodeId})=>localStorage.setItem('clash-native.last-session',JSON.stringify({path,nodeId})),
    {path:archive,nodeId:branches[0].id});
  report.checks.push('Packaged bridge confirms two native landings and restores the original snapshot between them');
  await harness.close();
  harness=null;
  }

  const page=await launch();
  await page.locator('button:enabled').filter({hasText:/^继续上次复盘$/}).evaluate(button=>button.click());
  await waitForArena(page);
  const reopened=await NativeBridge.launch();
  assert.equal((await reopened.get('/v1/observe')).digest,branches[0].digest);
  const replay=page.waitForResponse(r=>r.url().endsWith('/v1/replay')&&r.request().method()==='POST',{timeout:180000});
  await page.locator(`[data-node-id="${branches[1].id}"] button`).evaluate(button=>button.click());
  assert.equal((await (await replay).json()).digest,branches[1].digest);
  await waitForArena(page);
  report.reopened=await harness.screenshot('clashback-reopened-branch');
  report.checks.push('Close/reopen replays the saved branch and checks the sibling against its stored digest');
  const paused=await reopened.post('/v1/pause');
  await reopened.post('/v1/resume');
  await new Promise(done=>setTimeout(done,600));
  const advanced=await reopened.post('/v1/pause');
  assert(advanced.tick>paused.tick);
  assert((await reopened.frame(true)).size>10000);
  report.checks.push('The native clock resumes and returns a frame at the paused tick');
  for(let index=0;index<20;index++){
    await reopened.post('/v1/resume');
    await new Promise(done=>setTimeout(done,180));
    const stopped=await reopened.post('/v1/pause');
    assert((await reopened.frame(true)).size>10000);
    assert.equal((await reopened.get('/v1/observe')).tick,stopped.tick);
  }
  report.checks.push('20 packaged resume/pause cycles preserve the paused tick and fresh frame');
  if(process.env.CR_CLASHBACK_MIGRATION_ARCHIVE){
    const source=resolve(process.env.CR_CLASHBACK_MIGRATION_ARCHIVE);
    const original=await readFile(source);
    const old=validateNativeProject(JSON.parse(original.toString('utf8')));
    const migrated=projectForCurrentEngine(old,profile.identity);
    assert(migrated.upgraded);
    const root=old.nodes[old.rootNodeId];
    await reopened.replay(old.match.seed,[],root.finalTick,root.stateDigest,
      undefined,undefined,undefined,old.match);
    const base=await reopened.post('/v1/snapshot-create');
    const leaves=Object.values(old.nodes).filter(node=>!node.childIds.length);
    for(const node of leaves){
      const state=await reopened.replay(old.match.seed,nativeActionsToNode(old,node.id),node.finalTick,
        node.stateDigest,base.handle,root.finalTick,root.stateDigest,old.match);
      assert.equal(state.digest,node.stateDigest);
    }
    await reopened.post('/v1/snapshot-release',{handle:base.handle});
    const copy=join(home,'saves',`migration-${migrated.project.projectId}.cr-native-replay.json`);
    await window.__TAURI_INTERNALS__.invoke('write_project_file',{path:copy,data:JSON.stringify(migrated.project,null,2)});
    assert.deepEqual(await readFile(source),original);
    assert.deepEqual(migrated.project.nodes,old.nodes);
    report.migration={copy,verifiedBranches:leaves.length,originalPreserved:true};
    report.checks.push('Old-probe archive replays every leaf digest and saves an upgrade copy without changing the original');
  }
  assert.equal(report.pageErrors.length,0);
  Object.assign(report,{status:'passed',archive,branches});
} catch(error) {
  report.status='failed';
  report.error=String(error.stack??error);
  throw error;
} finally {
  if(harness)await harness.close();
  await writeFile(join(home,'report.json'),JSON.stringify(report,null,2)+'\n');
}
