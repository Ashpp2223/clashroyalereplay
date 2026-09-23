import type { CropProfile } from "./bridge.ts";

export type CropRect = [number, number, number, number];

export function eventToNativePixel(
  event: { clientX: number; clientY: number },
  element: HTMLElement,
  crop: CropRect
): { x: number; y: number } {
  const bounds = element.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) throw new Error("画面尺寸无效");
  const localX = (event.clientX - bounds.left) / bounds.width;
  const localY = (event.clientY - bounds.top) / bounds.height;
  return {
    x: Math.round(crop[0] + Math.min(1, Math.max(0, localX)) * crop[2]),
    y: Math.round(crop[1] + Math.min(1, Math.max(0, localY)) * crop[3])
  };
}

export function handSlotAtPixel(
  profile: CropProfile,
  owner: 0 | 1,
  pixel: { x: number; y: number }
): number | null {
  const handY = owner === 0 ? 171 : 1749;
  if (Math.abs(pixel.y - handY) > 86) return null;
  const visualIndex = profile.handCentersX.findIndex((x) => Math.abs(pixel.x - x) <= 67);
  if (visualIndex === -1) return null;
  return owner === 0 && profile.enemyHandReversed ? 3 - visualIndex : visualIndex;
}

export function arenaPixelToWorld(profile: CropProfile, pixel: { x: number; y: number }): { x: number; y: number } {
  const [left, top, width, height] = profile.world;
  const cell = (coordinate: number, start: number, size: number, count: number): number => {
    const index = Math.floor((coordinate - start) * count / size);
    return (Math.min(count - 1, Math.max(0, index)) * 1000) + 500;
  };
  return { x: cell(pixel.x, left, width, 18), y: cell(pixel.y, top, height, 32) };
}
