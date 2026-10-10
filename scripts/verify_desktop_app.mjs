import assert from 'node:assert/strict';
import {readFile,readdir,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {openDesktopHarness} from './desktop_control.mjs';

const app=resolve(process.argv[2]??'src-tauri/target/release/clash-replay-tree.exe');
const runtime=resolve(process.argv[3]??`${process.env.LOCALAPPDATA}/cr-replay-clean-test`);
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
  harness=await openDesktopHarness({app,home,runtime});
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
  assert.deepEqual(saved.data.match,initial);
  await screenshot('05-editable-child-saved');
  report.checks.push('copy-play-save-updates-one-child-without-changing-initial-configuration');
  await harness.close();harness=null;
  harness=await openDesktopHarness({app,home,runtime});page=harness.page;
  await page.locator('.native-notice').filter({hasText:'项目已载入'}).waitFor({timeout:180000});
  assert.equal(await page.locator('.tree-node').count(),2);
  assert((await page.locator('.tree-node.active').innerText()).includes('1 动作'));
  await screenshot('06-cold-reopen');
  report.checks.push('cold-reopen-restores-selected-child-and-locked-match');
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
