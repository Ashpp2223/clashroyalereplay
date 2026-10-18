// App-owned WebView2 automation. It never sends desktop mouse/keyboard input.
import {chromium} from 'playwright-core';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile,copyFile,access} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {createServer} from 'node:net';
import {pathToFileURL} from 'node:url';

async function freePort() {
  const server=createServer();await new Promise(done=>server.listen(0,'127.0.0.1',done));
  const port=server.address().port;await new Promise(done=>server.close(done));return port;
}

export async function openDesktopHarness({app,home,runtime,devUrl,engineProfile='legacy'}) {
  if(!['legacy','cn-38.4.5'].includes(engineProfile))throw new Error('Unknown test engine profile');
  if(devUrl&&!/^http:\/\/127\.0\.0\.1:1420\/?$/.test(devUrl))throw new Error('Development preview must use the local project server');
  app=resolve(app);home=resolve(home);runtime=resolve(runtime);
  await access(app);
  if(!devUrl)await access(join(dirname(app),'native-bridge','native-bridge.exe'));
  await mkdir(join(home,'runtime'),{recursive:true});
  await mkdir(join(home,'screenshots'),{recursive:true});
  const settings=JSON.parse(await readFile(join(runtime,'runtime.json'),'utf8'));
  const cn=engineProfile==='cn-38.4.5';
  if(cn ? settings.serial!=='emulator-5572'||settings.avdName!=='CR_Camp_Research_API31' : settings.serial!=='emulator-5560')throw new Error('Desktop acceptance device does not match its engine profile');
  await writeFile(join(home,'runtime','runtime.json'),JSON.stringify(settings));
  if(!cn)await copyFile(join(runtime,'prepared.json'),join(home,'runtime','prepared.json'));
  const port=await freePort();
  const process=spawn(app,[],{windowsHide:true,stdio:'ignore',env:{...globalThis.process.env,
    CR_DESKTOP_TEST_HOME:home,CR_ENGINE_PORT:cn?'26791':'26790',CR_ENGINE_PROFILE:engineProfile,
    ...(cn?{CR_CAMP_RESEARCH_ROOT:dirname(runtime)}:{}),WEBVIEW2_USER_DATA_FOLDER:join(home,'webview'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-address=127.0.0.1 --remote-debugging-port=${port} --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows`}});
  let browser,page;
  try {
    const deadline=Date.now()+30000;
    while(Date.now()<deadline) {
      if(process.exitCode!==null)throw new Error(`Test application exited: ${process.exitCode}`);
      try {await fetch(`http://127.0.0.1:${port}/json/version`).then(response=>{if(!response.ok)throw new Error('not ready');return response.json();});break;}catch {}
      await new Promise(done=>setTimeout(done,250));
    }
    browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const pageDeadline=Date.now()+15000;
    while(Date.now()<pageDeadline) {
      page=browser.contexts().flatMap(context=>context.pages()).find(page=>/^(?:https?:\/\/tauri\.localhost|tauri:\/\/localhost)/.test(page.url())||(devUrl&&page.url().startsWith(devUrl)));
      if(page)break;
      await new Promise(done=>setTimeout(done,100));
    }
    if(!page)throw new Error('The owned Tauri page was not found: '+JSON.stringify(browser.contexts().flatMap(context=>context.pages()).map(page=>page.url())));
    const owned=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('native_test_workspace'));
    if(resolve(owned??'')!==home)throw new Error('Refusing to automate a non-test application');
  } catch(error) {process.kill();if(browser)await browser.close();throw error;}
  return {
    page,home,
    async screenshot(name) {
      if(!/^[a-z0-9-]+$/i.test(name))throw new Error('Invalid screenshot name');
      const path=join(home,'screenshots',name+'.png');
      await page.evaluate(async()=>{await Promise.all([...document.images].filter(image=>!image.complete).map(image=>new Promise(done=>{image.addEventListener('load',done,{once:true});image.addEventListener('error',done,{once:true});setTimeout(done,3000);})));});
      await page.screenshot({path});return path;
    },
    async state() {
      return page.evaluate(()=>({title:document.title,notice:document.querySelector('.native-notice')?.textContent,
        nodes:[...document.querySelectorAll('.tree-node')].map(node=>({text:node.textContent,selected:node.classList.contains('active')})),
        dialogs:[...document.querySelectorAll('[role=dialog]')].map(dialog=>dialog.textContent),
        hands:[...document.querySelectorAll('.native-hand')].map(hand=>hand.textContent)}));
    },
    async close() {
      try {await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('finish_desktop_test'));}catch {}
      const deadline=Date.now()+70000;
      while(process.exitCode===null && Date.now()<deadline)await new Promise(done=>setTimeout(done,100));
      if(process.exitCode===null)process.kill();
      await browser.close();
    }
  };
}

