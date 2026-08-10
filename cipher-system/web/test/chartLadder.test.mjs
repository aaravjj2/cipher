/**
 * Guards for lib/chartLadder.ts — the Chart Saves thumbnail geometry.
 *
 * These exist because of a specific defect, not for coverage. The thumbnail used to draw
 * a seeded random-walk candlestick series in raw 0..100 space while drawing the level
 * lines in price space. Two marks at the same height therefore meant two unrelated
 * things, and the result read as "price came down and reacted to your level" when no
 * price series had been measured at all.
 *
 * So the invariant is: one mapping, used for every mark, monotonic in price. If someone
 * later reintroduces a second coordinate space, these fail.
 *
 * Run with: node --test web/test/*.test.mjs
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(join(tmpdir(), "cipher-ladder-"));

execFileSync(
  join(WEB, "node_modules", ".bin", "tsc"),
  [
    join(WEB, "src", "lib", "chartLadder.ts"),
    "--outDir", outDir,
    "--module", "esnext",
    "--target", "es2022",
    "--moduleResolution", "bundler",
    // chartLadder.ts is deliberately import-free, so a bare tsc with no `@/` path
    // mapping can compile it.
    "--skipLibCheck",
  ],
  { stdio: "inherit" },
);

const { buildLadder, CHART_H, CHART_W, LABEL_W } = await import(join(outDir, "chartLadder.js"));

const card = (price, levels) => ({
  id: "x",
  ticker: "SPY",
  price,
  view: "1 Exp",
  dateAdded: "8/10/26",
  topLevels: levels.map(([level, score]) => ({ level, score })),
  imageUrl: "",
});

test("a higher price is higher on screen", () => {
  const { yFor } = buildLadder(card(100, [[90, 5], [110, 8]]));
  assert.ok(yFor(110) < yFor(100));
  assert.ok(yFor(100) < yFor(90));
});

test("spot and levels share one mapping — the bug that motivated this file", () => {
  const c = card(100, [[90, 5], [110, 8]]);
  const { yFor, spotY } = buildLadder(c);
  // spotY must be nothing more than yFor applied to the spot price.
  assert.equal(spotY, yFor(100));
  // And a level equal to spot must land at exactly the same height.
  const same = buildLadder(card(100, [[100, 5], [110, 8]]));
  assert.equal(same.yFor(100), same.spotY);
});

test("every mark stays inside the viewbox", () => {
  const { yFor } = buildLadder(card(307.44, [[300, 3], [315, 9], [307.44, 1]]));
  for (const p of [300, 307.44, 315]) {
    assert.ok(yFor(p) >= 0 && yFor(p) <= CHART_H, `${p} -> ${yFor(p)}`);
  }
});

test("the extremes span the padded height, so the axis is actually used", () => {
  const { yFor } = buildLadder(card(100, [[90, 5], [110, 8]]));
  assert.ok(yFor(90) - yFor(110) > CHART_H * 0.5);
});

test("a single level does not divide by zero or fly to an edge", () => {
  const { yFor, spotY } = buildLadder(card(400, [[400, 7]]));
  assert.equal(spotY, CHART_H / 2);
  assert.equal(yFor(400), CHART_H / 2);
  assert.ok(Number.isFinite(spotY));
});

test("no levels at all still yields finite geometry", () => {
  const { spotY, barFor } = buildLadder(card(50, []));
  assert.ok(Number.isFinite(spotY));
  assert.ok(Number.isFinite(barFor(0)));
});

test("bar length is proportional to score, strongest level filling the track", () => {
  const { barFor } = buildLadder(card(100, [[90, 50], [110, 100]]));
  const track = CHART_W - LABEL_W - 16;
  assert.equal(barFor(100), track);
  assert.ok(Math.abs(barFor(50) - track / 2) < 1e-9);
});

test("a zero or negative score still renders a visible stub, never a negative width", () => {
  const { barFor } = buildLadder(card(100, [[90, 0], [110, 10]]));
  assert.ok(barFor(0) > 0);
  assert.ok(barFor(-5) > 0);
});
