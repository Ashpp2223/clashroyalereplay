import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createNativeProject,STANDARD_NATIVE_DECK,NATIVE_CARD_CATALOG} from '../src/domain/nativeReplay.ts';
const identity={upstreamCommit:'commit',probeSha256:'probe',apkSha256:'apk',libgSha256:'libg',runtimeFingerprintSha256:'runtime',protocolVersion:'v1',cropProfileId:'stock'};
const source=createNativeProject(identity,{seed:41,preset:'standard',deck0:[...STANDARD_NATIVE_DECK],deck1:[...STANDARD_NATIVE_DECK]},90,'root','保留用户标题');
const path='C:/test/saves/test.cr-native-replay.json';
const report={kind:'frontend with mocked files and bridge; no real engine',status:'running',checks:[],errors:[]};
const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
try {
 const page=await browser.newPage({viewport:{width:1440,height:960}});
 page.on('pageerror',error=>report.errors.push(String(error)));
 await page.addInitScript(({source,path})=>{
  const data={source,path,reads:[],writes:[],launches:0,offline:true,failSave:false,journal:null,journalClears:0,selected:'C:/test/saves/old.cr-replay.json'};
  window.mock=data;
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
   if(command==='native_test_workspace')return 'C:/test';
   if(command==='plugin:path|join')return args.paths.join('/');
   if(command==='ensure_writable_directory')return;
   if(command==='list_project_files')return [{name:'test.cr-native-replay.json',path},{name:'old.cr-replay.json',path:'C:/test/saves/old.cr-replay.json'},{name:'invalid.cr-native-replay.json',path:'C:/test/saves/invalid.cr-native-replay.json'}];
   if(command==='read_project_file'){data.reads.push(args.path);if(args.path===path)return JSON.stringify(data.source);if(args.path.includes('old'))return JSON.stringify({version:1,nodes:{}});return 'invalid';}
   if(command==='plugin:dialog|open')return data.selected;
   if(command==='write_project_file'){if(data.failSave)throw Error('Simulated file save failure');data.writes.push(args.path);data.source=JSON.parse(args.data);return;}
   if(command==='start_native_bridge'){data.launches++;if(data.offline)throw Error('Mock runtime unavailable');return {port:26791,token:'mock-test-only',protocol:'native-bridge.v1'};}
   if(command==='read_native_journal')return data.journal;
   if(command==='clear_native_journal'){data.journal=null;data.journalClears++;return;}
   if(command==='write_native_journal'){data.journal=args.data;return;}
   throw Error('Unhandled mock command: '+command);
  }};
 },{source,path});
 const cropProfile={id:'stock',nativeWidth:1080,nativeHeight:1920,enemyHud:[0,0,1080,282],arena:[54,282,972,1384],myHud:[0,1666,1080,254],world:[111,436,856,1230],handCentersX:[211,354,497,640],enemyHandReversed:true};
 let environmentReady=true,installRequests=0,resourcesReady=true,runtimeReady=false,playing=false,startRequests=0,startPolls=0,failStart=false,setupError=null;
 let state={tick:90,digest:'root',finalized:false,result:null,crowns:[0,0],entities:[],players:[0,1].map(owner=>({owner,elixir:76000,hand:STANDARD_NATIVE_DECK.slice(0,4).map((card,slot)=>({card,slot,cost:3})),cycle:STANDARD_NATIVE_DECK.slice(4),runtime:{abilityRuntime:[]}}))};
 const image=await readFile('public/elixir.png');
 let freshFrames=0,liveFrames=0,failNextFrame=false;
 await page.route('http://127.0.0.1:26791/**',async route=>{
  const url=new URL(route.request().url());const body=route.request().postDataJSON();
  if(url.pathname==='/v1/frame'){if(failNextFrame){failNextFrame=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Simulated capture failure'})});}if(url.searchParams.has('fresh'))freshFrames++;else liveFrames++;return route.fulfill({status:200,contentType:'image/png',body:image});}
  let response;
  if(url.pathname==='/v1/start'){startRequests++;startPolls=3;}
  if(url.pathname==='/v1/status' && startPolls>0 && --startPolls===0 && !failStart)runtimeReady=true;
  if(url.pathname==='/v1/status'||url.pathname==='/v1/start')response={ready:runtimeReady,playing,error:failStart?'Mock startup failed':null,stage:runtimeReady?'Mock ready':'Starting Android',stageCode:runtimeReady?'ready':'emulator',identity,cropProfile,capabilities:{},environmentConfigured:environmentReady,resourcesConfigured:resourcesReady,setup:{state:setupError?'failed':'idle',error:setupError}};
  else if(url.pathname==='/v1/setup/install'){assert.equal(body.acceptLicense,true);installRequests++;environmentReady=true;response={state:'ready'};}
  else if(url.pathname==='/v1/setup/resources'){resourcesReady=true;setupError=null;response={state:'ready'};}
  else if(url.pathname==='/v1/suspend')response={playing:false};
  else if(url.pathname==='/v1/snapshot-create')response={tick:state.tick,handle:1};
  else if(url.pathname==='/v1/snapshot-release')response={};
  else if(url.pathname==='/v1/replay'){state={...state,tick:body.targetTick,digest:body.expectedDigest??'root'};response=state;}
  else if(url.pathname==='/v1/reset'){await page.evaluate(body=>{window.mock.reset=body;},body);state={...state,tick:90,digest:'tower-root'};response=state;}
  else if(url.pathname==='/v1/play-card'){
   const card=state.players[body.owner].hand.find(card=>card.slot===body.slot);
   state={...state,tick:state.tick+1,digest:`draft-${state.tick+1}`};
   response={accepted:true,observation:state,action:{tick:state.tick,sequence:1,owner:body.owner,kind:'play-card',nativeCardId:card.card,nativeForm:card.form??0,slot:body.slot,nativeX:body.x,nativeY:body.y}};
  }
  else if(url.pathname==='/v1/step'){state={...state,tick:state.tick+body.ticks,digest:`tick-${state.tick+body.ticks}`};response=state;}
  else if(url.pathname==='/v1/pause'){playing=false;response=state;}
  else if(url.pathname==='/v1/resume'){playing=true;response={playing:true};}
  else if(url.pathname==='/v1/observe')response=state;
  else throw Error('Unhandled mock route '+url.pathname);
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(response)});
 });
 await page.goto('http://127.0.0.1:1420/');await page.locator('.archive-entry').waitFor();
 assert.equal(await page.locator('.archive-entry').count(),1);assert.equal(await page.evaluate(()=>window.mock.launches),0);assert.equal(await page.getByRole('dialog').count(),0);
 report.checks.push('home renders valid archives with runtime unavailable and no first-use modal');
 await page.evaluate(()=>{window.mock.offline=false;});await page.getByRole('button',{name:'设置运行环境'}).click();
 await page.locator('.first-run-panel').getByRole('heading',{name:'就绪',exact:true}).waitFor();
 assert.equal(startRequests,1);assert.equal(await page.getByRole('button',{name:'开始连接',exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'更新战斗资源包'}).isVisible(),false);
 await page.getByText('高级选项',{exact:true}).click();
 await page.locator('.first-run-panel').getByRole('button',{name:'更新战斗资源包'}).waitFor();
 assert.equal(await page.locator('.native-sidebar').isVisible(),false);assert.equal(await page.locator('.playback-controls').isVisible(),false);
 await mkdir('.local-runtime/native-ux-check',{recursive:true});
 await page.getByText('高级选项',{exact:true}).click();
 await page.screenshot({path:'.local-runtime/native-ux-check/automatic-setup.png'});
 report.checks.push('settings connects automatically once; ready state has no Connect button; maintenance tools are collapsed');
 assert.equal(await page.locator('.help-dialog').count(),0);
 await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();
 await page.evaluate(()=>{window.mock.offline=true;});
 report.checks.push('runtime settings remains accessible before any project exists');
 runtimeReady=false;failStart=true;
 await page.evaluate(()=>{window.mock.offline=false;});await page.getByRole('button',{name:'设置运行环境'}).click();
 await page.locator('.first-run-panel .setup-error').waitFor();
 const failedStarts=startRequests;await page.waitForTimeout(400);assert.equal(startRequests,failedStarts);
 failStart=false;await page.locator('.first-run-panel').getByRole('button',{name:'重试',exact:true}).click();
 await page.locator('.first-run-panel').getByRole('heading',{name:'就绪',exact:true}).waitFor();
 assert.equal(startRequests,failedStarts+1);
 await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();
 await page.evaluate(()=>{window.mock.offline=true;});
 report.checks.push('startup failure does not auto-loop; one retry resumes the automatic connection');

 await page.reload();await page.locator('.archive-entry').waitFor();
 await page.getByRole('button',{name:'打开其他文件'}).click();await page.locator('.native-notice').filter({hasText:'不支持此文件'}).waitFor();
 assert(!(await page.evaluate(()=>window.mock.reads)).some(path=>path.includes('old')));assert.deepEqual(await page.evaluate(()=>window.mock.writes),[]);
 report.checks.push('manual unsupported file safely rejected before read/write');
 await page.evaluate(()=>{
  const source=window.mock.source;
  window.mock.journal=JSON.stringify({schema:'cr-native-journal.v1',projectId:source.projectId,engine:source.engine,checkedOutNodeId:source.rootNodeId,workingTick:100,
   actions:[{id:'old-session-card',tick:91,sequence:1,owner:1,kind:'play-card',nativeCardId:source.match.deck1[0],slot:0,nativeX:4500,nativeY:18000}]});
 });
 await page.locator('.archive-entry').getByRole('button',{name:'打开',exact:true}).click();await page.locator('.native-notice').filter({hasText:'运行环境连接失败'}).waitFor();
 assert.equal(await page.locator('.help-dialog').count(),0);
 await page.evaluate(()=>{window.mock.offline=false;});await page.locator('.first-run-panel').getByRole('button',{name:'重试',exact:true}).click();
 await page.locator('.help-dialog').waitFor();assert.equal(await page.locator('.help-dialog h3').count(),9);await page.locator('.help-dialog').getByRole('button',{name:'关闭'}).click();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 report.checks.push('open intent survives unavailable runtime; guide appears only after configured battle');
 assert.equal(await page.locator('.confirmation-dialog').count(),0);
 assert.equal(await page.locator('.draft-note').count(),0);
 assert.equal(await page.getByLabel('时间跳转').inputValue(),'90');
 assert.equal(await page.evaluate(()=>window.mock.journal),null);
 assert((await page.evaluate(()=>window.mock.journalClears))>0);
 report.checks.push('opening an archive with an old draft, including retrying unavailable runtime, starts at the saved node without a cache prompt or old actions');

 const input=page.locator('.native-inspector input');const originalTitle=await input.inputValue();await input.fill('edited title');await input.blur();
 await page.evaluate(()=>{window.mock.failSave=true;});await page.getByRole('button',{name:'撤回',exact:true}).click();await page.locator('.native-notice').filter({hasText:'Simulated file save failure'}).waitFor();assert.equal(await input.inputValue(),'edited title');
 await page.evaluate(()=>{window.mock.failSave=false;});await page.locator('.battle-clock').click();await page.keyboard.press('Control+z');await page.locator('.native-notice').filter({hasText:'已撤回'}).waitFor();assert.equal(await input.inputValue(),originalTitle);
 report.checks.push('failed metadata undo retains current value and history; global Ctrl+Z retries and safely saves');
 const note=page.locator('.native-inspector textarea');await note.fill('用户备注');await note.blur();await page.getByRole('button',{name:'Language'}).click();assert.equal(await note.inputValue(),'用户备注');assert.equal(await page.getByLabel('Replay name').inputValue(),'保留用户标题');await page.getByRole('button',{name:'Language'}).click();
 await page.evaluate(()=>{window.mock.failSave=true;});await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-notice').filter({hasText:'Simulated file save failure'}).waitFor();assert.equal(await page.locator('.native-home').count(),0);assert.equal(await note.inputValue(),'用户备注');
 await page.evaluate(()=>{window.mock.failSave=false;});await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();assert.equal(await page.evaluate(()=>window.mock.source.nodes[window.mock.source.rootNodeId].note),'用户备注');
 report.checks.push('UI language preserves user content; home save failure retains workspace; retry safely saves');
 const framesBeforeReturn=freshFrames;
 await page.getByRole('button',{name:'继续上次复盘'}).click();
 await page.locator('.native-surface').waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(freshFrames,framesBeforeReturn);
 assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),'cache');
 report.checks.push('returning from home reuses verified node picture without another fresh screenshot');
 await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();
 resourcesReady=false;runtimeReady=false;await page.reload();await page.locator('.archive-entry').waitFor();
 await page.evaluate(()=>{window.mock.offline=false;window.mock.selected='C:/test/resources.zip';});
 await page.locator('.archive-entry').getByRole('button',{name:'打开',exact:true}).click();
 await page.locator('.first-run-panel').getByRole('button',{name:'导入战斗资源包'}).click();
 await page.locator('.native-surface').waitFor();await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(await page.getByLabel('战斗名称').inputValue(),'保留用户标题');
 report.checks.push('resource setup completion automatically starts runtime and resumes the original open intent');
 await page.getByRole('region',{name:'我方八张牌序'}).getByRole('button').first().click();
 const recoveryArena=page.getByLabel('原生竞技场'),recoveryBox=await recoveryArena.boundingBox();
 await recoveryArena.click({position:{x:recoveryBox.width*.3,y:recoveryBox.height*.72}});
 await page.locator('.draft-note').waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 const currentDraft=await page.evaluate(()=>JSON.parse(window.mock.journal));
 failNextFrame=true;await page.getByRole('button',{name:'前进一步 │▶',exact:true}).click();
 await page.locator('.native-recovery').waitFor();
 await page.getByRole('button',{name:'恢复连接',exact:true}).click();
 await page.locator('.native-notice').filter({hasText:'项目已载入'}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(await page.locator('.confirmation-dialog').count(),0);
 assert.equal(await page.locator('.draft-note').count(),1);
 assert.deepEqual((await page.evaluate(()=>JSON.parse(window.mock.journal))).actions,currentDraft.actions);
 report.checks.push('explicit recovery after a current-session frame failure restores confirmed draft actions without an old-cache confirmation');

 const beforeStep=freshFrames;
 await page.getByRole('button',{name:'前进一步 │▶',exact:true}).click();
 await page.locator('.native-notice').filter({hasText:'已前进一帧'}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert(freshFrames>beforeStep);
 const beforeSave=freshFrames;
 await page.getByRole('button',{name:'保存当前节点',exact:true}).click();
 await page.locator('.native-notice').filter({hasText:'已保存为子节点'}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(freshFrames,beforeSave);
 assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),'cache');
 report.checks.push('changed working tick fetches a fresh picture; saving that same state reuses it');
 // Observe video work separately from card rendering. Catalog lookups happen
 // in hand rendering; video-only updates must not repeat those lookups.
 await page.evaluate(()=>{
  window.handLookups=0;const find=Array.prototype.find;
  Array.prototype.find=function(...args){if(this.length===122&&this[0]?.engineName)window.handLookups++;return find.apply(this,args);};
 });
 await page.getByRole('button',{name:'▶ 播放',exact:true}).click();
 await page.getByRole('button',{name:'Ⅱ 暂停',exact:true}).waitFor();
 const beforeLive=liveFrames;
 await page.evaluate(()=>{window.handLookups=0;});
 await page.waitForTimeout(2000);
 const lookups=await page.evaluate(()=>window.handLookups);
 assert(liveFrames-beforeLive>=40,'Live video loop stalled');
 // StrictMode renders twice, and status/observation arrive separately at 10 Hz.
 assert(lookups<2400,`Video frames repeatedly rendered card lists: ${lookups}`);
 await page.getByRole('button',{name:'Ⅱ 暂停',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 const pausedFrames=liveFrames;
 await page.waitForTimeout(200);
 assert.equal(liveFrames,pausedFrames);
 assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-revision'),await page.locator('.native-surface').getAttribute('data-control-revision'));
 report.checks.push('live frames do not render card lists; pause stops polling and confirms the current frame');
 await page.getByRole('button',{name:'初始配置（只读）',exact:true}).click();
 assert.equal(await page.getByLabel('我方塔楼牌').inputValue(),'159000000');
 assert(await page.getByLabel('敌方塔楼牌').isDisabled());
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'新建存档',exact:true}).click();
 assert.equal(await page.getByRole('button',{name:'恢复标准卡组',exact:true}).count(),0);
 const catalogIds=()=>page.locator('.card-catalog button').evaluateAll(cards=>cards.map(card=>Number(card.dataset.cardId)));
 assert.deepEqual(await catalogIds(),NATIVE_CARD_CATALOG.map(card=>card.id));
 await page.getByLabel('搜索卡牌').fill('哥布林');
 const filtered=await catalogIds();assert(filtered.length>1);
 assert.deepEqual(filtered,NATIVE_CARD_CATALOG.filter(card=>filtered.includes(card.id)).map(card=>card.id));
 await page.getByLabel('搜索卡牌').fill('');
 const selected=id=>page.locator(`.selected-deck-card[data-card-id="${id}"]`);
 await selected(26000000).locator('select').selectOption('1');
 await selected(26000001).locator('select').selectOption('1');
 await selected(26000014).locator('select').selectOption('2');
 assert.equal(await selected(26000003).locator('option[value="2"]').isDisabled(),true);
 assert.equal(await selected(26000000).getAttribute('data-special-slot'),'evolutionSlot');
 assert.equal(await selected(26000001).getAttribute('data-special-slot'),'wildSlot');
 assert.equal(await selected(26000014).getAttribute('data-special-slot'),'heroSlot');
 await selected(26000000).getByRole('button',{name:'将骑士后移'}).click();
 assert.equal(await selected(26000000).locator('select').inputValue(),'1');
 assert.equal(await selected(26000001).locator('select').inputValue(),'1');
 await page.getByRole('button',{name:'敌方 · 8/8',exact:true}).click();
 assert.equal(await page.locator('.deck-special-slot[data-occupied="true"]').count(),0);
 await page.getByRole('button',{name:'我方 · 8/8',exact:true}).click();
 assert.equal(await page.locator('.deck-special-slot[data-occupied="true"]').count(),3);
 await mkdir('.local-runtime/native-ux-check',{recursive:true});
 await page.screenshot({path:'.local-runtime/native-ux-check/deck-slots.png'});
 const clearDeck=async()=>{while(await page.locator('.selected-deck-card').count())await page.locator('.initial-order-tools button').last().click();};
 const addCard=async id=>page.locator(`.card-catalog button[data-card-id="${id}"]`).click();
 await clearDeck();
 const champions=NATIVE_CARD_CATALOG.filter(card=>card.rarity==='champion');
 await addCard(champions[0].id);await addCard(champions[1].id);
 assert.equal(await page.locator(`.card-catalog button[data-card-id="${champions[2].id}"]`).isDisabled(),true);
 assert.equal(await selected(champions[0].id).locator('select').count(),0);
 assert.equal(await selected(champions[0].id).locator('.initial-form').innerText(),'英雄');
 await addCard(26000000);await selected(26000000).locator('select').selectOption('1');
 assert.equal(await selected(26000000).locator('option[value="2"]').isDisabled(),true);
 await addCard(26000001);assert.equal(await selected(26000001).locator('option[value="1"]').isDisabled(),true);
 await clearDeck();for(const id of STANDARD_NATIVE_DECK)await addCard(id);
 report.checks.push('catalog sorted by elixir and rarity, including search; no reset button; special slots track forms, Champions, hand moves and independent sides');
 assert.equal(await page.getByLabel('我方塔楼牌').locator('option').count(),4);
 await page.getByLabel('我方塔楼牌').selectOption('159000004');
 await page.getByLabel('敌方塔楼牌').selectOption('159000002');
 await page.getByRole('button',{name:'创建存档并开始',exact:true}).click();
 await page.waitForFunction(()=>window.mock.source.match.tower1===159000004);
 const towerConfig=await page.evaluate(()=>({match:window.mock.source.match,reset:window.mock.reset}));
 assert.equal(towerConfig.reset.tower0,159000002);assert.equal(towerConfig.reset.tower1,159000004);
 assert.equal(towerConfig.match.tower0,159000002);assert.equal(towerConfig.match.tower1,159000004);
 await page.getByRole('button',{name:'初始配置（只读）',exact:true}).click();
 assert.equal(await page.getByLabel('我方塔楼牌').inputValue(),'159000004');
 assert.equal(await page.getByLabel('敌方塔楼牌').inputValue(),'159000002');
 assert(await page.getByLabel('我方塔楼牌').isDisabled());
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'Language',exact:true}).click();
 await page.getByRole('button',{name:'Initial setup (read-only)',exact:true}).click();
 assert.equal(await page.getByLabel('You Tower Troop').inputValue(),'159000004');
 assert.equal(await page.getByLabel('Opponent Tower Troop').inputValue(),'159000002');
 report.checks.push('legacy archives show default Princess; independent Tower Troops survive language switch, reset, save and locked config');
 await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
 await page.getByRole('button',{name:'Home',exact:true}).click();await page.locator('.native-home').waitFor();
 environmentReady=false;resourcesReady=false;runtimeReady=false;
 await page.evaluate(()=>{localStorage.setItem('clash-native.language','zh-CN');});
 await page.reload();await page.locator('.native-home').waitFor();await page.evaluate(()=>{window.mock.offline=false;});
 const beforeInstallStarts=startRequests;
 await page.getByRole('button',{name:'设置运行环境',exact:true}).click();
 await page.locator('.sdk-terms input').waitFor();assert.equal(startRequests,beforeInstallStarts);assert.equal(installRequests,0);
 await page.locator('.sdk-terms input').check();
 await page.getByRole('button',{name:'导入战斗资源包',exact:true}).waitFor();assert.equal(installRequests,1);
 await page.evaluate(()=>{window.mock.selected='C:/test/resources.zip';});
 await page.getByRole('button',{name:'导入战斗资源包',exact:true}).click();
 await page.locator('.first-run-panel').getByRole('heading',{name:'就绪',exact:true}).waitFor();
 assert.equal(startRequests,beforeInstallStarts+1);
 report.checks.push('first use waits for SDK consent; consent automatically installs; selecting resources automatically connects');
 assert.deepEqual(report.errors,[]);report.status='passed';
} catch(error){report.status='failed';report.error=String(error);throw error;}
finally {await browser.close();await mkdir('.local-runtime/native-ux-check',{recursive:true});await writeFile('.local-runtime/native-ux-check/frontend-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));}
