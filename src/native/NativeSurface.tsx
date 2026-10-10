import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import type { CropProfile, NativeObservation } from "./bridge.ts";
import { NativeBridge } from "./bridge.ts";
import { NATIVE_CARD_CATALOG } from '../domain/nativeReplay.ts';
import { eventToNativePixel, type CropRect } from "./geometry.ts";

interface NativeSurfaceProps {
  bridge: NativeBridge | null;
  ready: boolean;
  profile: CropProfile;
  selected: { owner: 0 | 1; slot: number } | null;
  observation:NativeObservation|null;
  disabled:boolean;
  paused:boolean;
  framePending:boolean;
  frameRevision:number;
  frameCacheKey?:string|null;
  onFrameRendered:(revision:number)=>void;
  onSelectCard: (owner: 0 | 1, slot:number) => void;
  onArenaClick: (pixel: { x: number; y: number }) => void;
  onUnavailable: (reason: string) => void;
}

function CroppedFrame({ image, imageRevision, frameSource, onFrameRendered, profile, crop, label, onClick, selected }: {
  image: ImageBitmap | null;
  imageRevision:number;
  frameSource:'cache'|'live';
  onFrameRendered:(revision:number)=>void;
  profile: CropProfile;
  crop: CropRect;
  label: string;
  onClick: (pixel: { x: number; y: number }) => void;
  selected?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) return;
    context.fillStyle = "#e9f0f6";
    context.fillRect(0, 0, canvas.width, canvas.height);
    if (!image || !image.width) return;
    const scale = image.width / profile.nativeWidth;
    const surfaceHeight = profile.nativeHeight * scale;
    const insetY = Math.max(0, (image.height - surfaceHeight) / 2);
    context.drawImage(image, crop[0] * scale, insetY + crop[1] * scale,
      crop[2] * scale, crop[3] * scale, 0, 0, canvas.width, canvas.height);
    canvas.dataset.frameRevision=String(imageRevision);
    canvas.dataset.frameSource=frameSource;
    onFrameRendered(imageRevision);
  }, [image, imageRevision, frameSource, onFrameRendered, profile, crop]);

  function handleClick(event: MouseEvent<HTMLCanvasElement>): void {
    onClick(eventToNativePixel(event, event.currentTarget, crop));
  }

  return <div className={`native-crop ${selected ? "native-crop-selected" : ""}`}>
    <canvas ref={canvasRef} width={648} height={Math.round(648*crop[3]/crop[2])}
      aria-label={label} onClick={handleClick} />
  </div>;
}

