import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const helper = readFileSync(new URL("../src/lib/nightVisionKnownNet.ts", import.meta.url), "utf8");
const geometry = readFileSync(new URL("../src/lib/nightVisionGeometry.ts", import.meta.url), "utf8");

function knownStrikeNet(rows, strike, metric) {
  if (!rows?.length) return null;
  const row = rows.find((r) => r.strike === strike);
  if (!row) return null;
  let sum = 0;
  let any = false;
  for (const cell of row.cells) {
    const available =
      metric === "gex" ? (cell.gex_available ?? cell.available) : (cell.vex_available ?? cell.available);
    const raw = metric === "gex" ? cell.net_gex : cell.net_vex;
    if (!available || raw == null || !Number.isFinite(raw)) continue;
    sum += raw;
    any = true;
  }
  return any ? sum : null;
}

test("knownStrikeNet never treats missing exposure as 0", () => {
  assert.equal(
    knownStrikeNet(
      [{ strike: 100, cells: [{ available: true, gex_available: true, net_gex: null, net_vex: null }] }],
      100,
      "gex",
    ),
    null,
  );
  assert.equal(
    knownStrikeNet(
      [{ strike: 100, cells: [{ available: false, gex_available: false, net_gex: 0, net_vex: 0 }] }],
      100,
      "gex",
    ),
    null,
  );
  assert.equal(
    knownStrikeNet(
      [
        {
          strike: 200,
          cells: [
            { available: true, gex_available: true, net_gex: 50, net_vex: 1 },
            { available: true, gex_available: true, net_gex: null, net_vex: null },
          ],
        },
      ],
      200,
      "gex",
    ),
    50,
  );
  assert.equal(
    knownStrikeNet(
      [{ strike: 200, cells: [{ available: true, gex_available: true, net_gex: 0, net_vex: 0 }] }],
      200,
      "gex",
    ),
    0,
  );
});

test("Night Vision X-Ray and GEX bands skip missing levels without touching geometry", () => {
  assert.match(helper, /return any \? sum : null/);
  assert.match(nightVision, /from "@\/lib\/nightVisionKnownNet"/);
  assert.match(nightVision, /v == null \? "unknown" : formatDollar\(v\)/);
  assert.match(nightVision, /knownStrikeNet\(nightVision\?\.rows, lvl\.price, "gex"\) != null/);
  assert.match(nightVision, /\[\.\.\.\(nightVision\?\.levels \?\? \[\]\)\.map\(\(level\) => level\.price\)/);
  assert.match(geometry, /export function buildNightVisionGeometry/);
  assert.doesNotMatch(nightVision, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
