import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const GATE = "UNVALIDATED_FOR_LIVE_OPTIONS_PNL";
const radar = readFileSync(new URL("../src/components/panels/EarningsRadar.tsx", import.meta.url), "utf8");
const showcase = readFileSync(new URL("../src/components/panels/GuestShowcase.tsx", import.meta.url), "utf8");

test("Earnings Radar surfaces UNVALIDATED_FOR_LIVE_OPTIONS_PNL without renaming the gate", () => {
  assert.match(radar, new RegExp(GATE));
  assert.doesNotMatch(radar, /strategy_gate\?\.replaceAll/);
  assert.match(radar, /strategy_gate \?\? "UNVALIDATED_FOR_LIVE_OPTIONS_PNL"/);
  assert.match(radar, /Estimated wins/);
  assert.match(radar, /\{data\.caveat\}/);
  assert.match(radar, /Paper-only recommendations — no order authority/);
  assert.match(showcase, /"Earnings Radar"[\s\S]*UNVALIDATED_FOR_LIVE_OPTIONS_PNL/);
  assert.doesNotMatch(radar, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