export default function NativeSurface({ bridge, ready, profile, selected, observation, disabled, paused, framePending, frameRevision, frameCacheKey, onFrameRendered, onSelectCard, onArenaClick, onUnavailable }: NativeSurfaceProps) {
  const [image, setImage] = useState<{bitmap:ImageBitmap;revision:number;source:'cache'|'live'} | null>(null);
  const frameCache=useRef(new Map<string,Blob>());
  const [frameError, setFrameError] = useState<string | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [surfaceWidth, setSurfaceWidth] = useState(250);
  const [arenaWidth,setArenaWidth]=useState(250);
  useEffect(() => {
    const parent = surfaceRef.current?.parentElement;
    if (!parent) return;
    const resize = () => {
      const aspect = profile.arena[3] / profile.arena[2];
      const handHeight=Array.from(surfaceRef.current?.querySelectorAll<HTMLElement>('.native-hand')??[]).reduce((height,hand)=>height+hand.offsetHeight,0);
      setSurfaceWidth(Math.max(100, Math.min(640, parent.clientWidth - 24)));
      setArenaWidth(Math.max(100,Math.min(460,parent.clientWidth-24,(parent.clientHeight-24-handHeight)/aspect)));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(parent);
    surfaceRef.current?.querySelectorAll('.native-hand').forEach(hand=>observer.observe(hand));
    resize();
    return () => observer.disconnect();
  }, [profile.arena[2],profile.arena[3]]);
  useEffect(() => () => image?.bitmap.close(), [image]);
  useEffect(() => {
    if (!bridge || !ready) { setImage(null); return; }
    if (disabled) return;
    let alive = true;
    let timer: number;
    const refresh = async () => {
      const started=performance.now();
      try {
        const key=paused?frameCacheKey:null;
        const cached=key?frameCache.current.get(key):undefined;
        const bytes=cached??await bridge.frame(paused);
        const frame = await createImageBitmap(bytes);
        if (!alive) {
          frame.close();
          return;
        }
        if(key) {
          frameCache.current.delete(key);frameCache.current.set(key,bytes);
          let total=[...frameCache.current.values()].reduce((sum,blob)=>sum+blob.size,0);
          while(frameCache.current.size>32||total>32*1024*1024) {
            const oldest=frameCache.current.keys().next().value as string;
            total-=frameCache.current.get(oldest)!.size;frameCache.current.delete(oldest);
          }
        }
        setImage({bitmap:frame,revision:frameRevision,source:cached?'cache':'live'});
        setFrameError(null);
      } catch (error) {
        if (alive) { setFrameError(String(error)); onUnavailable("画面连接中断，战斗已暂停"); }
      } finally {
        if (alive && !paused) timer = window.setTimeout(refresh, Math.max(0,33-(performance.now()-started)));
      }
    };
    void refresh();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [bridge, ready, disabled, paused, frameRevision, frameCacheKey, onUnavailable]);

  function hand(owner:0|1):React.ReactNode {
    const player=observation?.players.find(player=>player.owner===owner);
    const elixir=player&&Number.isFinite(player.elixir)?Math.max(0,Math.min(10,player.elixir/10000)):null;
    const elixirDigits=elixir===null?null:elixir.toFixed(1).split('.');
    const cards=[...Array.from({length:4},(_,slot)=>({slot,card:player?.hand.find(card=>card.slot===slot)})),...(player?.cycle??[]).map((id,index)=>({slot:4+index,card:{card:id,cost:NATIVE_CARD_CATALOG.find(card=>card.id===id)?.cost??0,form:0}}))];
    const renderCard=({slot,card}:(typeof cards)[number]):React.ReactNode=>{
      const info=NATIVE_CARD_CATALOG.find(info=>info.id===card?.card),inHand=slot<4&&Boolean(card),affordable=Boolean(card&&player&&player.elixir>=card.cost*10000),playable=inHand&&affordable,active=playable&&selected?.owner===owner&&selected.slot===slot,form=card?.form??0,art=(form===1?info?.evolutionImage:form===2?info?.heroImage:info?.image)??info?.image;
      return <button key={slot} className={`${slot>=4?'queued':''} ${!playable?'unavailable':''} ${active?'selected':''}`} disabled={disabled||framePending||!ready||!playable} onClick={()=>onSelectCard(owner,slot)} title={`${slot<4?'手牌':'待抽牌'} ${slot+1} · ${info?.name??'补牌中'}${slot>=4?' · 尚未抽到':card&&!affordable?' · 圣水不足':''}`}>
        <span className="hand-card-number">{slot+1}</span><span className="hand-card-art">
          {art?<img src={art} alt=""/>:<span className="hand-card-fallback">{info?.name?.slice(0,2)??'…'}</span>}
          {card&&<span className="hand-card-cost" aria-label={`${card.cost} 圣水`}><img src="/elixir.png" alt="" draggable={false}/><b aria-hidden="true">{card.cost}</b></span>}
          {(form===1||form===2)&&<small className="hand-card-form">{form===1?'觉醒':'精英'}</small>}
        </span><strong>{info?.name??'补牌中'}</strong>
      </button>;
    };
    return <section className="native-hand" aria-label={`${owner===1?'我方':'敌方'}八张牌序`}>
      <div className="native-hand-heading"><strong>{owner===1?'我方':'敌方'}</strong>
        <div className={`native-elixir ${elixir===10?'full':''} ${elixir===null?'unavailable':''}`} role="meter"
          aria-label={`${owner===1?'我方':'敌方'}圣水`} aria-valuemin={0} aria-valuemax={10}
          aria-valuenow={elixir??undefined} aria-valuetext={elixir===null?'尚未获取':`${elixir.toFixed(1)} / 10`}>
          <div className="native-elixir-track">
            <div className="native-elixir-fill" style={{width:`${(elixir??0)*10}%`}} />
            <div className="native-elixir-segments" aria-hidden="true">{Array.from({length:10},(_,index)=><span key={index}/>)}</div>
          </div>
          <div className="native-elixir-value" aria-hidden="true"><img src="/elixir.png" alt="" draggable={false}/><b><span className="native-elixir-integer">{elixirDigits?.[0]??'—'}</span>{elixirDigits&&<span className="native-elixir-fraction">.{elixirDigits[1]}</span>}</b></div>
        </div>
      </div>
      <div className="native-hand-groups">
        <div className="native-hand-group current" role="group" aria-label={`${owner===1?'我方':'敌方'}手牌`}>
          <div className="native-hand-cards">{cards.slice(0,4).map(renderCard)}</div>
        </div>
        <div className="native-hand-group queue" role="group" aria-label={`${owner===1?'我方':'敌方'}待抽牌`}>
          <div className="native-hand-cards">{cards.slice(4).map(renderCard)}</div>
        </div>
      </div>
    </section>;
  }
  return <div className="native-surface" ref={surfaceRef} data-control-revision={frameRevision} style={{ "--surface-width": `${surfaceWidth}px`,"--arena-width":`${arenaWidth}px` } as CSSProperties}>
    {hand(0)}
    <div className="native-arena-frame">
    <CroppedFrame image={image?.bitmap??null} imageRevision={image?.revision??-1} frameSource={image?.source??'live'} onFrameRendered={onFrameRendered} profile={profile} crop={profile.arena} label="原生竞技场"
      onClick={onArenaClick} />
    </div>
    {hand(1)}
    {!ready && <div className="native-surface-message"><strong>战斗尚未就绪</strong><span>连接完成后将在这里显示游戏画面</span></div>}
    {frameError && <div className="native-surface-error">画面暂不可用：{frameError}</div>}
  </div>;
}
