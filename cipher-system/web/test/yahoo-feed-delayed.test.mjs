import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const helper = readFileSync(new URL("../src/lib/feedLabel.ts", import.meta.url), "utf8");
const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");
const terminal = readFileSync(new URL("../src/components/panels/OptionsTerminal.tsx", import.meta.url), "utf8");
const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const desk = readFileSync(new URL("../src/components/panels/ResearchDesk.tsx", import.meta.url), "utf8");
const header = readFileSync(new URL("../src/components/Header.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/app/page.tsx", import.meta.url), "utf8");
const settings = readFileSync(new URL("../src/components/panels/Settings.tsx", import.meta.url), "utf8");

test("Yahoo/yfinance feeds are labelled delayed, never live", () => {
  assert.match(helper, /return isYahooFeed\(feed\) \? "yahoo delayed" : feed/);
  assert.match(workbench, /feedLabel\(quote\.feed\)/);
  assert.match(workbench, /guestMode && !isYahooFeed\(quote\.feed\) \? " · live quote"/);
  assert.match(terminal, /feedLabel\(chain\.feed\)/);
  assert.match(nightVision, /feedLabel\(snapshot\.feed\)/);
  assert.match(desk, /feedLabel\(row\.observed\.feed\)/);
  assert.match(header, /isYahooFeed\(feed\) \? " delayed" : " live"/);
  assert.match(page, /feed=\{quote\?\.feed\}/);
  assert.match(settings, /delayed Yahoo Finance\/yfinance/);
  assert.match(settings, /delayed available/);
  assert.doesNotMatch(workbench, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