// Exercise the real TS action/journal code and packaged Rust commands through
// the owned test connection. No mouse, keyboard, DOM clicks or user session.
export async function verifyDeploymentPipeline(projectPath) {
 const assert=(await import('node:assert/strict')).default;
 const domain=await import('../src/domain/nativeReplay.ts');
 const {NativeBridge}=await import('../src/native/bridge.ts');
 const journal=await import('../src/native/journal.ts');
 const project=domain.validateNativeProject(JSON.parse(await readFile(projectPath,'utf8')));
 assert.equal(project.engine.protocolVersion,'cr-camp-control.v1','This pipeline check uses the CN test device');
 const home=resolve('.local-runtime/frontend-deployment-check');
 const report={status:'running',stage:'launch',cases:[]};
 const harness=await openDesktopHarness({app:'release/native-desktop/clash-replay-tree.exe',home,
  runtime:'../clashroyale1016/.runtime',engineProfile:'cn-38.4.5'});
 globalThis.window={__TAURI_INTERNALS__:{invoke:(command,args)=>harness.page.evaluate(
  ({command,args})=>window.__TAURI_INTERNALS__.invoke(command,args),{command,args})}};
 let bridge;
 try {
  bridge=await NativeBridge.launch();
  report.stage='start';await bridge.post('/v1/start');
  const deadline=Date.now()+180000;
  let status;
  do {
   status=await bridge.get('/v1/status');
   if(status.error)throw Error(status.error);
   if(status.ready)break;
   assert(Date.now()<deadline,'Runtime did not become ready');
   await new Promise(done=>setTimeout(done,250));
  }while(true);
  domain.configureNativeCatalog(status.engineCatalog,status.identity);
  domain.projectForCurrentEngine(project,status.identity);
  const root=project.nodes[project.rootNodeId];
  let branch=domain.checkoutNativeNode(project,project.rootNodeId);
  report.stage='replay';
  let state=await bridge.replay(project.match.seed,[],root.finalTick,root.stateDigest,undefined,undefined,undefined,project.match);
  await journal.clearNativeJournal(project.projectId);
  for(let index=0;index<3;index++) {
   const player=state.players[1];
   const card=player.hand.find(card=>card.cost*10000<=player.elixir);
   assert(card,'No affordable card in hand');
   const entry={index,card:card.card,slot:card.slot,stages:[]};report.cases.push(entry);
   report.stage='play-card';
   const receipt=await bridge.post('/v1/play-card',{owner:1,slot:card.slot,x:3500,y:25500,expectedTick:state.tick});
   assert(receipt.accepted,'Deployment rejected');entry.stages.push('native-confirmed');
   report.stage='append-action';
   branch=domain.advanceNativeWork(branch,receipt.observation.tick);
   branch=domain.appendConfirmedNativeAction(project,branch,{...receipt.action,id:crypto.randomUUID()});
   entry.stages.push('frontend-action-validated');
   report.stage='write-journal';await journal.writeNativeJournal(project,branch);
   const restored=await journal.readNativeJournal(project);
   assert.deepEqual(restored.actions,branch.actions);entry.stages.push('rust-journal-written-and-read');
   report.stage='frame-fetch';
   const image=await bridge.frame(true),base64=Buffer.from(await image.arrayBuffer()).toString('base64');
   report.stage='frame-decode';
   entry.render=await harness.page.evaluate(async({base64,type})=>{
    const image=await createImageBitmap(new Blob([Uint8Array.from(atob(base64),c=>c.charCodeAt(0))],{type}));
    const canvas=document.createElement('canvas');canvas.width=243;canvas.height=346;
    canvas.getContext('2d').drawImage(image,54,282,972,1384,0,0,243,346);
    const cropped=await new Promise(done=>canvas.toBlob(done,'image/png'));
    const result={width:image.width,height:image.height,croppedBytes:cropped?.size};image.close();return result;
   },{base64,type:image.type});
   assert(entry.render.croppedBytes>0);entry.stages.push('webview-image-decoded-drawn-and-packed');
   state=receipt.observation;
   console.log(JSON.stringify(entry));
  }
  report.stage='diagnostic-write';
  await bridge.recordFailure('isolated-pipeline-test',new Error('Diagnostic write check '+(process.env.USERPROFILE??'')));
  const diagnostic=JSON.parse(await readFile(join(home,'runtime/last-client-failure.json'),'utf8'));
  assert.equal(diagnostic.path,'isolated-pipeline-test');
  if(process.env.USERPROFILE)assert(!diagnostic.error.includes(process.env.USERPROFILE));
  report.diagnosticWrite='passed';
  report.status='passed';
 }catch(error){report.status='failed';report.error=String(error);process.exitCode=1;}
 finally {
  await writeFile(join(home,'pipeline-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({status:report.status,stage:report.stage,error:report.error}));
  await harness.close();delete globalThis.window;
 }
 return report;
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href&&process.argv[2]==='--deployment-check') {
 if(!process.argv[3])throw Error('Usage: node scripts/desktop_control.mjs --deployment-check <project.json>');
 await verifyDeploymentPipeline(process.argv[3]);
}
