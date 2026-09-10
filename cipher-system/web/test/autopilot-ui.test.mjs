import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const autopilot = readFileSync(new URL("../src/components/panels/Autopilot.tsx", import.meta.url), "utf8");

test("Autopilot chrome is flat and stays paper-only", () => {
  assert.match(autopilot, /AUTONOMOUS · PAPER ONLY · AUDITABLE/);
  assert.match(autopilot, /Models cannot authorize an order/);
  assert.match(autopilot, /A healthy no-setup session is a valid outcome/);
  assert.match(autopilot, /Live execution/);
  assert.match(autopilot, /Impossible/);
  assert.match(autopilot, /Local paper portfolio/);
  assert.match(autopilot, /broker orders disabled/);
  assert.doesNotMatch(autopilot, /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]|rounded-\[10px\]|rounded-\[8px\]/);
  assert.doesNotMatch(autopilot, /api\.alpaca\.markets|submit_order|place_order|create_order|TradingClient|OrderClient/);
});
