import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const strip = readFileSync(new URL("../src/components/TickerStrip.tsx", import.meta.url), "utf8");
const header = readFileSync(new URL("../src/components/Header.tsx", import.meta.url), "utf8");
const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");
const catalog = readFileSync(new URL("../src/lib/guestCatalog.ts", import.meta.url), "utf8");

test("guest ticker tape is demo and header/workbench quotes are labelled live", () => {
  assert.match(strip, /GUEST_DEMO_QUOTES/);
  assert.match(strip, /Demo tape · not live/);
  assert.match(strip, /Watchlist quotes · demo, not live/);
  assert.match(strip, /guestMode \? " demo"/);
  assert.match(header, /accessMode === "guest" && changePct != null \? \(isYahooFeed\(feed\) \? " delayed" : " live"\)/);
  assert.match(header, /\$\{ticker\} live quote \$\{price\}/);
  assert.match(header, /\$\{ticker\} delayed quote \$\{price\}/);
  assert.match(workbench, /guestMode && !isYahooFeed\(quote\.feed\) \? " · live quote"/);
  assert.match(workbench, /The ticker tape is a demo snapshot/);
  assert.match(workbench, /guestMode \? \["Overview", "Chart", "Options"\]/);
  assert.match(catalog, /\{ label: "Ticker Workbench", section: "ANALYZE", mode: "hybrid" \}/);
  assert.doesNotMatch(strip, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
