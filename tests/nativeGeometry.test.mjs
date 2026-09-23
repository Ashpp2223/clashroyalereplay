import assert from "node:assert/strict";
import { test } from "node:test";
import { arenaPixelToWorld, eventToNativePixel, handSlotAtPixel } from "../src/native/geometry.ts";

const profile = {
  id: "stock-v15-1080x1920", nativeWidth: 1080, nativeHeight: 1920,
  enemyHud: [0, 0, 1080, 282], arena: [54, 282, 972, 1384],
  myHud: [0, 1666, 1080, 254], world: [111, 436, 856, 1230],
  handCentersX: [211, 354, 497, 640], enemyHandReversed: true
};

test("enemy stock HUD reverses visual hand order while own hand stays native", () => {
  assert.equal(handSlotAtPixel(profile, 0, { x: 211, y: 171 }), 3);
  assert.equal(handSlotAtPixel(profile, 1, { x: 211, y: 1749 }), 0);
  assert.equal(handSlotAtPixel(profile, 0, { x: 10, y: 171 }), null);
});

test("cropped viewport click maps back to native pixel and world cell", () => {
  const element = { getBoundingClientRect: () => ({ left: 100, top: 50, width: 486, height: 692 }) };
  const pixel = eventToNativePixel({ clientX: 343, clientY: 396 }, element, profile.arena);
  assert.deepEqual(pixel, { x: 540, y: 974 });
  assert.deepEqual(arenaPixelToWorld(profile, pixel), { x: 9500, y: 13500 });
  assert.deepEqual(arenaPixelToWorld(profile, { x: 54, y: 282 }), { x: 500, y: 500 });
});
