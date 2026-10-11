import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createNativeProject,checkoutNativeNode,advanceNativeWork,appendConfirmedNativeAction,copyNativeNode,createNativeCheckpoint,deleteNativeSubtree,STANDARD_NATIVE_DECK,updateNativeNodeMetadata} from '../src/domain/nativeReplay.ts';
import {EditHistory,structureEntry,undoStructure} from '../src/native/history.ts';
import {restoreUndoBranch,withoutLastDraftAction,UndoRecoveryError} from '../src/native/undo.ts';
import {isNativeProjectPath,listReplayArchives,readNativeProject,reopenNativeSession} from '../src/native/files.ts';
import {zh,en,translate,storedLanguage,LANGUAGE_KEY} from '../src/native/i18n.ts';
const identity={upstreamCommit:'commit',probeSha256:'probe',apkSha256:'apk',libgSha256:'libg',runtimeFingerprintSha256:'runtime',protocolVersion:'v1',cropProfileId:'stock'};
const source=createNativeProject(identity,{seed:41,preset:'standard',deck0:[...STANDARD_NATIVE_DECK],deck1:[...STANDARD_NATIVE_DECK]},90,'root','User title');
const base=checkoutNativeNode(source,source.rootNodeId);
const action={id:'play',tick:91,sequence:0,owner:1,kind:'play-card',nativeCardId:26000000,slot:0,nativeX:3500,nativeY:13000};
const work=appendConfirmedNativeAction(source,advanceNativeWork(base,100),action);
const paused={tick:100,digest:'original'};
const reduced=withoutLastDraftAction(source,work,'play');

