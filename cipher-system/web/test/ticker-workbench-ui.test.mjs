import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");

test("Ticker Workbench is a flat hybrid shell with a bounded guest overview", () => {
  assert.match(workbench, /role="tablist"/);
  assert.match(workbench, /ArrowRight/);
  assert.match(workbench, /ArrowLeft/);
  assert.match(workbench, /tabIndex=\{tab === item \? 0 : -1\}/);
  assert.match(workbench, /visibleTabs\.length - 1/);
  assert.match(workbench, /guestMode \? \["Overview", "Chart", "Options"\]/);
  assert.match(workbench, /Flow, company context, agent tools/);
  assert.match(workbench, /tab === "Flow" && !guestMode/);
  assert.match(workbench, /tab === "Company" && !guestMode/);
  assert.match(workbench, /tab === "Agent" && !guestMode/);
  assert.match(workbench, /Ticker Workbench Options is the live chain/);
  assert.match(workbench, /Live chain · not the Options Terminal demo nav/);
  assert.match(workbench, /tab === "Options" && \(/);
  assert.match(workbench, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(workbench, /research only/);
  assert.doesNotMatch(workbench, /rounded-xl|rounded-lg|rounded-md/);
  assert.doesNotMatch(workbench, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
