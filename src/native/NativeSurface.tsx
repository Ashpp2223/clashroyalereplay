import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import type { CropProfile } from "./bridge.ts";
import { NativeBridge } from "./bridge.ts";
import { eventToNativePixel, type CropRect } from "./geometry.ts";

interface NativeSurfaceProps {
  bridge: NativeBridge | null;
  ready: boolean;
  profile: CropProfile;
  selected: { owner: 0 | 1; slot: number } | null;
  onHudClick: (owner: 0 | 1, pixel: { x: number; y: number }) => void;
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
    context.fillRect(0, 0, crop[2], crop[3]);
    if (!image || !image.width) return;
    const scale = image.width / profile.nativeWidth;
    const surfaceHeight = profile.nativeHeight * scale;
    const insetY = Math.max(0, (image.height - surfaceHeight) / 2);
    context.drawImage(image, crop[0] * scale, insetY + crop[1] * scale,
      crop[2] * scale, crop[3] * scale, 0, 0, crop[2], crop[3]);
  }, [image, profile, crop]);

  function handleClick(event: MouseEvent<HTMLCanvasElement>): void {
    onClick(eventToNativePixel(event, event.currentTarget, crop));
  }

  return <div className={`native-crop ${selected ? "native-crop-selected" : ""}`}>
    <canvas ref={canvasRef} width={crop[2]} height={crop[3]}
      aria-label={label} onClick={handleClick} />
  </div>;
}

export default function NativeSurface({ bridge, ready, profile, selected, onHudClick, onArenaClick, onUnavailable }: NativeSurfaceProps) {
  const [image, setImage] = useState<ImageBitmap | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [surfaceWidth, setSurfaceWidth] = useState(250);
  useEffect(() => {
    const parent = surfaceRef.current?.parentElement;
    if (!parent) return;
    const resize = () => {
      const aspect = profile.enemyHud[3] / profile.enemyHud[2] + profile.arena[3] / profile.arena[2] + profile.myHud[3] / profile.myHud[2];
      setSurfaceWidth(Math.max(100, Math.min(460, parent.clientWidth - 24, (parent.clientHeight - 50) / aspect)));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(parent);
    resize();
    return () => observer.disconnect();
  }, [profile]);
  useEffect(() => () => image?.close(), [image]);
  useEffect(() => {
    if (!bridge || !ready) { setImage(null); return; }
    let alive = true;
    let timer: number;
    const refresh = async () => {
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
        if (alive) timer = window.setTimeout(refresh, 70);
      }
    };
    void refresh();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [bridge, ready, onUnavailable]);

  return <div className="native-surface" ref={surfaceRef} style={{ "--surface-width": `${surfaceWidth}px` } as CSSProperties}>
    <div className="native-surface-label">敌方 · 原生手牌与圣水</div>
    <CroppedFrame image={image} profile={profile} crop={profile.enemyHud} label="敌方原生手牌与圣水"
      selected={selected?.owner === 0} onClick={(pixel) => onHudClick(0, pixel)} />
    <CroppedFrame image={image} profile={profile} crop={profile.arena} label="原生竞技场"
      onClick={onArenaClick} />
    <div className="native-surface-label">我方 · 原生手牌与圣水</div>
    <CroppedFrame image={image} profile={profile} crop={profile.myHud} label="我方原生手牌与圣水"
      selected={selected?.owner === 1} onClick={(pixel) => onHudClick(1, pixel)} />
    {!ready && <div className="native-surface-message"><strong>战斗尚未就绪</strong><span>连接完成后将在这里显示游戏画面</span></div>}
    {frameError && <div className="native-surface-error">画面暂不可用：{frameError}</div>}
  </div>;
}
