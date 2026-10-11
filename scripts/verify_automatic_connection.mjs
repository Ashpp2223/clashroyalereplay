import assert from 'node:assert/strict';
import {readFile,readdir,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {openDesktopHarness} from './desktop_control.mjs';

const app=resolve(process.argv[2]??'src-tauri/target/release/clash-replay-tree.exe');
const runtime=resolve('.local-runtime/desktop-check/runtime');
const home=resolve('.local-runtime/automatic-connection-check');
const title='自动连接验收-'+Date.now();
const report={status:'running',checks:[],timingsMs:{},screenshots:[]};
let harness;
const errors=[];
const attach=page=>{
 page.setDefaultTimeout(180000);
 page.on('pageerror',error=>errors.push(String(error)));
 let starts=0;
 page.on('request',request=>{if(new URL(request.url()).pathname==='/v1/start')starts++;});
 return ()=>starts;
};
const waitFrame=page=>page.waitForFunction(()=>{
 const surface=document.querySelector('.native-surface'),canvas=document.querySelector('[aria-label="原生竞技场"]');
 return surface&&canvas&&canvas.dataset.frameRevision===surface.dataset.controlRevision&&!document.querySelectorAll('.playback-controls button')[2].disabled;
});
try {
 await mkdir(home,{recursive:true});
 harness=await openDesktopHarness({app,home,runtime});
 let page=harness.page,starts=attach(page);
 await page.evaluate(()=>{localStorage.setItem('clash-native.language','zh-CN');localStorage.setItem('clash-native.help-seen.v1','1');localStorage.removeItem('clash-native.last-session');});
 await page.reload();await page.locator('.native-home').waitFor();assert.equal(starts(),0);
 const begin=Date.now();
 await page.getByRole('button',{name:'设置运行环境',exact:true}).click();
 await page.locator('.connection-steps li[data-complete="true"]').nth(1).waitFor();
 assert.equal(await page.locator('.native-sidebar').isVisible(),false);
 assert.equal(await page.locator('.native-inspector').isVisible(),false);
 assert.equal(await page.locator('.playback-controls').isVisible(),false);
 assert.equal(await page.getByRole('button',{name:'开始连接',exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'更新战斗资源包',exact:true}).isVisible(),false);
 assert.equal(await page.getByRole('button',{name:'导出诊断',exact:true}).isVisible(),false);
 report.screenshots.push(await harness.screenshot('automatic-progress'));
 await Promise.race([page.locator('.first-run-panel').getByRole('heading',{name:'就绪',exact:true}).waitFor(),page.locator('.setup-error').waitFor().then(async()=>{throw Error(await page.locator('.setup-error').innerText());})]);
 report.timingsMs.settingsToReady=Date.now()-begin;assert.equal(starts(),1);
 assert.equal(await page.locator('.connection-steps li[data-complete="true"]').count(),3);
 report.checks.push('packaged settings automatically starts once; maintenance options collapsed; all three stages become ready');
 await page.getByText('高级选项',{exact:true}).click();assert(await page.getByRole('button',{name:'更新战斗资源包',exact:true}).isVisible());
 await page.getByText('高级选项',{exact:true}).click();
 await page.locator('.first-run-panel').getByRole('button',{name:'新建存档',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await dialog.getByRole('textbox',{name:'新存档名称'}).fill(title);
 await dialog.getByRole('button',{name:'创建存档并开始',exact:true}).click();
 await page.locator('.native-surface').waitFor();await waitFrame(page);
 await page.getByRole('button',{name:'前进一步 │▶',exact:true}).click();await waitFrame(page);
 await page.getByRole('button',{name:'保存当前节点',exact:true}).click();
 await page.locator('.native-notice').filter({hasText:'已保存为子节点'}).waitFor();await waitFrame(page);
 const saved=await Promise.all((await readdir(join(home,'saves'))).filter(name=>name.endsWith('.cr-native-replay.json')).map(async name=>({path:join(home,'saves',name),data:JSON.parse(await readFile(join(home,'saves',name),'utf8'))})));
 const source=saved.find(entry=>entry.data.title===title);assert(source);
 const child=Object.values(source.data.nodes).find(node=>node.parentId);assert(child);assert.equal(child.finalTick,91);
 report.checks.push('real native battle creates, steps and saves a checkpoint');
 report.screenshots.push(await harness.screenshot('automatic-battle'));
 await harness.close();harness=null;
 harness=await openDesktopHarness({app,home,runtime});page=harness.page;starts=attach(page);
 await page.locator('.native-home').waitFor();assert.equal(starts(),0);
 const resume=Date.now();
 await page.getByRole('button',{name:'继续上次复盘',exact:true}).click();
 await page.locator('.native-surface').waitFor();await waitFrame(page);
 report.timingsMs.continueToBattle=Date.now()-resume;assert.equal(starts(),1);
 const observed=await page.evaluate(async()=>{
  const info=await window.__TAURI_INTERNALS__.invoke('start_native_bridge');
  const response=await fetch(`http://127.0.0.1:${info.port}/v1/observe`,{headers:{Authorization:`Bearer ${info.token}`}});
  return response.json();
 });
 assert.equal(observed.tick,child.finalTick);assert.equal(observed.digest,child.stateDigest);
 assert.deepEqual(JSON.parse(await readFile(source.path,'utf8')),source.data);
 report.checks.push('close/reopen uses Android Quick Boot and automatically restores the selected saved node with identical state digest');
 report.screenshots.push(await harness.screenshot('automatic-restored'));
 assert.deepEqual(errors,[]);report.status='passed';
} catch(error){report.status='failed';report.error=String(error);throw error;}
finally {if(harness)await harness.close();await writeFile(join(home,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));}
