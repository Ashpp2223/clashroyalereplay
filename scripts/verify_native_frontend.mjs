import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {openDesktopHarness} from './desktop_control.mjs';
import {createNativeProject,STANDARD_NATIVE_DECK,NATIVE_CARD_CATALOG,createNativeCheckpoint,checkoutNativeNode,advanceNativeWork} from '../src/domain/nativeReplay.ts';

// Component-only geometry check: no desktop input, user files or engine connection.
async function verifySurfaceLayout() {
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const report={status:'running',cases:[],errors:[]};
 try {
  const page=await browser.newPage();
  page.on('pageerror',error=>report.errors.push(String(error)));
  page.on('console',event=>{if(event.type()==='error')report.errors.push(event.text());});
  page.on('requestfailed',request=>report.errors.push(`${request.url()}: ${request.failure()?.errorText}`));
  await page.route('**/src/main.tsx*',route=>route.fulfill({contentType:'application/javascript',body:`
   import React from '/node_modules/.vite/deps/react.js';
   import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
   import NativeSurface from '/src/native/NativeSurface.tsx';
   import '/src/native.css';
   const root=ReactDOM.createRoot(document.getElementById('root'));
   const deck=[26000000,26000001,26000002,26000003,26000004,26000005,26000006,26000007];
   window.renderSurfaceCase=async(missing,zoom,abilityState='none')=>{
    const players=[0,1].map(owner=>({owner,elixir:100000,
     hand:deck.slice(0,4).flatMap((card,slot)=>slot<missing?[]:[{slot,card,cost:3}]),
     cycle:deck.slice(4).concat(deck.slice(0,missing)),runtime:{abilityRuntime:abilityState==='none'?[]:
      ['ArcherQueenRapid','Knight_hero_Ability','ElectroWizardHero_Ability'].map((actionDataName,controllerSlot)=>({
       actionDataName,controllerSlot,actionDataGlobalId:100+controllerSlot,
       available:abilityState!=='unavailable',remainingCooldownMs:abilityState==='cooldown'?1500:0,
       championEntityKeys:abilityState==='absent'?[]:[{owner,secondaryIndex:owner*10+controllerSlot+1}]}))}}));
    root.render(React.createElement('div',{className:'native-app',style:{zoom,height:(100/zoom)+'vh'}},
     React.createElement('div',{style:{height:180,flex:'none'}}),
     React.createElement('div',{className:'battle-stage'},React.createElement(NativeSurface,{
      bridge:null,ready:true,profile:{arena:[54,282,972,1384]},selected:null,
      observation:{players},disabled:false,paused:true,framePending:abilityState==='frame-pending',frameRevision:0,
      onFrameRendered:()=>{},onSelectCard:()=>{},onActivateAbility:()=>{},onArenaClick:()=>{},onUnavailable:()=>{}}))));
    await document.fonts.ready;
    for(let i=0;i<5;i++)await new Promise(requestAnimationFrame);
   };
  `}));
  await page.goto('http://127.0.0.1:1420/');
  await page.waitForFunction(()=>Boolean(window.renderSurfaceCase),null,{polling:100,timeout:10000});
  for(const [width,height,zoom] of [[1440,960,1],[1280,720,.75]]) {
   await page.setViewportSize({width,height});
   let baseline;
   for(const missing of [0,1,2,4,0]) {
    await page.evaluate(({missing,zoom})=>window.renderSurfaceCase(missing,zoom),{missing,zoom});
    const geometry=await page.evaluate(()=>{
     const rect=element=>{const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
     return {surface:rect(document.querySelector('.native-surface')),arena:rect(document.querySelector('.native-arena-frame')),
      hands:[...document.querySelectorAll('.native-hand')].map(rect),
      queues:[...document.querySelectorAll('.native-hand-group.queue')].map(group=>[...group.querySelectorAll('button')].map(rect)),
      art:[...document.querySelectorAll('.hand-card-art')].map(element=>({box:rect(element),content:rect(element.firstElementChild)}))};
    });
    report.cases.push({width,height,zoom,missing,geometry});
    baseline??=geometry;
    for(const key of ['surface','arena','hands'])assert.deepEqual(geometry[key],baseline[key],`Refilling ${missing} slots changed ${key} at ${width}x${height}`);
    for(const queue of geometry.queues)assert(queue.every(card=>Math.abs(card.y-queue[0].y)<.1),'Queue wrapped during refill');
    for(const art of geometry.art)assert(Math.abs(art.box.height-art.content.height)<.1,'Placeholder art changed size');
   }
   for(const abilityState of ['ready','cooldown','absent','unavailable','frame-pending','none']) {
    await page.evaluate(({zoom,abilityState})=>window.renderSurfaceCase(0,zoom,abilityState),{zoom,abilityState});
    await page.evaluate(()=>Promise.all([...document.querySelectorAll('.native-ability-icon')].map(image=>image.decode())));
    const geometry=await page.evaluate(()=>{
     const rect=element=>{const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
     return {surface:rect(document.querySelector('.native-surface')),arena:rect(document.querySelector('.native-arena-frame')),
      hands:[...document.querySelectorAll('.native-hand')].map(rect),
      abilities:[...document.querySelectorAll('.native-abilities')].map(group=>({own:group.classList.contains('own'),
       buttons:[...group.querySelectorAll('button')].map(button=>({box:rect(button),disabled:button.disabled,
        image:button.querySelector('.native-ability-icon').getAttribute('src'),cooldown:button.querySelector('.native-ability-cooldown')?.textContent}))}))};
    });
    report.cases.push({width,height,zoom,abilityState,geometry});
    for(const key of ['surface','arena','hands'])assert.deepEqual(geometry[key],baseline[key],`Ability ${abilityState} changed ${key}`);
    for(const group of geometry.abilities)for(const button of group.buttons) {
     assert.equal(button.disabled,abilityState!=='ready');
     assert(button.box.x>=geometry.arena.x+geometry.arena.width,'Skill overlaps arena pixels');
     assert(button.box.x+button.box.width<=geometry.surface.x+geometry.surface.width,'Skill exceeds surface');
     assert(button.box.y>=geometry.arena.y && button.box.y+button.box.height<=geometry.arena.y+geometry.arena.height,'Skill exceeds arena rail');
     assert.equal(button.box.y>geometry.arena.y+geometry.arena.height/2,group.own,'Owner skill is in the wrong corner');
     assert.equal(button.cooldown,abilityState==='cooldown'?'1.5s':undefined);
    }
   }
  }
  assert.deepEqual(report.errors,[]);report.status='passed';
 }catch(error){report.status='failed';report.error=String(error);throw error;}
 finally {await browser.close();await mkdir('.local-runtime/native-surface-layout',{recursive:true});await writeFile('.local-runtime/native-surface-layout/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,cases:report.cases.length,error:report.error}));}
}
if(process.argv.includes('--surface-layout')) {await verifySurfaceLayout();process.exit(0);}
const identity={upstreamCommit:'commit',probeSha256:'probe',apkSha256:'apk',libgSha256:'libg',runtimeFingerprintSha256:'runtime',protocolVersion:'v1',cropProfileId:'stock'};
let source=createNativeProject(identity,{seed:41,preset:'standard',deck0:[...STANDARD_NATIVE_DECK],deck1:[...STANDARD_NATIVE_DECK]},90,'root','保留用户标题');
const edgeFixture={};
if(process.argv.includes('--edge-playback')) {
 for(const [key,parent,tick] of [['B',null,110],['C',null,120],['D','B',140],['E','B',150],['F','C',130]]) {
  const base=checkoutNativeNode(source,parent?edgeFixture[parent]:source.rootNodeId);
  const result=createNativeCheckpoint(source,advanceNativeWork(base,tick),key);
  result.project.nodes[result.node.id].title=key;source=result.project;edgeFixture[key]=result.node.id;
 }
 source.nodes[edgeFixture.B].actionsFromParent=[{id:'B-card',tick:95,sequence:0,owner:1,kind:'play-card',nativeCardId:STANDARD_NATIVE_DECK[0],slot:0,nativeX:3500,nativeY:13000}];
}
const path='C:/test/saves/test.cr-native-replay.json';
const report={kind:'frontend with mocked files and bridge; no real engine',status:'running',checks:[],errors:[]};
const harness=process.argv.includes('--desktop')?await openDesktopHarness({app:'src-tauri/target/debug/clash-replay-tree.exe',
 home:'.local-runtime/layout-check',runtime:process.env.LOCALAPPDATA+'/cr-replay-clean-test',devUrl:'http://127.0.0.1:1420'}):null;
const browser=harness?null:await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
try {
 const page=harness?.page??await browser.newPage({viewport:{width:1440,height:960}});
 page.setDefaultTimeout(30000);
 if(harness)await page.evaluate(()=>localStorage.clear());
 report.host=await page.evaluate(()=>({kind:window.__TAURI_INTERNALS__?'WebView2':'browser',dpr:devicePixelRatio,viewport:[innerWidth,innerHeight]}));
 const devtools=harness?await page.context().newCDPSession(page):null;
 const resize=async(width,height)=>{
  await page.setViewportSize({width,height});
  if(devtools)await devtools.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:report.host.dpr,mobile:false});
  await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
 };
 page.on('pageerror',error=>report.errors.push(String(error)));
 if(process.argv.includes('--archive-delete'))await page.route('**/src/NativeApp.tsx*',async route=>{
  const response=await route.fetch();
  const body=(await response.text()).replace('const archiveContents =',
   'window.testArchiveActions = {removeArchive, finishConfirmation, openProject, returnHome, entries:archives, loadedProject:project}; const archiveContents =');
  assert(body.includes('window.testArchiveActions ='),'Archive test API instrumentation did not match');
  await route.fulfill({response,body});
 });
 if(process.argv.includes('--edge-playback'))await page.route('**/src/NativeApp.tsx*',async route=>{
  const response=await route.fetch();
  const body=(await response.text()).replace('const archiveContents =',
   'window.testPlayback = {playEdge,togglePlay,stepForward,checkpoint,saveProject,checkout,seek,changeSpeed,copyNode,undo,selectHand,playAt,activateAbility,showArchives,openProject,branch,project,selected,route:scrubRoute,busy,status}; const archiveContents =');
  assert(body.includes('window.testPlayback ='),'Playback test API instrumentation did not match');
  await route.fulfill({response,body});
 });
 // Instrument the exact Vite module used by the app, including HMR query IDs.
 // A later bare import can create a second class after a development edit.
 await page.route('**/src/native/bridge.ts*',async route=>{
  const response=await route.fetch();
  const body=(await response.text()).replace('async frame(fresh = false, signal) {',
   'async frame(fresh = false, signal) { window.testFrameCache = this.frameCache;');
  assert(body.includes('window.testFrameCache = this.frameCache;'),'Frame cache instrumentation did not match the served module');
  await route.fulfill({response,body});
 });
 if(harness)await page.route('**/node_modules/.vite/deps/*.js*',async route=>{
  const response=await route.fetch();
  const body=(await response.text()).replaceAll('window.__TAURI_INTERNALS__.invoke','window.mockInvoke');
  await route.fulfill({response,body});
 });
 await page.addInitScript(({source,path})=>{
  const data={source,path,reads:[],writes:[],launches:0,offline:true,failSave:false,failDelete:false,deleted:false,deletions:[],journal:null,journalClears:0,selected:'C:/test/saves/old.cr-replay.json'};
  window.mock=data;
  window.mockInvoke=async(command,args)=>{
   if(command==='native_test_workspace')return 'C:/test';
   if(command==='plugin:path|join')return args.paths.join('/');
   if(command==='ensure_writable_directory')return;
   if(command==='list_project_files')return [...(data.deleted?[]:[{name:'test.cr-native-replay.json',path}]),{name:'old.cr-replay.json',path:'C:/test/saves/old.cr-replay.json'},{name:'invalid.cr-native-replay.json',path:'C:/test/saves/invalid.cr-native-replay.json'}];
   if(command==='delete_native_project'){if(data.failDelete)throw Error('Simulated file deletion failure');data.deletions.push(args);data.deleted=true;return;}
   if(command==='read_project_file'){data.reads.push(args.path);if(args.path===path)return JSON.stringify(data.source);if(args.path.includes('old'))return JSON.stringify({version:1,nodes:{}});return 'invalid';}
   if(command==='plugin:dialog|open')return data.selected;
   if(command==='write_project_file'){if(data.failSave)throw Error('Simulated file save failure');data.writes.push(args.path);data.source=JSON.parse(args.data);return;}
   if(command==='start_native_bridge'){data.launches++;if(data.offline)throw Error('Mock runtime unavailable');return {port:26791,token:'mock-test-only',protocol:'native-bridge.v1'};}
   if(command==='read_native_journal')return data.journal;
   if(command==='clear_native_journal'){data.journal=null;data.journalClears++;return;}
   if(command==='write_native_journal'){data.journal=args.data;return;}
   throw Error('Unhandled mock command: '+command);
  };
  if(!window.__TAURI_INTERNALS__)window.__TAURI_INTERNALS__={invoke:window.mockInvoke};
 },{source,path});
 const cropProfile={id:'stock',nativeWidth:1080,nativeHeight:1920,enemyHud:[0,0,1080,282],arena:[54,282,972,1384],myHud:[0,1666,1080,254],world:[111,436,856,1230],handCentersX:[211,354,497,640],enemyHandReversed:true};
 let environmentReady=true,installRequests=0,resourcesReady=true,runtimeReady=false,playing=false,startRequests=0,startPolls=0,failStart=false,setupError=null;
 let state={tick:90,digest:'root',finalized:false,result:null,crowns:[0,0],entities:[],players:[0,1].map(owner=>({owner,elixir:76000,hand:STANDARD_NATIVE_DECK.slice(0,4).map((card,slot)=>({card,slot,cost:3})),cycle:STANDARD_NATIVE_DECK.slice(4),runtime:{abilityRuntime:[]}}))};
 let image=await readFile('public/elixir.png');
 let freshFrames=0,liveFrames=0,failNextFrame=false,failNextSnapshot=false;
 let nextSnapshot=1;
 const releasedSnapshots=[];
 const replayRequests=[];
 const playbackRequests=[];
 let playbackCompleted=false,resumeRequests=0,stepRequests=0,observeDelay=0;
 let replayDelay=0;
 await page.route('http://127.0.0.1:26791/**',async route=>{
  const url=new URL(route.request().url());const body=route.request().postDataJSON();
  if(url.pathname==='/v1/frame'){if(failNextFrame){failNextFrame=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Simulated capture failure'})});}if(url.searchParams.has('fresh'))freshFrames++;else liveFrames++;return route.fulfill({status:200,contentType:'image/png',body:image});}
  let response;
  if(url.pathname==='/v1/start'){startRequests++;startPolls=3;}
  if(url.pathname==='/v1/status' && startPolls>0 && --startPolls===0 && !failStart)runtimeReady=true;
  if(url.pathname==='/v1/status'||url.pathname==='/v1/start')response={ready:runtimeReady,playing,playback:playbackRequests.length?{completed:playbackCompleted,targetTick:playbackRequests.at(-1).targetTick}:null,error:failStart?'Mock startup failed':null,stage:runtimeReady?'Mock ready':'Starting Android',stageCode:runtimeReady?'ready':'emulator',identity,cropProfile,capabilities:{},environmentConfigured:environmentReady,resourcesConfigured:resourcesReady,setup:{state:setupError?'failed':'idle',error:setupError}};
  else if(url.pathname==='/v1/setup/install'){assert.equal(body.acceptLicense,true);installRequests++;environmentReady=true;response={state:'ready'};}
  else if(url.pathname==='/v1/setup/resources'){resourcesReady=true;setupError=null;response={state:'ready'};}
  else if(url.pathname==='/v1/suspend')response={playing:false};
  else if(url.pathname==='/v1/snapshot-create')response={tick:state.tick,handle:nextSnapshot++};
  else if(url.pathname==='/v1/snapshot-release'){releasedSnapshots.push(body.handle);response={};}
  else if(url.pathname==='/v1/replay'){
   playing=false;playbackCompleted=false;
   replayRequests.push(body);
   if(body.snapshotHandle&&failNextSnapshot){failNextSnapshot=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Expired snapshot'})});}
   if(replayDelay)await new Promise(done=>setTimeout(done,replayDelay));
   state={...state,tick:body.targetTick,digest:body.expectedDigest??'root'};
   response={...state,snapshots:(body.checkpoints??[]).map(checkpoint=>({...checkpoint,handle:nextSnapshot++}))};
  }
  else if(url.pathname==='/v1/reset'){await page.evaluate(body=>{window.mock.reset=body;},body);state={...state,tick:90,digest:'tower-root'};response=state;}
  else if(url.pathname==='/v1/play-card'){
   const card=state.players[body.owner].hand.find(card=>card.slot===body.slot);
   state={...state,tick:state.tick+1,digest:`draft-${state.tick+1}`};
   response={accepted:true,observation:state,action:{tick:state.tick,sequence:1,owner:body.owner,kind:'play-card',nativeCardId:card.card,nativeForm:card.form??0,slot:body.slot,nativeX:body.x,nativeY:body.y}};
  }
  else if(url.pathname==='/v1/step'){stepRequests++;state={...state,tick:state.tick+body.ticks,digest:`tick-${state.tick+body.ticks}`};response=state;}
  else if(url.pathname==='/v1/pause'){playing=false;response=state;}
  else if(url.pathname==='/v1/resume'){resumeRequests++;playing=true;playbackCompleted=false;response={playing:true};}
  else if(url.pathname==='/v1/speed')response={speed:body.multiplier};
  else if(url.pathname==='/v1/playback'){playbackRequests.push(body);playbackCompleted=body.targetTick===state.tick;playing=!playbackCompleted;response={...state,playing};}
  else if(url.pathname==='/v1/observe'){response=state;if(observeDelay)await new Promise(done=>setTimeout(done,observeDelay));}
  else throw Error('Unhandled mock route '+url.pathname);
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(response)});
 });
 await page.goto('http://127.0.0.1:1420/');await page.locator('.archive-entry').waitFor();
 assert.equal(await page.locator('.archive-entry').count(),1);assert.equal(await page.evaluate(()=>window.mock.launches),0);assert.equal(await page.getByRole('dialog').count(),0);
 report.checks.push('home renders valid archives with runtime unavailable and no first-use modal');
 if(process.argv.includes('--edge-playback')) {
  runtimeReady=true;
  await page.evaluate(()=>{window.mock.offline=false;localStorage.setItem('clash-native.help-seen.v1','1');});
  await page.evaluate(path=>window.testPlayback.openProject(path),path);
  await page.waitForFunction(()=>window.testPlayback?.status?.ready&&!window.testPlayback.busy&&document.querySelector('.tree-node'));
  assert.equal(await page.locator('.native-hand-group.current button:not(:disabled)').count(),0);
  await page.evaluate(()=>window.testPlayback.activateAbility(1,123,456));
  const hitEdge=await page.evaluate(id=>{
   const path=document.querySelector(`[data-edge-id="${id}"] .tree-edge-hit`);
   const point=path.getPointAtLength(path.getTotalLength()/2),matrix=path.getScreenCTM();
   const screen=new DOMPoint(point.x,point.y).matrixTransform(matrix);
   return document.elementFromPoint(screen.x,screen.y)?.closest('[data-edge-id]')?.getAttribute('data-edge-id');
  },edgeFixture.B);
  assert.equal(hitEdge,edgeFixture.B,'The visible edge must be hit-testable, not intercepted by tree panning');
  await page.evaluate(()=>window.testPlayback.togglePlay());
  assert.equal(resumeRequests,0);assert.equal(playbackRequests.length,0);
  await page.getByRole('status').filter({hasText:'请点击下一条连线'}).waitFor();
  report.checks.push('A with siblings B/C pauses for an explicit edge choice, including the Play button');
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.B);
  await page.waitForFunction(()=>Boolean(document.querySelector('.tree-edge.active animateMotion')));
  assert.equal(await page.locator('.tree-node.active').count(),0,'An interval must not mark its parent as the current point');
  assert.equal(await page.locator('.native-inspector input,.native-inspector textarea').count(),0,'Interval details must not edit the parent checkpoint');
  assert.equal(playbackRequests.at(-1).targetTick,110);
  assert.equal(await page.locator('.tree-edge.active animateMotion').getAttribute('repeatCount'),'indefinite');
  assert.equal(await page.locator('.tree-edge.active').getAttribute('data-edge-id'),edgeFixture.B);
  state={...state,tick:100,digest:'mid-B'};
  await page.waitForFunction(()=>document.querySelector('.tree-edge-progress-label')?.textContent==='50%');
  assert.equal(await page.locator('.draft-note').count(),0,'Recorded actions are not a working draft');
  await page.evaluate(()=>window.testPlayback.saveProject());
  assert.deepEqual(await page.evaluate(()=>window.mock.source.nodes),source.nodes,'Saving while viewing must not create or extend a node');
  assert.equal(await page.evaluate(()=>window.mock.journal),null);
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.waitForFunction(()=>document.querySelector('.tree-links').animationsPaused()&&document.querySelector('.tree-flow-ball.paused'));
  const pausedTime=await page.evaluate(()=>document.querySelector('.tree-links').getCurrentTime());
  await new Promise(done=>setTimeout(done,180));
  assert.equal(await page.evaluate(()=>document.querySelector('.tree-links').getCurrentTime()),pausedTime,'Loop animation must freeze without jumping to the tick percentage');
  const pausedReplayCount=replayRequests.length;
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.B);
  assert.equal(replayRequests.length,pausedReplayCount,'Selecting the paused active edge must not rewind');
  assert.equal(playbackRequests.at(-1).expectedTick,100);
  await page.waitForFunction(()=>!document.querySelector('.tree-links').animationsPaused());
  const activeRequestCount=playbackRequests.length;
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.B);
  assert.equal(playbackRequests.length,activeRequestCount,'Selecting an already playing edge must not restart it');
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.evaluate(()=>window.testPlayback.seek(99));
  assert.equal(replayRequests.at(-1).targetTick,99);
  assert.equal(await page.evaluate(()=>window.testPlayback.route.edgeId),edgeFixture.B);
  assert.equal(await page.locator('.native-hand-group.current button:not(:disabled)').count(),0);
  await page.evaluate(()=>window.testPlayback.changeSpeed(2));
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.waitForFunction(()=>Boolean(document.querySelector('animateMotion')));
  assert.equal(playbackRequests.at(-1).expectedTick,99);
  state={...state,tick:110,digest:'B'};playing=false;playbackCompleted=true;
  await page.waitForFunction(id=>window.testPlayback.branch.checkedOutNodeId===id&&!window.testPlayback.route?.edgeId,edgeFixture.B);
  assert.equal(await page.locator('animateMotion').count(),0);
  const requestCount=playbackRequests.length;
  await page.evaluate(()=>window.testPlayback.togglePlay());
  assert.equal(playbackRequests.length,requestCount);assert.equal(resumeRequests,0);
  await page.evaluate(()=>window.testPlayback.stepForward());
  assert.equal(stepRequests,0);assert.equal(playbackRequests.length,requestCount);
  report.checks.push('AB loops independently of ticks, freezes/resumes at its animation phase, and never restarts when its active edge is selected');
  report.checks.push('At fork B both Play and Step wait for D/E; viewing and saving recorded actions never creates drafts or changes saved nodes');
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.C);
  assert.equal(playbackRequests.at(-1).expectedTick,90);
  assert.equal(playbackRequests.at(-1).targetTick,120);
  const beforeContinuationReplays=replayRequests.length;
  const beforeNodeArrivalResumes=resumeRequests,beforeNodeArrivalEdges=playbackRequests.length;
  state={...state,tick:120,digest:'C'};playing=false;playbackCompleted=true;
  await page.waitForFunction(id=>window.testPlayback.branch.checkedOutNodeId===id&&window.testPlayback.route===null&&window.testPlayback.status.playing&&!window.testPlayback.busy,edgeFixture.C);
  assert.equal(resumeRequests,beforeNodeArrivalResumes+1);assert.equal(playbackRequests.length,beforeNodeArrivalEdges);
  assert.equal(replayRequests.length,beforeContinuationReplays,'Arriving at C must continue the verified state without restoring it again');
  assert.equal(await page.locator('animateMotion').count(),0);
  await page.waitForFunction(()=>document.querySelector('.native-hand-group.current button:not(:disabled)'));
  assert.equal(await page.locator('.tree-node.active').getAttribute('data-node-id'),edgeFixture.C);
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.F);
  assert.equal(playbackRequests.at(-1).expectedTick,120);assert.equal(playbackRequests.at(-1).targetTick,130);
  state={...state,tick:130,digest:'F'};playing=false;playbackCompleted=true;
  await page.waitForFunction(id=>window.testPlayback.branch.checkedOutNodeId===id&&window.testPlayback.route===null&&window.testPlayback.status.playing&&!window.testPlayback.busy,edgeFixture.F);
  assert.equal(await page.locator('.tree-node.active').getAttribute('data-node-id'),edgeFixture.F);
  assert.equal(resumeRequests,beforeNodeArrivalResumes+2);
  await page.evaluate(()=>window.testPlayback.togglePlay());
  assert.equal(state.tick,130);assert.equal(await page.locator('animateMotion').count(),0);
  assert.equal(await page.evaluate(()=>window.mock.journal),null);
  assert.deepEqual(await page.evaluate(()=>window.mock.source.nodes),source.nodes);
  report.checks.push('AC enters simulation at C without auto-selecting CF; explicit CF playback also enters simulation at leaf F, with cards unlocked and no extra restore');
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.C);
  const beforeBoundaryPause=playbackRequests.length;
  const beforeBoundaryResumes=resumeRequests;
  observeDelay=650;state={...state,tick:120,digest:'C'};playing=false;playbackCompleted=true;
  await page.waitForFunction(()=>window.testPlayback.status.playback?.completed&&window.testPlayback.status.playback.targetTick===120);
  assert(await page.evaluate(()=>window.testPlayback.status.playing),'Pause must remain available until the endpoint observation arrives');
  await page.evaluate(()=>window.testPlayback.togglePlay());
  observeDelay=0;
  await new Promise(done=>setTimeout(done,750));
  assert.equal(playbackRequests.length,beforeBoundaryPause,'An explicit pause at a completed boundary must not auto-continue later');
  assert.equal(await page.evaluate(()=>window.testPlayback.route?.edgeId),undefined);
  assert.equal(await page.evaluate(()=>window.testPlayback.branch.checkedOutNodeId),edgeFixture.C);
  assert.equal(resumeRequests,beforeBoundaryResumes);
  assert.equal(await page.evaluate(()=>window.testPlayback.route),null,'An explicitly paused endpoint is an editable node, not a replay interval');
  report.checks.push('Pausing while the endpoint observation is delayed leaves an editable paused C and never overrides the pause');
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.B);
  state={...state,tick:100,digest:'mid-B'};
  await page.waitForFunction(()=>document.querySelector('.tree-edge-progress-label')?.textContent==='50%');
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.evaluate(()=>window.testPlayback.copyNode());
  const copied=await page.evaluate(()=>({project:window.testPlayback.project,branch:window.testPlayback.branch,route:window.testPlayback.route}));
  assert.equal(copied.route,null);assert.notEqual(copied.branch.checkedOutNodeId,edgeFixture.B);
  assert.equal(copied.project.nodes[copied.branch.checkedOutNodeId].finalTick,100);
  assert.deepEqual(copied.project.nodes[edgeFixture.B],source.nodes[edgeFixture.B]);
  assert.deepEqual(copied.project.nodes[edgeFixture.C],source.nodes[edgeFixture.C]);
  assert.deepEqual(copied.project.nodes[copied.branch.checkedOutNodeId].actionsFromParent,source.nodes[edgeFixture.B].actionsFromParent);
  await page.evaluate(()=>window.testPlayback.undo());
  assert.equal(await page.evaluate(()=>window.testPlayback.route.edgeId),edgeFixture.B,'Undoing a copy must restore the paused replay, not silently enter recording');
  assert.equal(await page.evaluate(()=>window.mock.journal),null);
  assert.equal(await page.locator('.draft-note').count(),0);
  await page.evaluate(()=>window.testPlayback.copyNode());
  const beforeCopyResume=resumeRequests;
  await page.evaluate(()=>window.testPlayback.togglePlay());
  assert.equal(resumeRequests,beforeCopyResume+1);
  report.checks.push('Seeking inside an edge resumes its remaining interval; copying its paused frame starts an independent editable branch');
  const nodeStartResumes=resumeRequests,nodeStartEdges=playbackRequests.length;
  await page.evaluate(id=>window.testPlayback.checkout(id),edgeFixture.C);
  await page.waitForFunction(()=>window.testPlayback.status.playing&&!window.testPlayback.busy&&window.testPlayback.route===null);
  assert.equal(replayRequests.at(-1).expectedDigest,'C');assert.equal(state.tick,120);
  assert.equal(resumeRequests,nodeStartResumes+1);assert.equal(playbackRequests.length,nodeStartEdges,'Selecting C must simulate its state instead of replaying CF');
  await page.waitForFunction(()=>document.querySelector('.native-hand-group.current button:not(:disabled)'));
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.evaluate(id=>window.testPlayback.checkout(id),source.rootNodeId);
  await page.waitForFunction(()=>window.testPlayback.status.playing&&!window.testPlayback.busy&&window.testPlayback.route===null);
  assert.equal(state.tick,90);assert.equal(state.digest,'root');
  assert.equal(resumeRequests,nodeStartResumes+2);assert.equal(playbackRequests.length,nodeStartEdges,'Selecting fork A must simulate from A without choosing AB or AC');
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.waitForFunction(()=>!document.querySelector('.playback-controls button:last-of-type').disabled);
  await page.evaluate(()=>window.testPlayback.stepForward());
  assert.equal(stepRequests,1);assert.equal(state.tick,91);
  await page.waitForFunction(()=>!document.querySelector('.playback-controls button:last-of-type').disabled);
  await page.evaluate(()=>window.testPlayback.selectHand(1,0));
  await page.waitForFunction(()=>window.testPlayback.selected?.slot===0);
  await page.evaluate(()=>window.testPlayback.playAt({x:350,y:600}));
  assert.equal(state.tick,92);assert.equal(await page.locator('.draft-note').count(),1);
  await page.evaluate(()=>window.testPlayback.checkpoint());
  const continued=await page.evaluate(()=>({project:window.testPlayback.project,branch:window.testPlayback.branch,route:window.testPlayback.route}));
  const newNode=continued.project.nodes[continued.branch.checkedOutNodeId];
  assert.equal(newNode.parentId,source.rootNodeId);assert.equal(newNode.finalTick,92);assert.equal(newNode.actionsFromParent.length,1);
  for(const key of ['B','C','D','E','F'])assert.deepEqual(continued.project.nodes[edgeFixture[key]],source.nodes[edgeFixture[key]],`Continuing from A changed existing ${key}`);
  assert(continued.project.nodes[source.rootNodeId].childIds.includes(edgeFixture.B));
  assert(continued.project.nodes[source.rootNodeId].childIds.includes(edgeFixture.C));
  report.checks.push('Selecting C or fork A restores the exact saved state and immediately simulates; step/card/save creates a new sibling without changing existing routes');
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.F);
  state={...state,tick:130,digest:'F'};playing=false;playbackCompleted=true;
  await page.waitForFunction(id=>window.testPlayback.branch.checkedOutNodeId===id&&window.testPlayback.route===null&&window.testPlayback.status.playing&&!window.testPlayback.busy,edgeFixture.F);
  state={...state,tick:135,digest:'after-F'};
  await page.waitForFunction(()=>window.testPlayback.branch.workingTick===135);
  await page.evaluate(()=>window.testPlayback.togglePlay());
  await page.waitForFunction(()=>!document.querySelector('.playback-controls button:last-of-type').disabled);
  const beforeLeafStep=stepRequests;
  await page.evaluate(()=>window.testPlayback.stepForward());
  assert.equal(stepRequests,beforeLeafStep+1);assert.equal(state.tick,136);
  await page.waitForFunction(()=>!document.querySelector('.playback-controls button:last-of-type').disabled);
  await page.evaluate(()=>window.testPlayback.selectHand(1,0));
  await page.waitForFunction(()=>window.testPlayback.selected?.slot===0);
  await page.evaluate(()=>window.testPlayback.playAt({x:350,y:600}));
  await page.evaluate(()=>window.testPlayback.checkpoint());
  const afterLeaf=await page.evaluate(()=>({project:window.testPlayback.project,branch:window.testPlayback.branch}));
  const leafChild=afterLeaf.project.nodes[afterLeaf.branch.checkedOutNodeId];
  assert.equal(leafChild.parentId,edgeFixture.F);assert.equal(leafChild.finalTick,137);assert.equal(leafChild.actionsFromParent.length,1);
  assert.equal(afterLeaf.project.nodes[edgeFixture.F].finalTick,130);assert.equal(afterLeaf.project.nodes[edgeFixture.F].stateDigest,'F');
  report.checks.push('After CF reaches leaf F, simulation advances beyond F and supports pause, step, card play and saving a child while preserving F');
  await page.evaluate(id=>window.testPlayback.playEdge(id),edgeFixture.D);
  const beforeFinalizedResumes=resumeRequests;
  state={...state,tick:140,digest:'D',finalized:true};playing=false;playbackCompleted=true;
  await page.waitForFunction(id=>window.testPlayback.branch.checkedOutNodeId===id&&!window.testPlayback.route?.edgeId&&!window.testPlayback.status.playing,edgeFixture.D);
  assert.equal(resumeRequests,beforeFinalizedResumes);
  await page.evaluate(()=>window.testPlayback.togglePlay());
  assert.equal(resumeRequests,beforeFinalizedResumes);
  report.checks.push('A genuinely finalized endpoint stops rather than resuming a finished battle');
 } else if(process.argv.includes('--archive-delete')) {
  assert.equal(await page.getByRole('button',{name:'删除',exact:true}).count(),1);
  await page.evaluate(()=>{localStorage.setItem('clash-native.last-session',JSON.stringify({path:window.mock.path,nodeId:window.mock.source.rootNodeId}));void window.testArchiveActions.removeArchive(window.testArchiveActions.entries[0]);});
  await page.locator('.confirmation-dialog').waitFor();
  assert((await page.locator('.confirmation-dialog').textContent()).includes('test.cr-native-replay.json'));
  assert.deepEqual(await page.evaluate(()=>window.mock.deletions),[]);
  await page.evaluate(()=>window.testArchiveActions.finishConfirmation(false));
  await page.locator('.confirmation-dialog').waitFor({state:'detached'});
  assert.equal(await page.locator('.archive-entry').count(),1);
  assert.deepEqual(await page.evaluate(()=>window.mock.deletions),[]);
  report.checks.push('delete requires explicit confirmation; cancel leaves the archive untouched');
  await page.evaluate(()=>{window.mock.failDelete=true;void window.testArchiveActions.removeArchive(window.testArchiveActions.entries[0]);});
  await page.locator('.confirmation-dialog').waitFor();
  await page.evaluate(()=>window.testArchiveActions.finishConfirmation(true));
  await page.getByRole('alert').filter({hasText:'Simulated file deletion failure'}).waitFor();
  assert.equal(await page.locator('.archive-entry').count(),1);
  assert(await page.evaluate(()=>Boolean(localStorage.getItem('clash-native.last-session'))));
  report.checks.push('file deletion failure remains visible and retains the archive and recent session');
  // Load a retained project without starting the engine, then return home.
  await page.evaluate(()=>window.testArchiveActions.openProject(window.testArchiveActions.entries[0].path));
  await page.evaluate(()=>window.testArchiveActions.returnHome());
  await page.locator('.native-home').waitFor();
  await page.evaluate(()=>{window.mock.failDelete=false;void window.testArchiveActions.removeArchive(window.testArchiveActions.entries[0]);});
  await page.locator('.confirmation-dialog').waitFor();
  await page.evaluate(()=>window.testArchiveActions.finishConfirmation(true));
  await page.waitForFunction(()=>document.querySelectorAll('.archive-entry').length===0 && window.testArchiveActions.loadedProject===null);
  assert.deepEqual(await page.evaluate(()=>window.mock.deletions),[{path,directory:'C:/test/saves',projectId:source.projectId}]);
  assert.equal(await page.evaluate(()=>localStorage.getItem('clash-native.last-session')),null);
  assert(await page.getByRole('button',{name:'继续上次复盘',exact:true}).isDisabled());
  const writes=await page.evaluate(()=>window.mock.writes.length);
  await page.evaluate(()=>window.testArchiveActions.returnHome());
  assert.equal(await page.evaluate(()=>window.mock.writes.length),writes);
  report.checks.push('confirmed deletion refreshes the list, clears the loaded/recent archive and cannot recreate it');
 } else {
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
 const replaysBeforeReturn=replayRequests.length;
 await page.getByRole('button',{name:'继续上次复盘'}).click();
 await page.locator('.native-surface').waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(freshFrames,framesBeforeReturn);
 assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),'cache');
 assert.equal(replayRequests.length,replaysBeforeReturn+1);
 assert(replayRequests.at(-1).snapshotHandle>0);
 report.checks.push('returning from home restores a live snapshot without resetting the battle or taking another screenshot');
 await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();
 failNextSnapshot=true;
 const beforeExpiredSnapshot=replayRequests.length;
 await page.getByRole('button',{name:'继续上次复盘'}).click();
 await page.locator('.native-surface').waitFor();
 await page.locator('.native-notice').filter({hasText:'项目已载入'}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(replayRequests.length,beforeExpiredSnapshot+2);
 assert(replayRequests[beforeExpiredSnapshot].snapshotHandle>0);
 assert.equal(replayRequests.at(-1).snapshotHandle,undefined);
 assert.equal(await page.locator('.native-recovery').count(),0);
 report.checks.push('expired retained snapshots fall back to a checked seed replay without blocking the workspace');
 await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();
 resourcesReady=false;runtimeReady=false;await page.reload();await page.locator('.archive-entry').waitFor();
 await page.evaluate(()=>{window.mock.offline=false;window.mock.selected='C:/test/resources.zip';});
 await page.locator('.archive-entry').getByRole('button',{name:'打开',exact:true}).click();
 replayDelay=600;
 await page.locator('.first-run-panel').getByRole('button',{name:'导入战斗资源包'}).click();
 await page.locator('.first-run-panel').getByRole('heading',{name:'正在恢复战斗局面',exact:true}).waitFor();
 assert.equal(await page.locator('.connection-steps li').nth(2).getAttribute('data-complete'),'false');
 assert.equal(await page.locator('.connection-steps li').nth(2).getAttribute('aria-current'),'step');
 replayDelay=0;
 report.checks.push('connection stays in progress while the engine is ready but the saved battle is still restoring');
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
 await page.evaluate(()=>{
  window.bitmapDecodes=0;const decode=window.createImageBitmap;
  window.createImageBitmap=function(...args){window.bitmapDecodes++;return decode.apply(this,args);};
 });
 await page.getByRole('button',{name:'保存当前节点',exact:true}).click();
 await page.locator('.native-notice').filter({hasText:'已保存为子节点'}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(freshFrames,beforeSave);
 assert.equal(await page.evaluate(()=>window.bitmapDecodes),0);
 assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),'cache');
 report.checks.push('changed working tick fetches a fresh picture; saving that same state reuses canvas pixels without another decode');
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
 assert.equal(await page.locator('.selected-deck-card').count(),0);
 assert(await page.getByRole('button',{name:'创建存档并开始',exact:true}).isDisabled());
 for(const side of ['敌方','我方']){
  await page.getByRole('button',{name:`${side} · 0/8`,exact:true}).click();
  assert.equal(await page.locator('.selected-deck-card').count(),0);
  for(const id of STANDARD_NATIVE_DECK)await page.locator(`.card-catalog button[data-card-id="${id}"]`).click();
 }
 report.checks.push('new archives start with both decks empty and require explicit card selection before creation');
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
 const deckIds=()=>page.locator('.selected-deck-card').evaluateAll(cards=>cards.map(card=>Number(card.dataset.cardId)));
 const dragOnto=async(from,to,cancel=false)=>{
  const origin=await selected(from).locator('.initial-card').boundingBox(),target=await selected(to).boundingBox();
  await page.mouse.move(origin.x+origin.width/2,origin.y+origin.height/2);await page.mouse.down();
  await page.mouse.move(target.x+target.width/2,target.y+target.height/3,{steps:8});
  if(cancel)await page.keyboard.press('Escape');
  await page.mouse.up();
  assert.equal(await page.locator('[data-dragging="true"], [data-drop-target="true"]').count(),0);
 };
 const beforeDrag=await deckIds();
 await dragOnto(26000000,26000014);
 const afterDrag=[...beforeDrag];[afterDrag[0],afterDrag[6]]=[afterDrag[6],afterDrag[0]];
 assert.deepEqual(await deckIds(),afterDrag,'Dragging swaps the two cards without shifting intervening cards');
 assert.equal(await selected(26000000).locator('select').inputValue(),'1');
 assert.equal(await selected(26000001).locator('select').inputValue(),'1');
 assert.equal(await selected(26000014).locator('select').inputValue(),'2');
 await dragOnto(26000000,26000001,true);assert.deepEqual(await deckIds(),afterDrag);
 await selected(26000000).locator('.initial-card').press('ArrowLeft');
 assert.equal((await deckIds())[5],26000000);
 await page.getByRole('button',{name:'敌方 · 8/8',exact:true}).click();
 assert.equal(await page.locator('.deck-special-slot[data-occupied="true"]').count(),0);
 await page.getByRole('button',{name:'我方 · 8/8',exact:true}).click();
 assert.equal(await page.locator('.deck-special-slot[data-occupied="true"]').count(),3);
 await mkdir('.local-runtime/native-ux-check',{recursive:true});
 await page.screenshot({path:'.local-runtime/native-ux-check/deck-slots.png'});
 const clearDeck=async()=>{while(await page.locator('.selected-deck-card').count())await page.locator('.initial-order-tools button').last().click();};
 const addCard=async id=>page.locator(`.card-catalog button[data-card-id="${id}"]`).click();
 await clearDeck();
 const fixedCards=NATIVE_CARD_CATALOG.filter(card=>card.omitFromStartingHand);
 for(const id of [...STANDARD_NATIVE_DECK.slice(0,6),...fixedCards.map(card=>card.id)])await addCard(id);
 const fixedOrder=await deckIds();
 await dragOnto(fixedOrder[0],fixedOrder[7]);assert.deepEqual(await deckIds(),fixedOrder);
 await dragOnto(fixedOrder[5],fixedOrder[7]);
 const swappedFixed=[...fixedOrder];[swappedFixed[5],swappedFixed[7]]=[swappedFixed[7],swappedFixed[5]];
 assert.deepEqual(await deckIds(),swappedFixed);
 await resize(800,700);
 const wrappedOrder=await deckIds();await dragOnto(wrappedOrder[0],wrappedOrder[6]);
 const wrappedSwap=[...wrappedOrder];[wrappedSwap[0],wrappedSwap[6]]=[wrappedSwap[6],wrappedSwap[0]];
 assert.deepEqual(await deckIds(),wrappedSwap);
 await resize(1440,960);
 report.checks.push('pointer drag swaps cards and forms, Escape cancels, keyboard reorders, fixed deal slots and wrapped/scaled layouts remain valid');
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
 await page.getByRole('button',{name:'返回首页'}).click();await page.locator('.native-home').waitFor();
 image=Buffer.from(await page.evaluate(async()=>{
  const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=1920;
  const context=canvas.getContext('2d'),pixels=context.createImageData(canvas.width,canvas.height);
  let value=41;
  for(let offset=0;offset<pixels.data.length;offset+=4) {
   value^=value<<13;value^=value>>>17;value^=value<<5;
   pixels.data[offset]=value&255;pixels.data[offset+1]=(value>>>8)&255;
   pixels.data[offset+2]=(value>>>16)&255;pixels.data[offset+3]=255;
  }
  context.putImageData(pixels,0,0);
  return canvas.toDataURL('image/png').split(',')[1];
 }),'base64');
 const routeNodes=await page.evaluate(path=>{
  const project=window.mock.source;project.projectId='ancestor-cache-test';
  let parent=project.nodes[project.rootNodeId];
  const nodes=[];
  for(let index=1;index<=40;index++) {
   const node={...parent,id:`route-${index}`,parentId:parent.id,childIds:[],finalTick:90+index*10,
    title:`路线 ${index}`,note:'',actionsFromParent:[],stateDigest:`route-digest-${index}`};
   parent.childIds=[node.id];project.nodes[node.id]=node;project.layout[node.id]={x:0,y:index*100};
   nodes.push(node);parent=node;
  }
  const root=project.nodes[project.rootNodeId];
  root.childIds.push('sibling');
  project.nodes.sibling={...root,id:'sibling',parentId:root.id,childIds:[],finalTick:100,stateDigest:'sibling-digest'};
  project.layout.sibling={x:220,y:100};
  localStorage.setItem('clash-native.last-session',JSON.stringify({path,nodeId:parent.id}));
  return nodes;
 },path);
 const beforeAncestorReplay=replayRequests.length;
 await page.getByRole('button',{name:'继续上次复盘'}).click();
 await page.locator('.native-notice').filter({hasText:'项目已载入'}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(replayRequests.length,beforeAncestorReplay+2);
 const warmed=replayRequests.at(-1).checkpoints;
 assert.equal(warmed.length,31);assert.equal(warmed.at(-1).nodeId,'route-40');
 assert(!warmed.some(checkpoint=>checkpoint.nodeId==='sibling'));
 const ancestor=routeNodes[29];
 await page.locator(`[data-node-id="${ancestor.id}"] button`).evaluate(button=>button.click());
 await page.waitForFunction(id=>document.querySelector('.tree-node.active')?.dataset.nodeId===id,ancestor.id);
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(replayRequests.at(-1).snapshotHandle>0,true);
 assert.equal(replayRequests.at(-1).baseTick,ancestor.finalTick);
 assert.deepEqual(replayRequests.at(-1).actions,[]);assert.deepEqual(replayRequests.at(-1).checkpoints,[]);
 assert(releasedSnapshots.length>0);
 report.checks.push('opening a 40-node route warms bounded verified ancestors; first ancestor visit restores a snapshot without replaying siblings');
 await page.waitForFunction(()=>[...window.testFrameCache].some(([key,value])=>JSON.parse(key)[0]==='ancestor-cache-test'&&JSON.parse(key)[2]===390&&value.cropped));
 const pixels=await page.evaluate(async()=>{
  const canvas=document.querySelector('[aria-label="原生竞技场"]');
  const [key,entry]=[...window.testFrameCache].find(([key])=>JSON.parse(key)[0]==='ancestor-cache-test'&&JSON.parse(key)[2]===390);
  const bitmap=await createImageBitmap(entry.image),copy=new OffscreenCanvas(bitmap.width,bitmap.height);
  const context=copy.getContext('2d',{alpha:false});context.drawImage(bitmap,0,0);bitmap.close();
  const hash=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(value=>value.toString(16).padStart(2,'0')).join('');
  return {width:copy.width,height:copy.height,canvasWidth:canvas.width,canvasHeight:canvas.height,
   cachedBytes:entry.image.size,displayHash:await hash(canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data),
   cacheHash:await hash(context.getImageData(0,0,copy.width,copy.height).data)};
 });
 assert.equal(pixels.width,pixels.canvasWidth);assert.equal(pixels.height,pixels.canvasHeight);
 assert.equal(pixels.displayHash,pixels.cacheHash);assert(pixels.cachedBytes<image.length/2);
 const visit=async node=>{
  await page.locator(`[data-node-id="${node.id}"] button`).evaluate(button=>button.click());
  await page.waitForFunction(id=>document.querySelector('.tree-node.active')?.dataset.nodeId===id,node.id);
  await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
  await page.waitForFunction(tick=>[...window.testFrameCache].some(([key,entry])=>JSON.parse(key)[0]==='ancestor-cache-test'&&JSON.parse(key)[2]===tick&&entry.cropped),node.finalTick);
 };
 for(const node of routeNodes.slice(10,18))await visit(node);
 const cache=await page.evaluate(()=>({entries:window.testFrameCache.size,bytes:[...window.testFrameCache.values()].reduce((total,value)=>total+value.image.size,0),
  routeEntries:[...window.testFrameCache.keys()].filter(key=>JSON.parse(key)[0]==='ancestor-cache-test').length}));
 assert(cache.entries<=32);assert(cache.bytes<=32*1024*1024);assert(cache.routeEntries>=10);
 const framesBeforeCachedReturn=freshFrames;
 await visit(routeNodes[10]);assert.equal(freshFrames,framesBeforeCachedReturn);
 assert.equal(await page.getByLabel('原生竞技场').getAttribute('data-frame-source'),'cache');
 report.frameCache={...cache,fullFrameBytes:image.length,croppedFrameBytes:pixels.cachedBytes};
 report.checks.push('cropped frame cache preserves every displayed pixel and retains ten large frames within the original memory budget');
 runtimeReady=false;playing=false;
 await page.reload();await page.locator('.native-home').waitFor();
 assert.equal(await page.evaluate(()=>window.mock.launches),0);
 await page.evaluate(()=>{window.mock.offline=false;});
 const startsBeforePreparation=startRequests,resetsBeforePreparation=replayRequests.length;
 const prepared=page.waitForResponse(async response=>response.url().endsWith('/v1/status')&&(await response.json()).ready);
 await page.getByRole('button',{name:'新建存档',exact:true}).click();
 await prepared;
 assert.equal(startRequests,startsBeforePreparation+1);assert.equal(await page.evaluate(()=>window.mock.launches),1);
 assert.equal(replayRequests.length,resetsBeforePreparation);assert.deepEqual(await page.evaluate(()=>window.mock.writes),[]);
 assert.equal(await page.getByRole('dialog').getByRole('textbox',{name:'新存档名称'}).isEnabled(),true);
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'新建存档',exact:true}).click();
 assert.equal(startRequests,startsBeforePreparation+1);
 for(const side of ['敌方','我方']){
  await page.getByRole('button',{name:`${side} · 0/8`,exact:true}).click();
  assert.equal(await page.locator('.selected-deck-card').count(),0);
  for(const id of STANDARD_NATIVE_DECK)await addCard(id);
 }
 await page.getByRole('dialog').getByRole('button',{name:'创建存档并开始',exact:true}).click();
 await page.locator('.native-surface').waitFor();
 await page.waitForFunction(()=>!document.querySelector('.playback-controls button:nth-child(3)').disabled);
 assert.equal(startRequests,startsBeforePreparation+1);assert.equal(await page.locator('.first-run-panel').count(),0);
 report.checks.push('opening new-match setup prepares one runtime while cards stay editable; cancel creates no save and confirmation reuses the connection');
 report.responsiveLayouts=[];
 for(const [name,width,height] of [['reference',1828,978],['window',1029,686],['same-aspect',1280,685],['compact',760,540],['restored',1828,978]]) {
  await resize(width,height);
  const layout=await page.evaluate(()=>{
   const box=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
   return {viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,zoom:Number(getComputedStyle(document.querySelector('.native-app')).zoom),
    app:box('.native-app'),tree:box('.native-sidebar'),main:box('.native-main'),inspector:box('.native-inspector'),
    arena:box('.native-crop canvas'),card:box('.native-hand-group.current button'),control:box('.play-button'),
    overflow:document.documentElement.scrollWidth-innerWidth};
  });
  report.responsiveLayouts.push({name,...layout});
  assert(Math.abs(layout.app.width-width)<1&&Math.abs(layout.app.height-height)<1,`${name}: layout does not fill the viewport`);
  assert(layout.inspector.width>0,`${name}: inspector disappeared`);
  assert(Math.abs(layout.dpr-report.host.dpr)<.001,`${name}: system scale changed during the layout check`);
  assert(layout.overflow<=1,`${name}: horizontal overflow`);
  assert(Math.abs(layout.arena.width/layout.arena.height-648/923)<.003,`${name}: arena stretched`);
  assert(layout.arena.y+layout.arena.height<layout.control.y,`${name}: arena overlaps playback controls`);
  const reference=report.responsiveLayouts[0];
  assert(Math.abs(layout.arena.height/layout.zoom/reference.arena.height-1)<.09,`${name}: only the arena shrank`);
  assert(Math.abs(layout.card.height-reference.card.height*layout.zoom)<1.5,`${name}: cards do not follow the UI scale`);
  assert(Math.abs(layout.control.height-reference.control.height*layout.zoom)<1.5,`${name}: controls do not follow the UI scale`);
  await page.screenshot({path:`.local-runtime/native-ux-check/layout-${harness?'desktop-':''}${name}.png`});
 }
 assert.deepEqual(report.responsiveLayouts[0].arena,report.responsiveLayouts.at(-1).arena);
 report.checks.push('reference, window, same-aspect and compact layouts scale cards, controls and arena together; restoring the window retains its dimensions');
 await resize(1029,686);
 await page.getByRole('button',{name:'适应视图',exact:true}).click();
 const node=page.locator('.tree-node').first();
 const beforeMove=await node.boundingBox();
 await page.mouse.move(beforeMove.x+beforeMove.width/2,beforeMove.y+beforeMove.height/2);
 await page.mouse.down();await page.mouse.move(beforeMove.x+beforeMove.width/2+60,beforeMove.y+beforeMove.height/2+35,{steps:8});await page.mouse.up();
 let afterMove=await node.boundingBox();
 assert(Math.abs(afterMove.x-beforeMove.x-60)<1.5&&Math.abs(afterMove.y-beforeMove.y-35)<1.5,'Scaled tree drag did not follow the pointer');
 const center={x:afterMove.x+afterMove.width/2,y:afterMove.y+afterMove.height/2};
 await page.mouse.move(center.x,center.y);await page.mouse.wheel(0,-120);
 await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
 afterMove=await node.boundingBox();
 assert(Math.abs(afterMove.x+afterMove.width/2-center.x)<1.5&&Math.abs(afterMove.y+afterMove.height/2-center.y)<1.5,'Tree wheel zoom lost its pointer anchor');
 report.checks.push('scaled node dragging follows the pointer and wheel zoom keeps its anchor');
 await page.getByRole('region',{name:'我方八张牌序'}).locator('button:not(:disabled)').first().click();
 const placement=page.waitForRequest(request=>request.url().endsWith('/v1/play-card'));
 const arena=page.getByLabel('原生竞技场'),bounds=await arena.boundingBox();
 await arena.click({position:{x:bounds.width*.525,y:bounds.height*.7}});
 const placed=(await placement).postDataJSON();assert.equal(placed.x,9500);assert.equal(placed.y,21500);
 report.checks.push('scaled arena clicks still select the exact native world cell');
 await page.getByRole('button',{name:'初始配置（只读）',exact:true}).click();
 const modal=await page.getByRole('dialog').boundingBox();
 assert(modal.x>=0&&modal.y>=0&&modal.x+modal.width<=1029&&modal.y+modal.height<=686,'Scaled dialog exceeds the viewport');
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
 report.checks.push('dialogs remain within the scaled window');
 }
 assert.deepEqual(report.errors,[]);report.status='passed';
} catch(error){report.status='failed';report.error=String(error);throw error;}
finally {if(harness)await harness.close();else await browser.close();await mkdir('.local-runtime/native-ux-check',{recursive:true});await writeFile(`.local-runtime/native-ux-check/${process.argv.includes('--edge-playback')?'edge-playback-':process.argv.includes('--archive-delete')?'archive-delete-':harness?'desktop-':''}frontend-report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));}
