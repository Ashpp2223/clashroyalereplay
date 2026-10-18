import { useI18n, errorText } from './i18n.ts';
import {useRef,useState,type PointerEvent} from 'react';
import {DEFAULT_NATIVE_LEVEL,DEFAULT_NATIVE_TOWER,NATIVE_TOWER_CATALOG,NATIVE_CARD_CATALOG,nativeDeckSpecialSlots,validateNativeDeck,validateNativeForms,validateNativeDeal,validateNativeTower,type NativeMatchConfig} from '../domain/nativeReplay.ts';

export interface NativeCreationInput {
  title:string;deck0:number[];deck1:number[];forms0:number[];forms1:number[];
  tower0:number;tower1:number;
}

export default function NativeMatchDialog({match,busy,ready,onClose,onCreate}:{
  match?:NativeMatchConfig;busy:boolean;ready:boolean;onClose:()=>void;
  onCreate:(input:NativeCreationInput)=>void;
}) {
  const {t, language} = useI18n();
  const readonly=Boolean(match);
  const omitted=(id:number)=>Boolean(NATIVE_CARD_CATALOG.find(card=>card.id===id)?.omitFromStartingHand);
  const [title,setTitle]=useState(t("newTitle"));
  const [side,setSide]=useState<0|1>(1);
  const [search,setSearch]=useState('');
  const gesture=useRef<{id:number;side:0|1;x:number;y:number;scale:number;moved:boolean}|null>(null);
  const [drag,setDrag]=useState<{id:number;target:number|null;x:number;y:number}|null>(null);
  const [decks,setDecks]=useState<number[][]>([match?.deck0.slice()??[],match?.deck1.slice()??[]]);
  const [forms,setForms]=useState<number[][]>([match?.forms0?.slice()??Array(match?.deck0.length??0).fill(0),match?.forms1?.slice()??Array(match?.deck1.length??0).fill(0)]);
  const [towers,setTowers]=useState<number[]>([match?.tower0??DEFAULT_NATIVE_TOWER,match?.tower1??DEFAULT_NATIVE_TOWER]);
  const specialSlots=nativeDeckSpecialSlots(decks[side],forms[side]);
  function fitsSpecialSlots(cards:readonly number[],modes:readonly number[]):boolean {
    try {nativeDeckSpecialSlots(cards,modes);return true;}catch{return false;}
  }
  function canSetForm(index:number,mode:number):boolean {
    return fitsSpecialSlots(decks[side],forms[side].map((value,slot)=>slot===index?mode:value));
  }
  let error:string|null=null;
  if(!readonly) {
    try {decks.forEach(validateNativeDeck);decks.forEach((deck,owner)=>validateNativeForms(deck,forms[owner]));decks.forEach(validateNativeDeal);towers.forEach(validateNativeTower);if(!title.trim())throw new Error(t("nameRequired"));}
    catch(cause){error=errorText(cause,language);}
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
    const next=moveTarget(index,direction);if(next<0)return;
    swapCards(index,next);
  }
  function swapCards(index:number,next:number):void {
    if(readonly||busy||index<0||next<0||index===next||omitted(decks[side][index])!==omitted(decks[side][next]))return;
    const swap=(row:number[])=>{const copy=row.slice();[copy[index],copy[next]]=[copy[next],copy[index]];return copy;};
    setDecks(rows=>rows.map((row,owner)=>owner===side?swap(row):row));
    setForms(rows=>rows.map((row,owner)=>owner===side?swap(row):row));
  }
  function cancelDrag():void {gesture.current=null;setDrag(null);}
  function startDrag(event:PointerEvent<HTMLDivElement>,id:number):void {
    if(readonly||busy||event.button!==0||!event.isPrimary)return;
    event.preventDefault();event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current={id,side,x:event.clientX,y:event.clientY,scale:event.currentTarget.getBoundingClientRect().width/event.currentTarget.offsetWidth,moved:false};
  }
  function dropTarget(event:PointerEvent<HTMLDivElement>):number|null {
    const active=gesture.current;if(!active||active.side!==side)return null;
    const target=event.currentTarget.ownerDocument.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLElement>('.selected-deck-card');
    const id=Number(target?.dataset.cardId);
    return target&&target.parentElement===event.currentTarget.closest('.selected-deck')&&id!==active.id&&decks[side].includes(id)&&omitted(id)===omitted(active.id)?id:null;
  }
  function dragCard(event:PointerEvent<HTMLDivElement>):void {
    const active=gesture.current;if(!active)return;
    if(readonly||busy||active.side!==side){cancelDrag();return;}
    const x=event.clientX-active.x,y=event.clientY-active.y;
    if(Math.hypot(x,y)>5)active.moved=true;
    if(active.moved)setDrag({id:active.id,target:dropTarget(event),x:x/active.scale,y:y/active.scale});
  }
  function finishDrag(event:PointerEvent<HTMLDivElement>):void {
    const active=gesture.current,target=dropTarget(event);
    if(active?.moved&&target!==null)swapCards(decks[side].indexOf(active.id),decks[side].indexOf(target));
    cancelDrag();
  }
  return <div className="native-dialog-backdrop"><section className="native-dialog deck-dialog" role="dialog" aria-modal="true" aria-labelledby="match-heading">
    <div className="dialog-heading"><h2 id="match-heading">{readonly?t("config"):t("createTitle")}</h2><button onClick={onClose} disabled={busy}>{t("close")}</button></div>
    <p>{readonly?t("lockedLead"):t("deckLead")}
      {readonly?(match?.level?t("levelValue",{level:match.level}):t("originalLevel")):t("levelValue",{level:DEFAULT_NATIVE_LEVEL})}</p>
    {!readonly&&<label className="new-archive-title">{t("archiveName")}<input aria-label={t("newArchiveName")} value={title} onChange={event=>setTitle(event.target.value)} disabled={busy}/></label>}
    <div className="deck-tabs">{([1,0] as const).map(owner=><button className={side===owner?'active':''} key={owner} onClick={()=>setSide(owner)}>{owner===1?t("own"):t("enemy")} · {decks[owner].length}/8</button>)}</div>
    <div className="tower-config">{([1,0] as const).map(owner=><label key={owner}>{t("towerLabel",{side:t(owner===1?"own":"enemy")})}
      <select aria-label={t("towerLabel",{side:t(owner===1?"own":"enemy")})} value={towers[owner]} disabled={readonly||busy} onChange={event=>setTowers(rows=>rows.map((value,index)=>index===owner?Number(event.target.value):value))}>
        {NATIVE_TOWER_CATALOG.map(card=><option key={card.id} value={card.id}>{language==='en-US'?card.engineName:card.name}</option>)}
      </select></label>)}</div>
    <div className="special-deck-slots">{(['evolutionSlot','heroSlot','wildSlot'] as const).map(slot=>{
      const index=specialSlots.indexOf(slot),card=NATIVE_CARD_CATALOG.find(card=>card.id===decks[side][index]);
      return <div key={slot} className={`deck-special-slot ${slot}`} data-occupied={Boolean(card)}><strong>{t(slot)}</strong><small>{card?(language==='en-US'?card.engineName:card.name):t('unusedSlot')}</small></div>;
    })}</div>
    <div className="deck-order-heading">{!readonly||match?.dealMode==='ordered'?t("order"):t("seededOrder")}{!readonly&&<> · {t("dragOrderHint")}</>}</div>
    <div className="selected-deck">{decks[side].map((id,index)=>{
      const card=NATIVE_CARD_CATALOG.find(card=>card.id===id)!,mode=forms[side][index]??0;
      const art=(mode===1?card.evolutionImage:mode===2?card.heroImage:card.image)??card.image;
      return <article className="selected-deck-card" key={id} data-card-id={id} data-special-slot={specialSlots[index]??undefined}
        data-dragging={drag?.id===id||undefined} data-drop-target={drag?.target===id||undefined}
        style={drag?.id===id?{transform:`translate(${drag.x}px,${drag.y}px)`}:undefined}>
        <div className="initial-card" role={readonly?undefined:'button'} tabIndex={readonly||busy?undefined:0} aria-disabled={readonly?undefined:busy}
          aria-label={readonly?undefined:t("dragCard",{card:language==="en-US"?card.engineName:card.name})} title={readonly?undefined:t("dragOrderHint")}
          onPointerDown={event=>startDrag(event,id)} onPointerMove={dragCard} onPointerUp={finishDrag} onPointerCancel={cancelDrag} onLostPointerCapture={cancelDrag}
          onKeyDown={event=>{if(event.key==='Escape')cancelDrag();else if(!gesture.current&&(event.key==='ArrowLeft'||event.key==='ArrowRight')){event.preventDefault();move(index,event.key==='ArrowLeft'?-1:1);}}}>
          <span>{index+1}</span>{art?<img src={art} alt="" draggable={false}/>:<div className="hand-card-fallback">{(language==="en-US"?card.engineName:card.name).slice(0,2)}</div>}<strong>{(language==="en-US"?card.engineName:card.name)}</strong><small>{card.cost} {t("elixir")}</small></div>
        {readonly?<div className="initial-form">{card.rarity==='champion'?t("champion"):mode===1?t("evolution"):mode===2?t("hero"):t("normal")}</div>:<>
          {card.forms&&card.rarity!=='champion'?<select aria-label={t("formLabel",{side:t(side===1?"own":"enemy"),card:language==="en-US"?card.engineName:card.name})} value={mode} disabled={busy} onChange={event=>{const next=Number(event.target.value);if(canSetForm(index,next))setForms(rows=>rows.map((row,owner)=>owner===side?row.map((value,slot)=>slot===index?next:value):row));}}>
            <option value={0}>{t("normal")}</option>{Boolean(card.forms&1)&&<option value={1} disabled={!canSetForm(index,1)}>{t("evolution")}</option>}{Boolean(card.forms&2)&&<option value={2} disabled={!canSetForm(index,2)}>{t("hero")}</option>}</select>:<div className="initial-form">{t(card.rarity==='champion'?'champion':'normal')}</div>}
          <div className="initial-order-tools"><button aria-label={t("removeCard",{card:language==="en-US"?card.engineName:card.name})} disabled={busy} onClick={()=>remove(index)}>×</button></div>
        </>}
      </article>;
    })}</div>
    {!readonly&&<>
      <input className="card-search" aria-label={t("search")} placeholder={t("searchHint")} value={search} onChange={event=>setSearch(event.target.value)} disabled={busy}/>
      <div className="card-catalog">{NATIVE_CARD_CATALOG.filter(card=>`${(language==="en-US"?card.engineName:card.name)} ${card.engineName} ${card.forms&1?t("evolution"):''} ${card.forms&2?t("hero"):''} ${card.rarity==='champion'?t("champion"):''}`.toLowerCase().includes(search.trim().toLowerCase())).map(card=><button key={card.id} data-card-id={card.id} className={decks[side].includes(card.id)?'selected':''} disabled={busy||decks[side].includes(card.id)||decks[side].length>=8||!fitsSpecialSlots([...decks[side],card.id],[...forms[side],0])} onClick={()=>{if(fitsSpecialSlots([...decks[side],card.id],[...forms[side],0]))setSideCards([...decks[side],card.id],[...forms[side],0]);}}>{card.image&&<img src={card.image} alt=""/>}<strong>{(language==="en-US"?card.engineName:card.name)}</strong><small>{card.cost} {t("elixir")}</small></button>)}</div>
      <div className="deck-footer"><span role={error?'alert':undefined}>{error??t("formLimit")}</span><button className="setup-primary" disabled={!ready||busy||Boolean(error)} onClick={()=>onCreate({title:title.trim(),deck0:decks[0],deck1:decks[1],forms0:forms[0],forms1:forms[1],tower0:towers[0],tower1:towers[1]})}>{busy?t("validating"):t("createStart")}</button></div>
    </>}
  </section></div>;
}
