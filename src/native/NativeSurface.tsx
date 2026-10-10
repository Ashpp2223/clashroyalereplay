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
  onSelectCard: (owner: 0 | 1, slot:number) => void;
  onArenaClick: (pixel: { x: number; y: number }) => void;
  onUnavailable: (reason: string) => void;
}

function CroppedFrame({ image, profile, crop, label, onClick, selected }: {
  image: ImageBitmap | null;
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
  }, [image, profile, crop]);

  function handleClick(event: MouseEvent<HTMLCanvasElement>): void {
    onClick(eventToNativePixel(event, event.currentTarget, crop));
  }

  return <div className={`native-crop ${selected ? "native-crop-selected" : ""}`}>
    <canvas ref={canvasRef} width={648} height={Math.round(648*crop[3]/crop[2])}
      aria-label={label} onClick={handleClick} />
  </div>;
}

export default function NativeSurface({ bridge, ready, profile, selected, observation, disabled, onSelectCard, onArenaClick, onUnavailable }: NativeSurfaceProps) {
  const [image, setImage] = useState<ImageBitmap | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [surfaceWidth, setSurfaceWidth] = useState(250);
  const [arenaWidth,setArenaWidth]=useState(250);
  useEffect(() => {
    const parent = surfaceRef.current?.parentElement;
    if (!parent) return;
    const resize = () => {
      const aspect = profile.arena[3] / profile.arena[2];
      setSurfaceWidth(Math.max(100, Math.min(640, parent.clientWidth - 24)));
      setArenaWidth(Math.max(100,Math.min(460,parent.clientWidth-24,(parent.clientHeight-190)/aspect)));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(parent);
    resize();
    return () => observer.disconnect();
  }, [profile.arena[2],profile.arena[3]]);
  useEffect(() => () => image?.close(), [image]);
  useEffect(() => {
    if (!bridge || !ready) { setImage(null); return; }
    let alive = true;
    let timer: number;
    const refresh = async () => {
      const started=performance.now();
      try {
        const frame = await createImageBitmap(await bridge.frame());
        if (!alive) {
          frame.close();
          return;
        }
        setImage(frame);
        setFrameError(null);
      } catch (error) {
        if (alive) { setFrameError(String(error)); onUnavailable("画面连接中断，战斗已暂停"); }
      } finally {
        if (alive) timer = window.setTimeout(refresh, Math.max(0,33-(performance.now()-started)));
      }
    };
    void refresh();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [bridge, ready, onUnavailable]);

  function hand(owner:0|1):React.ReactNode {
    const player=observation?.players.find(player=>player.owner===owner);
    const cards=[...Array.from({length:4},(_,slot)=>({slot,card:player?.hand.find(card=>card.slot===slot)})),...(player?.cycle??[]).map((id,index)=>({slot:4+index,card:{card:id,cost:NATIVE_CARD_CATALOG.find(card=>card.id===id)?.cost??0,form:0}}))];
    return <section className="native-hand" aria-label={`${owner===1?'我方':'敌方'}八张牌序`}>
      <div className="native-hand-heading"><strong>{owner===1?'我方':'敌方'}</strong><span>手牌 1–4 · 待抽牌按顺序排列</span><span>圣水 {player?(player.elixir/10000).toFixed(1):'—'}</span></div>
      <div className="native-hand-cards">{cards.map(({slot,card})=>{const info=NATIVE_CARD_CATALOG.find(info=>info.id===card?.card),playable=slot<4&&Boolean(card),active=selected?.owner===owner&&selected.slot===slot,form=card?.form??0,art=(form===1?info?.evolutionImage:form===2?info?.heroImage:info?.image)??info?.image;return <button key={slot} className={`${slot>=4?'queued':''} ${active?'selected':''}`} disabled={disabled||!ready||!playable} onClick={()=>onSelectCard(owner,slot)} title={`${slot<4?'手牌':'待抽牌'} ${slot+1} · ${info?.name??'补牌中'}`}>
        <span className="hand-card-number">{slot+1}</span>{art?<img src={art} alt=""/>:<span className="hand-card-fallback">{info?.name?.slice(0,2)??'…'}</span>}<strong>{info?.name??'补牌中'}</strong><small>{slot>=4?`待抽 ${slot-3}`:card?`${card.cost} 圣水${form===1?' · 觉醒':form===2?' · 精英':''}`:''}</small>
      </button>;})}</div>
    </section>;
  }
  return <div className="native-surface" ref={surfaceRef} style={{ "--surface-width": `${surfaceWidth}px`,"--arena-width":`${arenaWidth}px` } as CSSProperties}>
    {hand(0)}
    <div className="native-arena-frame">
    <CroppedFrame image={image} profile={profile} crop={profile.arena} label="原生竞技场"
      onClick={onArenaClick} />
    </div>
    {hand(1)}
    {!ready && <div className="native-surface-message"><strong>战斗尚未就绪</strong><span>连接完成后将在这里显示游戏画面</span></div>}
    {frameError && <div className="native-surface-error">画面暂不可用：{frameError}</div>}
  </div>;
}
