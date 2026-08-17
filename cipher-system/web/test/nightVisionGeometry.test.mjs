import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(join(tmpdir(), "cipher-night-vision-"));
execFileSync(join(WEB, "node_modules", ".bin", "tsc"), [
  join(WEB, "src", "lib", "nightVisionGeometry.ts"), "--outDir", outDir,
  "--module", "esnext", "--target", "es2022", "--moduleResolution", "bundler", "--skipLibCheck",
]);
const { buildNightVisionGeometry, isRegularSessionBar, nearestBarIndex, visibleTail } = await import(join(outDir, "nightVisionGeometry.js"));

test("candles, spot, and levels share one monotonic mapping", () => {
  const geometry = buildNightVisionGeometry([{ low: 98, high: 102 }], 100, [99, 101], 20, 400);
  assert.ok(geometry.priceToY(102) < geometry.priceToY(100));
  assert.ok(geometry.priceToY(100) < geometry.priceToY(98));
  assert.ok(geometry.priceToY(100) >= 20 && geometry.priceToY(100) <= 420);
});

test("a distant exposure level cannot crush the candle range", () => {
  const geometry = buildNightVisionGeometry([{ low: 99, high: 101 }], 100, [1000], 0, 400);
  assert.ok(geometry.domainMax < 103);
  assert.ok(geometry.domainMin > 97);
});

test("crosshair snaps to a valid visible bar and rejects the axes", () => {
  assert.equal(nearestBarIndex(10, 10, 100, 5), 0);
  assert.equal(nearestBarIndex(109, 10, 100, 5), 4);
  assert.equal(nearestBarIndex(111, 10, 100, 5), null);
});

test("range selection always returns a stable tail", () => {
  assert.deepEqual(visibleTail([1, 2, 3, 4], 2), [3, 4]);
  assert.deepEqual(visibleTail([1, 2], 60), [1, 2]);
});

test("RTH filtering is defined in New York time rather than the browser timezone", () => {
  assert.equal(isRegularSessionBar("2026-08-13T13:30:00Z"), true); // 09:30 ET
  assert.equal(isRegularSessionBar("2026-08-13T19:59:00Z"), true); // 15:59 ET
  assert.equal(isRegularSessionBar("2026-08-13T20:00:00Z"), false);
});
