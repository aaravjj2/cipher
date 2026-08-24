import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");

test("Ticker Workbench Overview states there is no order ticket", () => {
  assert.match(workbench, /Bid\/ask research, Greeks, OI, IV and payoff constraints\. No order ticket\./);
  assert.match(workbench, /Evidence readiness[\s\S]{0,400}No order ticket/);
  assert.match(workbench, /every order capability stay locked\. No order ticket\./);
  assert.match(workbench, /Ticker Workbench Options is the live chain/);
  assert.match(workbench, /Live chain · not the Options Terminal demo nav/);
  assert.doesNotMatch(workbench, /Executable bid\/ask/);
  assert.match(workbench, /guestMode \? \["Overview", "Chart", "Options"\]/);
  assert.match(workbench, /tab === "Flow" && !guestMode/);
  assert.doesNotMatch(workbench, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