test('history bounds and continuous text grouping preserve the earliest reversible value',()=>{
 const history=new EditHistory();
 history.push({kind:'metadata',nodeId:source.rootNodeId,field:'title',before:'root',merge:'title'});
 history.push({kind:'metadata',nodeId:source.rootNodeId,field:'title',before:'a',merge:'title'});
 assert.equal(history.entries.length,1);assert.equal(history.peek().before,'root');
 history.endText();history.push({kind:'metadata',nodeId:source.rootNodeId,field:'title',before:'ab',merge:'title'});
 assert.equal(history.entries.length,2);
 for(let index=0;index<101;index++)history.push({kind:'position',nodeId:source.rootNodeId,before:{x:index,y:0}});
 assert.equal(history.entries.length,100);history.clear();assert.equal(history.peek(),undefined);
});
test('metadata and position reversal do not alter battle identity, snapshots or stored actions',()=>{
 const moved=updateNativeNodeMetadata(source,source.rootNodeId,{title:'renamed',note:'note',position:{x:123,y:42}});
 const undone=updateNativeNodeMetadata(moved,source.rootNodeId,{title:source.nodes[source.rootNodeId].title,note:'',position:source.layout[source.rootNodeId]});
 assert.deepEqual(undone.nodes,source.nodes);assert.deepEqual(undone.layout,source.layout);assert.deepEqual(undone.match,source.match);
});
test('copy and subtree deletion can be reversed, but saving descendants seals history',()=>{
 const copy=copyNativeNode(source,base,'root');
 const entry=structureEntry(source,copy.project,base,'root');
 assert.deepEqual(undoStructure(copy.project,entry).nodes,source.nodes);
 const descendant=copyNativeNode(copy.project,copy.branch,'root');
 assert.throws(()=>undoStructure(descendant.project,entry),/SAVED_HISTORY/);
 const deleted=deleteNativeSubtree(descendant.project,copy.node.id);
 const deleteEntry=structureEntry(descendant.project,deleted,descendant.branch,'root');
 assert.deepEqual(undoStructure(deleted,deleteEntry).nodes,descendant.project.nodes);
});
test('only the last uncommitted card or ability is eligible; saved history stays unchanged',()=>{
 assert.equal(reduced.workingTick,100);assert.deepEqual(reduced.actions,[]);assert.equal(work.actions.length,1);
 const ability={id:'ability',tick:92,sequence:1,owner:1,kind:'activate-ability',nativeEntityId:123,nativeAbilityId:456};
 const two=appendConfirmedNativeAction(source,work,ability);
 assert.throws(()=>withoutLastDraftAction(source,two,'play'),/SAVED_HISTORY/);
 assert.deepEqual(withoutLastDraftAction(source,two,'ability').actions,[action]);
 const saved=createNativeCheckpoint(source,work,'saved');
 assert.throws(()=>withoutLastDraftAction(saved.project,saved.branch,'play'),/SAVED_HISTORY/);
 assert.deepEqual(saved.node.actionsFromParent,[action]);
});
test('engine undo validates original and reduced digests before writing journal; no checkpoint is created',async()=>{
 const calls=[];
 const replay=async(p,w,digest)=>{calls.push(['replay',w.actions.length,digest]);return {tick:100,digest:w.actions.length?'original':'reduced'};};
 const journal=async(p,w)=>calls.push(['journal',w.actions.length]);
 const state=await restoreUndoBranch(source,work,paused,reduced,replay,journal);
 assert.equal(state.digest,'reduced');assert.deepEqual(calls,[['replay',1,'original'],['replay',0,undefined],['replay',0,'reduced'],['journal',0]]);
 assert.equal(Object.keys(source.nodes).length,1);assert.deepEqual(work.actions,[action]);
});
for(const failure of ['replay','digest','journal','save','connection'])test(`undo ${failure} failure retains original branch and journal or requires recovery`,async()=>{
 let count=0;let stored=work;let disk=source;let rollbackCount=0;
 const target={...source,title:'Undo structure'};
 const replay=async(p,w,digest)=>{
  count++;
  if(failure==='connection' && count>=2)throw new Error('offline');
  if(failure==='replay' && count===2)throw new Error('invalid snapshot / replay rejected');
  if(failure==='digest' && count===3)return {tick:100,digest:'diverged'};
  if(count>=4)rollbackCount++;
  return {tick:100,digest:w.actions.length?'original':'reduced'};
 };
 const journal=async(p,w)=>{if(failure==='journal' && w.actions.length===0)throw new Error('disk full');stored=w;};
 const persist=async p=>{if(failure==='save' && p===target)throw new Error('save failed');disk=p;};
 await assert.rejects(restoreUndoBranch(source,work,paused,reduced,replay,journal,target,undefined,persist),error=>error instanceof UndoRecoveryError && error.recovered===(failure!=='connection'));
 assert.equal(stored,work);assert.equal(disk,source);assert.deepEqual(work.actions,[action]);
 if(failure!=='connection')assert(rollbackCount>0 || failure==='replay');
});
test('native archive listing excludes legacy and malformed files without reading legacy data',async()=>{
 const reads=[];
 globalThis.window={__TAURI_INTERNALS__:{invoke:async(command,args)=>{
  if(command==='native_test_workspace')return 'C:/test';if(command==='plugin:path|join')return args.paths.join('/');if(command==='ensure_writable_directory')return;
  if(command==='list_project_files')return [{name:'old.cr-replay.json',path:'old.cr-replay.json'},{name:'valid.cr-native-replay.json',path:'valid.cr-native-replay.json'},{name:'bad.cr-native-replay.json',path:'bad.cr-native-replay.json'},{name:'wrong.cr-native-replay.json',path:'wrong.cr-native-replay.json'}];
  if(command==='read_project_file'){reads.push(args.path);return args.path.startsWith('valid')?JSON.stringify(source):args.path.startsWith('wrong')?JSON.stringify({version:1,nodes:{}}):'invalid';}
  throw new Error(command);
 }}};
 assert.equal(isNativeProjectPath('old.cr-replay.json'),false);
 assert.deepEqual((await listReplayArchives()).map(entry=>entry.title),['User title']);assert(!reads.includes('old.cr-replay.json'));
 const before=reads.length;await assert.rejects(readNativeProject('old.cr-replay.json'));assert.equal(reads.length,before);
});
test('continue retains selected node and falls back safely when it no longer exists',async()=>{
 const copied=copyNativeNode(source,base,'root');
 globalThis.window={__TAURI_INTERNALS__:{invoke:async()=>JSON.stringify(copied.project)}};
 let session={path:'test.cr-native-replay.json',nodeId:copied.node.id};
 globalThis.localStorage={getItem:()=>JSON.stringify(session)};
 assert.equal((await reopenNativeSession()).nodeId,copied.node.id);
 session.nodeId='missing';assert.equal((await reopenNativeSession()).nodeId,source.rootNodeId);
});
test('all locale keys and placeholders match and language preference does not touch project data',()=>{
 assert.deepEqual(Object.keys(zh).sort(),Object.keys(en).sort());
 for(const key of Object.keys(zh)){assert(en[key]?.length,key);assert.deepEqual((zh[key].match(/\{\w+\}/g)??[]).sort(),(en[key].match(/\{\w+\}/g)??[]).sort(),key);}
 globalThis.localStorage={getItem:key=>key===LANGUAGE_KEY?'en-US':null};
 assert.equal(storedLanguage(),'en-US');assert.equal(translate(storedLanguage(),'home'),'Home');assert.equal(source.title,'User title');
});

