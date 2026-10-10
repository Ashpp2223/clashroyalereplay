import assert from 'node:assert/strict';
import {readFile,readdir,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {openDesktopHarness} from './desktop_control.mjs';

const app=resolve(process.argv[2]??'src-tauri/target/release/clash-replay-tree.exe');
const runtime=resolve(process.argv[3]??`${process.env.LOCALAPPDATA}/cr-replay-clean-test`);
const devUrl=process.argv[4];
const home=resolve('.local-runtime/desktop-check');
const title='程序化桌面验收-'+Date.now();
const existingCount=(await readdir(join(home,'saves')).catch(()=>[])).filter(name=>name.endsWith('.cr-native-replay.json')).length;
const report={status:'running',checks:[],screenshots:[]};
let harness;
const screenshot=async name=>report.screenshots.push(await harness.screenshot(name));
const project=async()=>{
  const entries=(await readdir(join(home,'saves'))).filter(name=>name.endsWith('.cr-native-replay.json'));
  const matches=await Promise.all(entries.map(async name=>({path:join(home,'saves',name),data:JSON.parse(await readFile(join(home,'saves',name),'utf8'))})));
  return matches.find(entry=>entry.data.title===title);
};

try {
  harness=await openDesktopHarness({app,home,runtime,devUrl});
  let page=harness.page;
  await page.evaluate(()=>localStorage.removeItem('clash-native.last-session'));
  await page.reload();
  await page.getByRole('heading',{name:'创建复盘存档',exact:true}).waitFor({timeout:180000});
  await screenshot('01-before-creation');
  assert.equal((await readdir(join(home,'saves')).catch(()=>[])).filter(name=>name.endsWith('.cr-native-replay.json')).length,existingCount);
  report.checks.push('startup-does-not-create-an-archive');
  await page.getByRole('button',{name:'新建存档',exact:true}).first().click();
  let dialog=page.getByRole('dialog');
  await dialog.getByRole('textbox',{name:'新存档名称'}).fill(title);
  await dialog.getByLabel('我方骑士形态').selectOption('2');
  await dialog.getByRole('button',{name:'将骑士后移',exact:true}).click();
  await screenshot('02-initial-configuration');
  await dialog.getByRole('button',{name:'创建存档并开始',exact:true}).click();
  await dialog.waitFor({state:'hidden',timeout:90000});
  let saved=await project();assert(saved);
  assert.equal(saved.data.match.level,16);assert.equal(saved.data.match.dealMode,'ordered');
  const initial=structuredClone(saved.data.match);
  for(const [owner,label] of [[0,'敌方八张牌序'],[1,'我方八张牌序']]) {
    const hand=page.getByRole('region',{name:label});assert.equal(await hand.getByRole('button').count(),8);
    const expected=initial[owner===0?'deck0':'deck1'];
    const names=JSON.parse(await readFile('src/data/nativeCards.json','utf8'));
    for(let index=0;index<8;index++)assert((await hand.getByRole('button').nth(index).innerText()).includes(names.find(card=>card.id===expected[index]).name));
  }
  report.checks.push('configured-eight-card-orders-match-live-native-deal');
  await screenshot('03-native-battle');
  await page.getByRole('button',{name:'初始配置（只读）',exact:true}).click();
  dialog=page.getByRole('dialog');assert.equal(await dialog.locator('select,input').count(),0);
  assert.equal(await dialog.getByRole('button',{name:/移除|前移|后移|创建/}).count(),0);
  await screenshot('04-locked-configuration');
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  // The native file writer also rejects attempts to alter a saved match.
  const changed=structuredClone(saved.data);changed.match.level=11;
  const blocked=await page.evaluate(async({path,data})=>{
    try {await window.__TAURI_INTERNALS__.invoke('write_project_file',{path,data:JSON.stringify(data)});return false;}catch{return true;}
  },{path:saved.path,data:changed});assert(blocked);
  report.checks.push('configuration-is-locked-in-ui-and-native-file-writer');
  await page.getByRole('button',{name:'＋ 从当前节点创建子节点',exact:true}).click();
  await page.locator('.tree-node').nth(1).waitFor();
  await page.getByRole('region',{name:'我方八张牌序'}).getByRole('button').nth(1).click();
  const arena=page.getByLabel('原生竞技场');const box=await arena.boundingBox();assert(box);
  await arena.click({position:{x:box.width*.3,y:box.height*.72}});
  await page.locator('.native-notice').filter({hasText:'已确认'}).waitFor();
  await page.getByRole('button',{name:'保存当前节点',exact:true}).click();
  await page.locator('.native-notice').filter({hasText:'已更新当前节点'}).waitFor();
  saved=await project();assert.equal(Object.keys(saved.data.nodes).length,2);
  const child=Object.values(saved.data.nodes).find(node=>node.parentId);
  assert.equal(child.actionsFromParent.length,1);assert.equal(child.actionsFromParent[0].nativeForm,2);
  assert.equal(child.actionsFromParent[0].tick,saved.data.nodes[child.parentId].finalTick+1);
  assert.equal(child.finalTick,child.actionsFromParent[0].tick);
  assert.deepEqual(saved.data.match,initial);
  await screenshot('05-editable-child-saved');
  report.checks.push('copy-play-save-updates-one-child-without-changing-initial-configuration');
  await harness.close();harness=null;
  harness=await openDesktopHarness({app,home,runtime,devUrl});page=harness.page;
  await page.locator('.native-notice').filter({hasText:'项目已载入'}).waitFor({timeout:180000});
  assert.equal(await page.locator('.tree-node').count(),2);
  assert((await page.locator('.tree-node.active').innerText()).includes('1 动作'));
  await screenshot('06-cold-reopen');
  report.checks.push('cold-reopen-restores-selected-child-and-locked-match');
  // Crossing a recorded card event must replay it, not create an empty future.
  const waitFrame=()=>page.waitForFunction(()=>{
    const surface=document.querySelector('.native-surface'),canvas=document.querySelector('[aria-label="原生竞技场"]');
    return surface&&canvas&&canvas.dataset.frameRevision===surface.dataset.controlRevision&&!document.querySelectorAll('.playback-controls button')[2].disabled;
  },{},{polling:20});
  await waitFrame();
  // Root restoration must keep its snapshot after reopening a saved child.
  let freshFrames=0;
  const countFrame=request=>{if(request.url().endsWith('/v1/frame?fresh=1'))freshFrames++;};
  page.on('request',countFrame);
  const root=saved.data.nodes[saved.data.rootNodeId];
  async function switchNode(node) {
    const started=performance.now(),before=freshFrames;
    const response=page.waitForResponse(response=>response.url().endsWith('/v1/replay')&&response.ok());
    await page.locator(`[data-node-id="${node.id}"] button`).evaluate(button=>button.click());
    const confirmed=await response,state=await confirmed.json();
    assert.equal(state.tick,node.finalTick);assert.equal(state.digest,node.stateDigest);
    await waitFrame();
    return {nodeId:node.id,displayMs:Math.round(performance.now()-started),freshFrames:freshFrames-before,
      source:await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),command:confirmed.request().postDataJSON()};
  }
  const firstRoot=await switchNode(root);
  assert(firstRoot.command.snapshotHandle>0);assert.equal(firstRoot.command.baseTick,root.finalTick);
  await switchNode(child);
  report.nodeSwitch={firstRootDisplayMs:firstRoot.displayMs,warm:[]};
  for(const node of [root,child,root,child,root,child]) {
    const sample=await switchNode(node);
    assert.equal(sample.freshFrames,0);assert.equal(sample.source,'cache');
    report.nodeSwitch.warm.push({nodeId:sample.nodeId,displayMs:sample.displayMs});
  }
  page.off('request',countFrame);
  report.checks.push('reopened-root-keeps-snapshot-and-warm-node-switches-use-validated-frames');
  await page.getByRole('button',{name:'◀│ 后退一步',exact:true}).click();
  await waitFrame();
  const historicalReplay=page.waitForResponse(response=>response.url().endsWith('/v1/replay')&&response.ok());
  await page.getByRole('button',{name:'前进一步 │▶',exact:true}).click();
  const restoredEvent=await (await historicalReplay).json();
  await waitFrame();
  assert.equal(restoredEvent.tick,child.finalTick);
  assert.equal(restoredEvent.digest,child.stateDigest);
  report.checks.push('backward-then-forward-replays-the-recorded-card-event-and-matching-digest');
  for(const multiplier of ['1','4']) {
    await page.getByLabel('播放速度').selectOption(multiplier);
    await waitFrame();
    const before=Number(await page.getByLabel('时间跳转').inputValue());
    const step=page.waitForResponse(response=>response.url().endsWith('/v1/step')&&response.ok());
    await page.getByRole('button',{name:'前进一步 │▶',exact:true}).click();
    const stepped=await (await step).json();
    await waitFrame();
    assert.equal(stepped.tick,before+1);
    assert.equal(Number(await page.getByLabel('时间跳转').inputValue()),before+1);
  }
  report.checks.push('single-step-at-1x-and-4x-advances-one-tick-and-waits-for-the-drawn-frame');
  await page.getByLabel('播放速度').selectOption('1');
  await waitFrame();
  // Release old poll responses after a pause, reproducing the reported race.
  report.pauseTimingsMs=[];
  for(const endpoint of ['status','observe']) {
    let release,heldReady,armed=false,held=false,heldTick;
    const released=new Promise(done=>{release=done;});
    const ready=new Promise(done=>{heldReady=done;});
    const handler=async route=>{
      const response=await route.fetch();
      const body=await response.json();
      if(armed&&!held&&(endpoint==='observe'||body.playing)) {
        held=true;heldTick=body.tick;heldReady();await released;
      }
      await route.fulfill({response});
    };
    await page.route(`**/v1/${endpoint}`,handler);
    try {
      await page.getByRole('button',{name:'▶ 播放',exact:true}).click();
      await page.getByRole('button',{name:'Ⅱ 暂停',exact:true}).waitFor();
      armed=true;
      await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('No live poll was held')),10000))]);
      await page.waitForTimeout(160);
      const fresh=page.waitForResponse(response=>response.url().endsWith('/v1/frame?fresh=1')&&response.ok());
      const pauseAck=page.waitForResponse(response=>response.url().endsWith('/v1/pause')&&response.ok());
      const started=performance.now();
      await page.getByRole('button',{name:'Ⅱ 暂停',exact:true}).click();
      await page.getByRole('button',{name:'▶ 播放',exact:true}).waitFor();
      await page.waitForFunction(()=>!document.querySelector('.play-button').disabled);
      report.pauseTimingsMs.push(Math.round(performance.now()-started));
      const paused=await (await pauseAck).json();
      if(endpoint==='observe')assert(paused.tick>heldTick);
      assert((await fresh).headers()['content-type'].startsWith('image/png'));
      const frozen=await page.locator('.battle-clock').innerText();
      release();
      await page.waitForTimeout(500);
      assert.equal(await page.locator('.battle-clock').innerText(),frozen);
      assert.equal(await page.getByRole('button',{name:'Ⅱ 暂停',exact:true}).count(),0);
      report.checks.push(`late-${endpoint}-response-cannot-restart-or-rewind-paused-ui`);
    } finally {release();await page.unroute(`**/v1/${endpoint}`,handler);}
  }
  await screenshot('08-pause-synchronization');
  await page.getByRole('button',{name:'存档',exact:true}).click();
  await page.getByRole('heading',{name:'复盘存档'}).waitFor();
  await page.locator('.archive-entry').filter({hasText:title}).waitFor();
  await screenshot('07-archive-browser');
  report.checks.push('archive-browser-lists-the-created-project');
  report.status='passed';
} catch(error) {report.status='failed';report.error=String(error);if(harness)await screenshot('failure').catch(()=>{});throw error;}
finally {
  if(harness)await harness.close();
  await mkdir(home,{recursive:true});await writeFile(join(home,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}
