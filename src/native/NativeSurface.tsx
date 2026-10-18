import { useI18n } from './i18n.ts';
import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { CropProfile, NativeObservation } from "./bridge.ts";
import { NativeBridge } from "./bridge.ts";
import { NATIVE_CARD_CATALOG } from '../domain/nativeReplay.ts';
import { eventToNativePixel } from "./geometry.ts";
import gameAbilityIcons from '../data/nativeAbilityIcons.json' with { type: 'json' };

const abilityIcons:Record<string,{image:string;cost:number}>=gameAbilityIcons.abilities;

interface NativeSurfaceProps {
  bridge: NativeBridge | null;
  ready: boolean;
  profile: CropProfile;
  selected: { owner: 0 | 1; slot: number } | null;
  observation:NativeObservation|null;
  disabled:boolean;
  readOnly?:boolean;
  paused:boolean;
  framePending:boolean;
  frameRevision:number;
  frameCacheKey?:string|null;
  onFrameRendered:(revision:number)=>void;
  onSelectCard: (owner: 0 | 1, slot:number) => void;
  onActivateAbility: (owner:0|1, entityId:number, abilityId:number) => void;
  onArenaClick: (pixel: { x: number; y: number }) => void;
  onUnavailable: (reason: string) => void;
}

export default function NativeSurface({ bridge, ready, profile, selected, observation, disabled, readOnly=false, paused, framePending, frameRevision, frameCacheKey, onFrameRendered, onSelectCard, onActivateAbility, onArenaClick, onUnavailable }: NativeSurfaceProps) {
  const {t, language} = useI18n();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const displayedFrame = useRef<{bridge:NativeBridge;key:string;width:number;height:number}|null>(null);
  const renderedRef = useRef(onFrameRendered);
  renderedRef.current = onFrameRendered;
  const [frameError, setFrameError] = useState<string | null>(null);
  const frameErrorRef = useRef(false);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [surfaceWidth, setSurfaceWidth] = useState(250);
  const [arenaWidth,setArenaWidth]=useState(250);
  useEffect(() => {
    const parent = surfaceRef.current?.parentElement;
    if (!parent) return;
    const resize = () => {
      const aspect = profile.arena[3] / profile.arena[2];
      const handHeight=Array.from(surfaceRef.current?.querySelectorAll<HTMLElement>('.native-hand')??[]).reduce((height,hand)=>height+hand.offsetHeight,0);
      const width=Math.max(100, Math.min(640, parent.clientWidth - 24));
      setSurfaceWidth(width);
      // Reserve the side rails even before a skill appears, so deployment and
      // cooldown changes cannot resize or move the arena.
      setArenaWidth(Math.max(100,Math.min(460,width-144,(parent.clientHeight-24-handHeight)/aspect)));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(parent);
    surfaceRef.current?.querySelectorAll('.native-hand').forEach(hand=>observer.observe(hand));
    resize();
    return () => observer.disconnect();
  }, [profile.arena[2],profile.arena[3]]);
  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d", { alpha: false });
    if (!canvas || !context) return;
    if (!bridge || !ready) {
      displayedFrame.current=null;
      context.fillStyle = "#e9f0f6";
      context.fillRect(0, 0, canvas.width, canvas.height);
      return;
    }
    if (disabled) return;
    const displayed=displayedFrame.current;
    if(paused&&frameCacheKey&&displayed?.bridge===bridge&&displayed.key===frameCacheKey&&
        displayed.width===canvas.width&&displayed.height===canvas.height) {
      // Saving or changing speed at the same verified state can keep the
      // pixels already on the canvas, including while PNG packing finishes.
      canvas.dataset.frameRevision=String(frameRevision);
      canvas.dataset.frameSource='cache';
      renderedRef.current(frameRevision);
      return;
    }
    let alive = true;
    let timer: number;
    const controller = new AbortController();
    const refresh = async () => {
      const started=performance.now();
      try {
        const frameCache=bridge.frameCache;
        const key=paused?frameCacheKey:null;
        const cached=key?frameCache.get(key):undefined;
        const cropped=cached?.cropped??false;
        const bytes=cached?.image??await bridge.frame(paused, controller.signal);
        if (!alive) return;
        const frame = await createImageBitmap(bytes);
        try {
          if (!alive) return;
          const scale = frame.width / profile.nativeWidth;
          const insetY = Math.max(0, (frame.height - profile.nativeHeight * scale) / 2);
          const crop = profile.arena;
          // Video pixels do not belong in React state: only the arena changes
          // at video frequency, while cards and controls follow observations.
          if(cropped)context.drawImage(frame,0,0,canvas.width,canvas.height);
          else context.drawImage(frame, crop[0] * scale, insetY + crop[1] * scale,
              crop[2] * scale, crop[3] * scale, 0, 0, canvas.width, canvas.height);
          displayedFrame.current=key?{bridge,key,width:canvas.width,height:canvas.height}:null;
          canvas.dataset.frameRevision = String(frameRevision);
          canvas.dataset.frameSource = cached ? 'cache' : 'live';
          renderedRef.current(frameRevision);
        } finally {
          frame.close();
        }
        if(key) {
          const entry=cached??{image:bytes,cropped:false};
          const trim=()=>{
            let total=[...frameCache.values()].reduce((sum,value)=>sum+value.image.size,0);
            while(frameCache.size>32||total>32*1024*1024) {
              const oldest=frameCache.keys().next().value as string;
              total-=frameCache.get(oldest)!.image.size;frameCache.delete(oldest);
            }
          };
          frameCache.delete(key);frameCache.set(key,entry);
          trim();
          if(!entry.cropped) {
            // Snapshot the displayed pixels now and compress off the display
            // path. Keep the original usable until the cropped PNG is ready.
            canvas.toBlob(image=>{
              if(image&&frameCache.get(key)===entry) {
                entry.image=image;entry.cropped=true;trim();
              }
            },"image/png");
          }
        }
        if (frameErrorRef.current) {
          frameErrorRef.current = false;
          setFrameError(null);
        }
      } catch (error) {
        if (alive) { void bridge.recordFailure('frame-render',error); frameErrorRef.current = true; setFrameError(String(error)); onUnavailable(t("frameInterrupted")); }
      } finally {
        if (alive && !paused) timer = window.setTimeout(refresh, Math.max(0,33-(performance.now()-started)));
      }
    };
    void refresh();
    return () => {
      alive = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [bridge, ready, disabled, paused, frameRevision, frameCacheKey, onUnavailable,
    profile.nativeWidth, profile.nativeHeight, ...profile.arena]);

  function hand(owner:0|1):React.ReactNode {
    const player=observation?.players.find(player=>player.owner===owner);
    const elixir=player&&Number.isFinite(player.elixir)?Math.max(0,Math.min(10,player.elixir/10000)):null;
    const elixirDigits=elixir===null?null:elixir.toFixed(1).split('.');
    const cards=[...Array.from({length:4},(_,slot)=>({slot,card:player?.hand.find(card=>card.slot===slot)})),...(player?.cycle??[]).map((id,index)=>({slot:4+index,card:{card:id,cost:NATIVE_CARD_CATALOG.find(card=>card.id===id)?.cost??0,form:0}}))];
    const renderCard=({slot,card}:(typeof cards)[number]):React.ReactNode=>{
      const info=NATIVE_CARD_CATALOG.find(info=>info.id===card?.card),inHand=slot<4&&Boolean(card),affordable=Boolean(card&&player&&player.elixir>=card.cost*10000),playable=inHand&&affordable,active=playable&&selected?.owner===owner&&selected.slot===slot,form=card?.form??0,art=(form===1?info?.evolutionImage:form===2?info?.heroImage:info?.image)??info?.image;
      return <button key={slot} className={`${slot>=4?'queued':''} ${!playable?'unavailable':''} ${active?'selected':''}`} aria-pressed={active} disabled={disabled||readOnly||framePending||!ready||!playable} onClick={()=>onSelectCard(owner,slot)} title={`${slot<4?t("hand"):t("queue")} ${slot+1} · ${(language==="en-US"?info?.engineName:info?.name)??t("refilling")}${slot>=4?` · ${t("notDrawn")}`:card&&!affordable?` · ${t("unaffordable")}`:''}`}>
        <span className="hand-card-number">{slot+1}</span><span className="hand-card-art">
          {art?<img src={art} alt=""/>:<span className="hand-card-fallback">{(language==="en-US"?info?.engineName:info?.name)?.slice(0,2)??'…'}</span>}
          {card&&<span className="hand-card-cost" aria-label={`${card.cost} ${t("elixir")}`}><img src="/elixir.png" alt="" draggable={false}/><b aria-hidden="true">{card.cost}</b></span>}
          {(form===1||form===2)&&<small className="hand-card-form">{form===1?t("evolution"):t("hero")}</small>}
        </span><strong>{(language==="en-US"?info?.engineName:info?.name)??t("refilling")}</strong>
      </button>;
    };
    return <section className="native-hand" aria-label={`${owner===1?t("own"):t("enemy")}${t("cardOrder")}`}>
      <div className="native-hand-heading"><strong>{owner===1?t("own"):t("enemy")}</strong>
        <div className={`native-elixir ${elixir===10?'full':''} ${elixir===null?'unavailable':''}`} role="meter"
          aria-label={`${owner===1?t("own"):t("enemy")} ${t("elixir")}`} aria-valuemin={0} aria-valuemax={10}
          aria-valuenow={elixir??undefined} aria-valuetext={elixir===null?t("unavailable"):`${elixir.toFixed(1)} / 10`}>
          <div className="native-elixir-track">
            <div className="native-elixir-fill" style={{width:`${(elixir??0)*10}%`}} />
            <div className="native-elixir-segments" aria-hidden="true">{Array.from({length:10},(_,index)=><span key={index}/>)}</div>
          </div>
          <div className="native-elixir-value" aria-hidden="true"><img src="/elixir.png" alt="" draggable={false}/><b><span className="native-elixir-integer">{elixirDigits?.[0]??'—'}</span>{elixirDigits&&<span className="native-elixir-fraction">.{elixirDigits[1]}</span>}</b></div>
        </div>
      </div>
      <div className="native-hand-groups">
        <div className="native-hand-group current" role="group" aria-label={`${owner===1?t("own"):t("enemy")} ${t("hand")}`}>
          <div className="native-hand-cards">{cards.slice(0,4).map(renderCard)}</div>
        </div>
        <div className="native-hand-group queue" role="group" aria-label={`${owner===1?t("own"):t("enemy")} ${t("queue")}`}>
          <div className="native-hand-cards">{cards.slice(4).map(renderCard)}</div>
        </div>
      </div>
    </section>;
  }
  function abilities(owner:0|1):React.ReactNode {
    const player=observation?.players.find(player=>player.owner===owner);
    return <div className={`native-abilities ${owner===0?'enemy':'own'}`} role="group" aria-label={`${owner===0?t('enemy'):t('own')} · ${t('ability')}`}>
      {(player?.runtime?.abilityRuntime??[]).map(ability=>{
        const entity=ability.championEntityKeys.find(key=>key?.owner===owner);
        const icon=abilityIcons[ability.actionDataName];
        const card=NATIVE_CARD_CATALOG.find(card=>card.abilityNames.includes(ability.actionDataName));
        const cooldown=ability.remainingCooldownMs>0?`${(ability.remainingCooldownMs/1000).toFixed(1)}s`:null;
        const label=`${owner===0?t('enemy'):t('own')} · ${(language==='en-US'?card?.engineName:card?.name)??t('ability')}${cooldown?` · ${cooldown}`:''}`;
        return <button key={ability.controllerSlot} className="native-ability" aria-label={label} title={label}
          disabled={disabled||readOnly||framePending||!ready||!entity||!ability.available||ability.remainingCooldownMs>0}
          onClick={()=>{if(entity)onActivateAbility(owner,entity.secondaryIndex,ability.actionDataGlobalId);}}>
          {icon?<img className="native-ability-icon" src={icon.image} alt="" draggable={false}/>:<span>{t('ability')}</span>}
          {icon&&<span className="native-ability-cost"><img src="/elixir.png" alt="" draggable={false}/><b>{icon.cost}</b></span>}
          {cooldown&&<span className="native-ability-cooldown">{cooldown}</span>}
        </button>;
      })}
    </div>;
  }
  return <div className="native-surface" ref={surfaceRef} data-control-revision={frameRevision} style={{ "--surface-width": `${surfaceWidth}px`,"--arena-width":`${arenaWidth}px` } as CSSProperties}>
    {hand(0)}
    <div className="native-arena-region">
    <div className="native-arena-frame">
    <div className="native-crop">
      <canvas ref={canvasRef} width={648} height={Math.round(648*profile.arena[3]/profile.arena[2])}
        aria-label={t("arena")} onClick={event=>{if(!readOnly)onArenaClick(eventToNativePixel(event,event.currentTarget,profile.arena));}} />
    </div>
    </div>
    {abilities(0)}
    {abilities(1)}
    </div>
    {hand(1)}
    {!ready && <div className="native-surface-message"><strong>{t("battleNotReady")}</strong><span>{t("pictureLead")}</span></div>}
    {frameError && <div className="native-surface-error">{t("pictureError")}{frameError}</div>}
  </div>;
}