test('interrupted structure undo recovers its original project when the on-disk node was already deleted',async()=>{
 const copied=copyNativeNode(source,base,'root');
 const deleted=deleteNativeSubtree(copied.project,copied.node.id);
 let text;
 globalThis.window={__TAURI_INTERNALS__:{invoke:async(command,args)=>{if(command==='write_native_journal')text=args.data;if(command==='read_native_journal')return text;}}};
 const {writeNativeJournal,readNativeJournal}=await import('../src/native/journal.ts');
 await writeNativeJournal(copied.project,copied.branch,{project:copied.project,digest:'root'});
 const recovered=await readNativeJournal(deleted);
 assert.equal(recovered.checkedOutNodeId,copied.node.id);assert.equal(recovered.recoveryDigest,'root');assert.deepEqual(recovered.recoveryProject.nodes,copied.project.nodes);
 // A normal completed journal no longer retains the recovery backup.
 await writeNativeJournal(copied.project,copied.branch);
 assert.equal((await readNativeJournal(copied.project)).recoveryProject,undefined);
});

test('a recovery backup from another project or configuration is rejected before replay',async()=>{
 const {readNativeJournal}=await import('../src/native/journal.ts');
 const entry={schema:'cr-native-journal.v1',projectId:source.projectId,engine:source.engine,checkedOutNodeId:source.rootNodeId,workingTick:90,actions:[],undoRecovery:{project:{...source,projectId:'other'},digest:'root'}};
 globalThis.window={__TAURI_INTERNALS__:{invoke:async()=>JSON.stringify(entry)}};
 await assert.rejects(readNativeJournal(source),error=>error.code==='journalMismatch');
});

test('seeking back then resuming cannot make a saved future action eligible for draft undo',()=>{
 const saved=createNativeCheckpoint(source,work,'saved');
 // Browsing can expose saved suffix actions as a working branch, then resume clears the scrub route.
 assert.throws(()=>withoutLastDraftAction(saved.project,work,'play'),/SAVED_HISTORY/);
 assert.deepEqual(saved.project.nodes[saved.node.id].actionsFromParent,[action]);
});

test('network disconnects and malformed replay replies use stable codes before UI recovery',async()=>{
 const {NativeBridge}=await import('../src/native/bridge.ts');
 globalThis.window={__TAURI_INTERNALS__:{invoke:async()=>({port:26792,token:'unit-test-only',protocol:'native-bridge.v1'})}};
 const client=await NativeBridge.launch();const previousFetch=globalThis.fetch;
 try {
  globalThis.fetch=async()=>{throw new TypeError('Network disconnected');};
  await assert.rejects(client.post('/v1/pause'),error=>error.code==='requestFailed');
  globalThis.fetch=async()=>({ok:true,json:async()=>{throw new SyntaxError('Invalid response JSON');}});
  await assert.rejects(client.replay(41,[],90),error=>error.code==='replayFailed');
 }finally{globalThis.fetch=previousFetch;}
});
