import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const autopilot = readFileSync(new URL("../src/components/panels/Autopilot.tsx", import.meta.url), "utf8");
const showcase = readFileSync(new URL("../src/components/panels/GuestShowcase.tsx", import.meta.url), "utf8");

test("Autopilot treats a zero-trade HEALTHY_NO_SETUP session as healthy", () => {
  assert.match(autopilot, /state === "ACTIVE_POSITION" \|\| state === "HEALTHY_NO_SETUP"/);
  assert.match(autopilot, /0 positions · healthy no-trade/);
  assert.match(autopilot, /Zero paper trades is a healthy outcome when no setup qualifies/);
  assert.match(autopilot, /showCurrentBlock && data\.executor\.last_entry_block/);
  assert.match(autopilot, /A healthy no-setup session is a valid outcome/);
  assert.match(autopilot, /Models cannot authorize an order/);
  assert.match(showcase, /Autopilot:[\s\S]*Zero paper trades is a healthy outcome/);
  assert.match(showcase, /A day with no paper trade can be the correct outcome/);
  assert.doesNotMatch(autopilot, /submit_order|place_order|create_order|TradingClient|OrderClient|api\.alpaca\.markets/);
});

test("guest Autopilot uses the hosted API boundary and local-paper copy", () => {
  assert.match(showcase, /hostedApiUrl\("\/api\/agent-showcase"\)/);
  assert.match(showcase, /Cipher local paper/);
  assert.doesNotMatch(showcase, /Alpaca paper only/);
});
