// App-owned WebView2 automation. It never sends desktop mouse/keyboard input.
import {chromium} from 'playwright-core';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile,copyFile,access} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {createServer} from 'node:net';

async function freePort() {
  const server=createServer();await new Promise(done=>server.listen(0,'127.0.0.1',done));
  const port=server.address().port;await new Promise(done=>server.close(done));return port;
}

export async function openDesktopHarness({app,home,runtime,devUrl}) {
  if(devUrl&&!/^http:\/\/127\.0\.0\.1:1420\/?$/.test(devUrl))throw new Error('Development preview must use the local project server');
  app=resolve(app);home=resolve(home);runtime=resolve(runtime);
  await access(app);
  if(!devUrl)await access(join(dirname(app),'native-bridge','native-bridge.exe'));
  await mkdir(join(home,'runtime'),{recursive:true});
  await mkdir(join(home,'screenshots'),{recursive:true});
  const settings=JSON.parse(await readFile(join(runtime,'runtime.json'),'utf8'));
  if(settings.serial!=='emulator-5560')throw new Error('Desktop acceptance must use the dedicated emulator-5560 test device');
  await writeFile(join(home,'runtime','runtime.json'),JSON.stringify(settings));
  await copyFile(join(runtime,'prepared.json'),join(home,'runtime','prepared.json'));
  const port=await freePort();
  const process=spawn(app,[],{windowsHide:true,stdio:'ignore',env:{...globalThis.process.env,
    CR_DESKTOP_TEST_HOME:home,CR_ENGINE_PORT:'26790',WEBVIEW2_USER_DATA_FOLDER:join(home,'webview'),
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
      const deadline=Date.now()+20000;
      while(process.exitCode===null && Date.now()<deadline)await new Promise(done=>setTimeout(done,100));
      if(process.exitCode===null)process.kill();
      await browser.close();
    }
  };
}
