import {useState} from 'react';
import {DEFAULT_NATIVE_LEVEL,NATIVE_CARD_CATALOG,STANDARD_NATIVE_DECK,validateNativeDeck,validateNativeForms,validateNativeDeal,type NativeMatchConfig} from '../domain/nativeReplay.ts';

export interface NativeCreationInput {
  title:string;deck0:number[];deck1:number[];forms0:number[];forms1:number[];
}

export default function NativeMatchDialog({match,busy,ready,onClose,onCreate}:{
  match?:NativeMatchConfig;busy:boolean;ready:boolean;onClose:()=>void;
  onCreate:(input:NativeCreationInput)=>void;
}) {
  const readonly=Boolean(match);
  const omitted=(id:number)=>Boolean(NATIVE_CARD_CATALOG.find(card=>card.id===id)?.omitFromStartingHand);
  const [title,setTitle]=useState('新复盘');
  const [side,setSide]=useState<0|1>(1);
  const [search,setSearch]=useState('');
  const [decks,setDecks]=useState<number[][]>(match?[[...match.deck0],[...match.deck1]]:[[...STANDARD_NATIVE_DECK],[...STANDARD_NATIVE_DECK]]);
  const [forms,setForms]=useState<number[][]>([match?.forms0?.slice()??Array(8).fill(0),match?.forms1?.slice()??Array(8).fill(0)]);
  let error:string|null=null;
  if(!readonly) {
    try {decks.forEach(validateNativeDeck);decks.forEach((deck,owner)=>validateNativeForms(deck,forms[owner]));decks.forEach(validateNativeDeal);if(!title.trim())throw new Error('请输入存档名称');}
    catch(cause){error=String(cause).replace(/^Error: /,'');}
  }
  function remove(index:number):void {
    if(readonly)return;
    setSideCards(decks[side].filter((_,slot)=>slot!==index),forms[side].filter((_,slot)=>slot!==index));
  }
  function setSideCards(cards:number[],modes:number[]):void {
    const ordinary=cards.map((id,index)=>({id,mode:modes[index]})).filter(row=>!omitted(row.id));
    const special=cards.map((id,index)=>({id,mode:modes[index]})).filter(row=>omitted(row.id));
    const positions=special.length===1?[cards.length-1]:special.length===2?[Math.max(0,cards.length-3),cards.length-1]:[];
    const ordered=Array.from({length:cards.length},(_,index)=>positions.includes(index)?special.shift()!:ordinary.shift()!);
    setDecks(rows=>rows.map((row,owner)=>owner===side?ordered.map(card=>card.id):row));
    setForms(rows=>rows.map((row,owner)=>owner===side?ordered.map(card=>card.mode):row));
  }
  function moveTarget(index:number,direction:number):number {
    let next=index+direction;
    while(next>=0&&next<decks[side].length) {
      if(omitted(decks[side][index])===omitted(decks[side][next]))return next;
      next+=direction;
    }
    return -1;
  }
  function move(index:number,direction:number):void {
    if(readonly)return;
    const next=moveTarget(index,direction);if(next<0)return;
    const swap=(row:number[])=>{const copy=row.slice();[copy[index],copy[next]]=[copy[next],copy[index]];return copy;};
    setDecks(rows=>rows.map((row,owner)=>owner===side?swap(row):row));
    setForms(rows=>rows.map((row,owner)=>owner===side?swap(row):row));
  }
  return <div className="native-dialog-backdrop"><section className="native-dialog deck-dialog" role="dialog" aria-modal="true" aria-labelledby="match-heading">
    <div className="dialog-heading"><h2 id="match-heading">{readonly?'初始配置（只读）':'创建复盘存档'}</h2><button onClick={onClose} disabled={busy}>关闭</button></div>
    <p>{readonly?'初始卡组、牌序和等级已随存档锁定。需要其他初始局面时，请新建存档。':'在创建时选择双方八张牌、觉醒或精英形态，并调整初始牌序。创建后初始配置固定。'}
      {readonly?` 等级：${match?.level??'原存档配置'}。`:` 卡牌和塔默认 ${DEFAULT_NATIVE_LEVEL} 级。`}</p>
    {!readonly&&<label className="new-archive-title">存档名称<input aria-label="新存档名称" value={title} onChange={event=>setTitle(event.target.value)} disabled={busy}/></label>}
    <div className="deck-tabs">{([1,0] as const).map(owner=><button className={side===owner?'active':''} key={owner} onClick={()=>setSide(owner)}>{owner===1?'我方':'敌方'} · {decks[owner].length}/8</button>)}
      {!readonly&&<button disabled={busy} onClick={()=>{setDecks([[...STANDARD_NATIVE_DECK],[...STANDARD_NATIVE_DECK]]);setForms([Array(8).fill(0),Array(8).fill(0)]);}}>恢复标准卡组</button>}</div>
    <div className="deck-order-heading">{!readonly||match?.dealMode==='ordered'?'1–4 为起手牌；5–8 为待抽顺序':'旧存档的卡组输入顺序；实际起手牌由原种子固定'}</div>
    <div className="selected-deck">{decks[side].map((id,index)=>{
      const card=NATIVE_CARD_CATALOG.find(card=>card.id===id)!,mode=forms[side][index]??0;
      const art=(mode===1?card.evolutionImage:mode===2?card.heroImage:card.image)??card.image;
      return <article className="selected-deck-card" key={id} data-card-id={id}>
        <div className="initial-card"><span>{index+1}</span>{art?<img src={art} alt=""/>:<div className="hand-card-fallback">{card.name.slice(0,2)}</div>}<strong>{card.name}</strong><small>{card.cost} 圣水</small></div>
        {readonly?<div className="initial-form">{mode===1?'觉醒':mode===2?'精英':'普通'}</div>:<>
          <select aria-label={`${side===1?'我方':'敌方'}${card.name}形态`} value={mode} disabled={busy} onChange={event=>setForms(rows=>rows.map((row,owner)=>owner===side?row.map((value,slot)=>slot===index?Number(event.target.value):value):row))}>
            <option value={0}>普通</option>{Boolean(card.forms&1)&&<option value={1}>觉醒</option>}{Boolean(card.forms&2)&&<option value={2}>精英</option>}</select>
          <div className="initial-order-tools"><button aria-label={`将${card.name}前移`} disabled={busy||moveTarget(index,-1)<0} onClick={()=>move(index,-1)}>←</button><button aria-label={`将${card.name}后移`} disabled={busy||moveTarget(index,1)<0} onClick={()=>move(index,1)}>→</button><button aria-label={`移除${card.name}`} disabled={busy} onClick={()=>remove(index)}>×</button></div>
        </>}
      </article>;
    })}</div>
    {!readonly&&<>
      <input className="card-search" aria-label="搜索卡牌" placeholder="搜索卡牌名称、觉醒或精英…" value={search} onChange={event=>setSearch(event.target.value)} disabled={busy}/>
      <div className="card-catalog">{NATIVE_CARD_CATALOG.filter(card=>`${card.name} ${card.engineName} ${card.forms&1?'觉醒':''} ${card.forms&2?'精英 英雄':''}`.toLowerCase().includes(search.trim().toLowerCase())).map(card=><button key={card.id} className={decks[side].includes(card.id)?'selected':''} disabled={busy||decks[side].includes(card.id)||decks[side].length>=8} onClick={()=>{setSideCards([...decks[side],card.id],[...forms[side],0]);}}>{card.image&&<img src={card.image} alt=""/>}<strong>{card.name}</strong><small>{card.cost} 圣水</small></button>)}</div>
      <div className="deck-footer"><span role={error?'alert':undefined}>{error??'最多两张觉醒、两张精英，合计最多三张；镜像或采集器固定在第 8 位，同时携带时固定在第 6、8 位。'}</span><button className="setup-primary" disabled={!ready||busy||Boolean(error)} onClick={()=>onCreate({title:title.trim(),deck0:decks[0],deck1:decks[1],forms0:forms[0],forms1:forms[1]})}>{busy?'正在校验原生牌序…':'创建存档并开始'}</button></div>
    </>}
  </section></div>;
}
