import assert from 'node:assert/strict';
import {mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {createHash} from 'node:crypto';
import {openDesktopHarness} from './desktop_control.mjs';
import {STANDARD_NATIVE_DECK} from '../src/domain/nativeReplay.ts';

const research=resolve(process.env.CR_CAMP_RESEARCH_ROOT??'../clashroyale1016');
const packaged=process.argv.includes('--packaged');
const catalogueOnly=process.argv.includes('--catalogue-only');
const app=packaged?'release/native-desktop/clash-replay-tree.exe':'src-tauri/target/debug/clash-replay-tree.exe';
const devUrl=packaged?undefined:'http://127.0.0.1:1420';
const home=join(research,catalogueOnly?'.runtime/recorder-cn-catalogue-check':packaged?'.runtime/recorder-cn-packaged-acceptance':'.runtime/recorder-cn-acceptance');
const profile=JSON.parse(await readFile(join(research,'analysis/recorder-profile.json'),'utf8'));
const sha256=async path=>createHash('sha256').update(await readFile(path)).digest('hex');
const artifacts={appSha256:await sha256(app),profileSha256:await sha256(join(research,'analysis/recorder-profile.json')),
 ...(packaged?{bridgeSha256:await sha256('release/native-desktop/native-bridge/native-bridge.exe')}:{} )};
const playbackOnly=process.argv.includes('--playback-only');
const terminalReopen=process.argv.includes('--terminal-reopen');
const previous=playbackOnly||terminalReopen?JSON.parse(await readFile(join(home,'report.json'),'utf8')):null;
assert(!(playbackOnly&&terminalReopen),'Choose one acceptance stage at a time');
assert(!(catalogueOnly&&(playbackOnly||terminalReopen)),'Catalogue check runs independently');
if(previous){
 assert.equal(previous.status,'passed','Continuation requires a successful previous acceptance stage');
 assert.deepEqual(previous.artifacts,artifacts,'Application or profile changed; rerun the full acceptance baseline');
 assert.deepEqual(previous.identity,profile.identity);
 assert(previous.checks.includes('actual application close/reopen, disk archive log reconstruction and restored branch digests'),'Playback requires an independently passed archive baseline');
 assert(previous.stateA?.digest&&previous.stateB?.digest&&previous.savedProject);
 await writeFile(join(home,'previous-report-'+Date.now()+'.json'),JSON.stringify(previous,null,2)+'\n');
}
const title=previous?.title??'CN native acceptance '+Date.now();
const report=previous?{...previous,status:'running'}:{status:'running',title,packaged,artifacts,checks:[],screenshots:[],errors:[],identity:profile.identity};
report.checks.push=(...items)=>{console.log(...items);return Array.prototype.push.apply(report.checks,items);};
let harness,bridgeInfo;
const waitReady=(page,allowTerminal=false)=>page.waitForFunction(terminal=>{
 const error=document.querySelector('.setup-error');if(error)throw Error(error.innerText);
 const surface=document.querySelector('.native-surface'),canvas=document.querySelector('[aria-label="原生竞技场"]');
 return surface&&canvas&&surface.dataset.controlRevision===canvas.dataset.frameRevision&&!(terminal?document.querySelector('.play-button'):document.querySelector('.playback-controls button:nth-child(3)')).disabled;
},allowTerminal,{timeout:180000});
const saved=async()=>{
 for(const name of await readdir(join(home,'saves'))){
  if(!name.endsWith('.cr-native-replay.json'))continue;
  const path=join(home,'saves',name),data=JSON.parse(await readFile(path,'utf8'));
  if(data.title===title)return {path,data};
 }
 throw Error('Owned acceptance archive was not saved');
};
const observe=page=>page.evaluate(async info=>{
 const response=await fetch(`http://127.0.0.1:${info.port}/v1/observe`,{headers:{Authorization:`Bearer ${info.token}`}});
 if(!response.ok)throw Error('Native observation failed');return response.json();
},bridgeInfo);
const checkpoint=async page=>{
 await page.getByRole('button',{name:'保存当前节点',exact:true}).click();
 await waitReady(page);await page.waitForFunction(()=>!document.querySelector('.draft-note'));
 return saved();
};
const selectNode=async(page,id)=>{
 await page.locator(`[data-node-id="${id}"] button`).focus();
 await page.locator(`[data-node-id="${id}"] button`).press('Enter');
 await waitReady(page);
};
const deploy=async(page,x,y)=>{
 await page.locator('.native-hand').last().locator('.native-hand-group.current button').first().click();
 const p=(await observe(page)).cropProfile??profile.cropProfile;
 const px=p.world[0]+p.world[2]*x/18000,py=p.world[1]+p.world[3]*y/32000;
 const arena=page.getByLabel('原生竞技场'),bounds=await arena.boundingBox();
 const response=page.waitForResponse(r=>r.url().endsWith('/v1/play-card')&&r.request().method()==='POST');
 await arena.click({position:{x:(px-p.arena[0])*bounds.width/p.arena[2],y:(py-p.arena[1])*bounds.height/p.arena[3]}});
 const receipt=await (await response).json();assert(receipt.accepted);assert.equal(receipt.action.nativeX,x);assert.equal(receipt.action.nativeY,y);
 await waitReady(page);return receipt;
};
const advanceTo131=async page=>{
 for(let tick=92;tick<=131;tick++){
  const response=page.waitForResponse(r=>r.url().endsWith('/v1/step')&&r.request().method()==='POST');
  await page.locator('.playback-controls button:nth-child(3)').click();
  assert.equal((await (await response).json()).tick,tick);await waitReady(page);
 }
};
try {
 await mkdir(join(home,'saves'),{recursive:true});
 let page;
 if(catalogueOnly){
  harness=await openDesktopHarness({app,home,runtime:join(research,'.runtime'),devUrl,engineProfile:'cn-38.4.5'});
  page=harness.page;
  page.on('pageerror',error=>report.errors.push(String(error)));
  await page.evaluate(()=>{localStorage.setItem('clash-native.language','zh-CN');localStorage.setItem('clash-native.help-seen.v1','1');});
  await page.reload();await page.locator('.native-home').waitFor();
  await page.getByRole('button',{name:'新建存档',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.card-catalog [data-card-id="26000107"]'));
  const dialog=page.locator('.deck-dialog');
  assert.equal(await dialog.locator('.card-catalog button').count(),123);
  while(await dialog.locator('.selected-deck-card').count()){
   await dialog.locator('.selected-deck-card').first().locator('.initial-order-tools button').last().click();
  }
  for(const id of [26000107,26000085,26000023,26000042,26000000,26000001,28000000,28000001]){
   await dialog.locator(`.card-catalog [data-card-id="${id}"]`).click();
  }
  for(const [name,mode] of [['雷电巨人','1'],['寒冰法师','2'],['闪电法师','2']]){
   await dialog.getByLabel(`我方${name}形态`).selectOption(mode);
  }
  for(const [id,file] of [[26000107,'minion-giant.png'],[26000085,'evolved-electro-giant.png'],[26000023,'hero-ice-wizard.png'],[26000042,'hero-electro-wizard.png']]){
   const card=dialog.locator(`.selected-deck [data-card-id="${id}"]`);
   assert((await card.locator('img').getAttribute('src')).endsWith('/cards/'+file));
   await card.locator('img').evaluate(img=>img.decode());
  }
  assert.equal(await dialog.locator('[role="alert"]').count(),0);
  report.screenshots.push(await harness.screenshot('new-card-and-forms'));
  report.checks.push('packaged catalogue exposes Minion Giant, evolved Electro Giant and both new Wizard heroes with loaded artwork and a valid combined deck');
  await dialog.getByLabel('新存档名称').fill(title);
  await dialog.getByRole('button',{name:'创建存档并开始',exact:true}).click();
  await waitReady(page);
  bridgeInfo=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('start_native_bridge'));
  report.root=await observe(page);
  const archive=await saved();report.savedProject=archive.data;report.archive=archive.path;
  assert.deepEqual(archive.data.match.forms1,[0,1,2,2,0,0,0,0]);
  for(const id of [26000023,26000042])assert.equal(report.root.players[1].hand.find(card=>card.card===id)?.form,2);
  report.screenshots.push(await harness.screenshot('new-heroes-native-match'));
  report.checks.push('packaged UI creates and saves the new combined deck; native ordered opening hand confirms both new hero forms');
 }else if(!playbackOnly&&!terminalReopen){
 harness=await openDesktopHarness({app,home,
  runtime:join(research,'.runtime'),devUrl,engineProfile:'cn-38.4.5'});
 page=harness.page;
 page.on('pageerror',error=>report.errors.push(String(error)));
 await page.evaluate(()=>{localStorage.removeItem('clash-native.last-session');localStorage.removeItem('clash-native.language');localStorage.setItem('clash-native.help-seen.v1','1');});
 await page.reload();await page.locator('.native-home').waitFor();
 await page.getByRole('button',{name:'新建存档',exact:true}).click();
 await page.waitForFunction(()=>[...document.querySelectorAll('.tower-config option')].some(o=>o.value==='169000004'));
 const dialog=page.locator('.deck-dialog');await dialog.getByLabel('新存档名称').fill(title);
 for(const side of ['敌方','我方']){
  await dialog.getByRole('button',{name:`${side} · 0/8`,exact:true}).click();
  for(const id of STANDARD_NATIVE_DECK)await dialog.locator(`.card-catalog button[data-card-id="${id}"]`).click();
 }
 const minion=dialog.locator('.card-catalog [data-card-id="26000107"]');
 assert.equal(await minion.locator('strong').innerText(),'亡灵巨人');
 await minion.locator('img').evaluate(async img=>{await img.decode();if(!img.naturalWidth)throw Error('New card art failed to load');});
 await dialog.getByLabel('我方塔楼牌').selectOption('169000004');
 await dialog.getByRole('button',{name:'创建存档并开始',exact:true}).click();await waitReady(page);
 bridgeInfo=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('start_native_bridge'));
 const rootState=await observe(page);assert.equal(rootState.tick,90);
 let archive=await saved();const rootId=archive.data.rootNodeId;
 assert.deepEqual(archive.data.engine,profile.identity);assert.equal(archive.data.match.tower1,169000004);assert.equal(archive.data.match.level,11);
 report.checks.push('real CN startup, ordered deal, explicit identity, Chef tower, default level and initial archive');
 report.root=rootState;
 report.screenshots.push(await harness.screenshot('01-root'));
 report.playA=await deploy(page,3500,24500);await advanceTo131(page);
 report.stateA=await observe(page);archive=await checkpoint(page);
 const aId=Object.keys(archive.data.nodes).find(id=>id!==rootId);assert(aId);report.aId=aId;
 assert.equal(archive.data.nodes[aId].stateDigest,report.stateA.digest);
 report.screenshots.push(await harness.screenshot('02-branch-a'));
 await selectNode(page,rootId);assert.equal((await observe(page)).digest,rootState.digest);
 report.playB=await deploy(page,14500,24500);await advanceTo131(page);
 report.stateB=await observe(page);assert.notEqual(report.stateA.digest,report.stateB.digest);
 archive=await checkpoint(page);const bId=Object.keys(archive.data.nodes).find(id=>id!==rootId&&id!==aId);assert(bId);report.bId=bId;
 assert.equal(archive.data.nodes[bId].stateDigest,report.stateB.digest);
 report.screenshots.push(await harness.screenshot('03-branch-b'));
 await selectNode(page,aId);assert.equal((await observe(page)).digest,report.stateA.digest);
 await selectNode(page,bId);assert.equal((await observe(page)).digest,report.stateB.digest);
 report.checks.push('real UI card coordinates, 80 exact frame steps, persisted sibling branches and snapshot node switching');
 await page.getByRole('button',{name:'返回首页',exact:true}).click();await page.locator('.native-home').waitFor();
 await harness.close();harness=null;
 harness=await openDesktopHarness({app,home,
  runtime:join(research,'.runtime'),devUrl,engineProfile:'cn-38.4.5'});
 page=harness.page;page.on('pageerror',error=>report.errors.push(String(error)));
 await page.locator('.native-home').waitFor();await page.getByRole('button',{name:'继续上次复盘',exact:true}).click();await waitReady(page);
 bridgeInfo=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('start_native_bridge'));
 assert.equal((await observe(page)).digest,report.stateB.digest);
 await selectNode(page,aId);assert.equal((await observe(page)).digest,report.stateA.digest);
 report.checks.push('actual application close/reopen, disk archive log reconstruction and restored branch digests');
 report.screenshots.push(await harness.screenshot('04-reopened'));
 archive=await saved();report.archive=archive.path;report.savedProject=archive.data;
 }else{
  harness=await openDesktopHarness({app,home,
   runtime:join(research,'.runtime'),devUrl,engineProfile:'cn-38.4.5'});
  page=harness.page;page.on('pageerror',error=>report.errors.push(String(error)));
  await page.locator('.native-home').waitFor();await page.getByRole('button',{name:'继续上次复盘',exact:true}).click();await waitReady(page,terminalReopen);
  bridgeInfo=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('start_native_bridge'));
  if(terminalReopen){
   assert(report.terminalNodeId&&report.terminal?.finalized);
   const restored=await observe(page);assert.equal(restored.digest,report.terminal.digest);
   assert.equal(restored.tick,report.terminal.tick);assert(restored.finalized);
   report.terminalReopened=restored;
   report.screenshots.push(await harness.screenshot('08-terminal-reopened'));
   report.checks.push('closed application terminal archive reconstructs the identical finalized native state from disk');
  }else{
  await selectNode(page,report.aId);assert.equal((await observe(page)).digest,report.stateA.digest);
  const before=await observe(page),resume=page.waitForResponse(r=>r.url().endsWith('/v1/resume'));
  await page.locator('.play-button').click();assert((await (await resume).json()).playing);
  const playDeadline=Date.now()+45000;let running;
  while(Date.now()<playDeadline){
   const state=await observe(page);
   if(state.tick>=before.tick+20){running=state;break;}
   await new Promise(done=>setTimeout(done,250));
  }
  assert(running,'Native playback did not advance 20 ticks');
  const pause=page.waitForResponse(r=>r.url().endsWith('/v1/pause'));
  await page.locator('.play-button').click();const paused=await (await pause).json();await waitReady(page);
  assert(paused.tick>=running.tick);assert(paused.cropProfile);
  report.playback={before,running,paused};
  assert.notDeepEqual(paused.cropProfile.world,before.cropProfile.world);
  report.screenshots.push(await harness.screenshot('05-live-zoom-paused'));
  report.zoomedPlay=await deploy(page,14500,6500);
  await selectNode(page,report.bId);assert.equal((await observe(page)).digest,report.stateB.digest);
  await selectNode(page,report.aId);assert.equal((await observe(page)).digest,report.stateA.digest);
  report.afterZoomRestore=await observe(page);
  if(JSON.stringify(report.afterZoomRestore.cropProfile.world)!==JSON.stringify(report.stateA.cropProfile.world))
   assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),'live');
  report.screenshots.push(await harness.screenshot('06-restored-after-zoom'));
  report.checks.push('live resume/pause, changed native camera, exact zoomed card coordinates and projection-specific node frame cache');
  await page.locator('.playback-controls select').selectOption('4');
  const resumed=page.waitForResponse(r=>r.url().endsWith('/v1/resume'));
  await page.locator('.play-button').click();await resumed;
  const deadline=Date.now()+150000;let terminal,logged=-1;
  while(Date.now()<deadline){
   const state=await observe(page);
   if(state.finalized){terminal=state;break;}
   if(Math.floor(state.tick/1000)!==logged){logged=Math.floor(state.tick/1000);console.log('CN live terminal tick '+state.tick);}
   await new Promise(done=>setTimeout(done,300));
  }
  assert(terminal,'Native battle did not finalize');
  const stopped=page.waitForResponse(r=>r.url().endsWith('/v1/pause'));
  await page.locator('.play-button').click();report.terminal=await (await stopped).json();
  assert(report.terminal.finalized);assert([-1,0,1].includes(report.terminal.result));
  await page.waitForFunction(()=>{const s=document.querySelector('.native-surface'),c=document.querySelector('[aria-label="原生竞技场"]');return s&&c&&s.dataset.controlRevision===c.dataset.frameRevision&&!document.querySelector('.play-button').disabled;});
  await page.getByRole('button',{name:'保存当前节点',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('.draft-note')&&!document.querySelector('.play-button').disabled);
  const finalArchive=await saved();report.savedProject=finalArchive.data;
  const finalNode=Object.values(finalArchive.data.nodes).find(n=>n.stateDigest===report.terminal.digest);
  assert(finalNode);report.terminalNodeId=finalNode.id;
  report.screenshots.push(await harness.screenshot('07-terminal'));
  await selectNode(page,report.bId);assert.equal((await observe(page)).digest,report.stateB.digest);
  await page.locator(`[data-node-id="${finalNode.id}"] button`).focus();await page.locator(`[data-node-id="${finalNode.id}"] button`).press('Enter');
  await page.waitForFunction(()=>{const s=document.querySelector('.native-surface'),c=document.querySelector('[aria-label="原生竞技场"]');return s&&c&&s.dataset.controlRevision===c.dataset.frameRevision&&!document.querySelector('.play-button').disabled;});
  assert.equal((await observe(page)).digest,report.terminal.digest);
  report.checks.push('live native terminal, saved terminal node and restored identical terminal digest');
  }
 }
 assert.equal(report.errors.length,0,report.errors.join('\n'));
 report.status='passed';console.log(report.checks.join('\n'));
 delete report.error;delete report.errorStack;delete report.ui;
} catch(error){report.status='failed';report.error=String(error);report.errorStack=error.stack;if(harness){report.ui=await harness.state().catch(()=>null);report.screenshots.push(await harness.screenshot('failure').catch(()=>''));}throw error;}
finally {
 if(harness)await harness.close();
 await mkdir(home,{recursive:true});await writeFile(join(home,'report.json'),JSON.stringify(report,null,2)+'\n');
}
