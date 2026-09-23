import { useEffect, useRef, useState, type MouseEvent } from "react";
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
    context.fillStyle = "#101b27";
    context.fillRect(0, 0, crop[2], crop[3]);
    if (!image) return;
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

export default function NativeSurface({ bridge, ready, profile, selected, onHudClick, onArenaClick }: NativeSurfaceProps) {
  const [image, setImage] = useState<ImageBitmap | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  useEffect(() => {
    if (!bridge || !ready) return;
    let alive = true;
    let timer: number;
    let current: ImageBitmap | null = null;
    const refresh = async () => {
      try {
        const frame = await createImageBitmap(await bridge.frame());
        if (!alive) {
          frame.close();
          return;
        }
        const previous = current;
        current = frame;
        setImage(frame);
        setFrameError(null);
        previous?.close();
      } catch (error) {
        if (alive) setFrameError(String(error));
      } finally {
        if (alive) timer = window.setTimeout(refresh, 70);
      }
    };
    void refresh();
    return () => {
      alive = false;
      window.clearTimeout(timer);
      current?.close();
    };
  }, [bridge, ready]);

  return <div className="native-surface">
    <div className="native-surface-label">敌方 · 原生手牌与圣水</div>
    <CroppedFrame image={image} profile={profile} crop={profile.enemyHud} label="敌方原生手牌与圣水"
      selected={selected?.owner === 0} onClick={(pixel) => onHudClick(0, pixel)} />
    <CroppedFrame image={image} profile={profile} crop={profile.arena} label="原生竞技场"
      onClick={onArenaClick} />
    <div className="native-surface-label">我方 · 原生手牌与圣水</div>
    <CroppedFrame image={image} profile={profile} crop={profile.myHud} label="我方原生手牌与圣水"
      selected={selected?.owner === 1} onClick={(pixel) => onHudClick(1, pixel)} />
    {!ready && <div className="native-surface-message">正在等待原生战斗画面…</div>}
    {frameError && <div className="native-surface-error">画面暂不可用：{frameError}</div>}
  </div>;
}
