import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const radar = readFileSync(new URL("../src/components/panels/EarningsRadar.tsx", import.meta.url), "utf8");

test("Earnings Radar chrome is flat and keeps missing data unknown", () => {
  assert.match(radar, /unavailable, never zero/);
  assert.match(radar, /Paper-only recommendations — no order authority/);
  assert.match(radar, /\{data\.caveat\}/);
  assert.match(radar, /aria-label="Upcoming earnings radar"/);
  assert.match(radar, /Estimated wins/);
  assert.match(radar, /overflow-x-auto overflow-y-auto/);
  assert.match(radar, /role="region"/);
  assert.match(radar, /Upcoming earnings radar scrollport/);
  assert.doesNotMatch(radar, /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]|rounded-\[10px\]|rounded-\[8px\]/);
  assert.doesNotMatch(radar, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
