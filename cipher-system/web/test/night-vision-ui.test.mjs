import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const geometry = readFileSync(new URL("../src/lib/nightVisionGeometry.ts", import.meta.url), "utf8");
const catalog = readFileSync(new URL("../src/lib/guestCatalog.ts", import.meta.url), "utf8");
const host = readFileSync(new URL("../src/components/PanelHost.tsx", import.meta.url), "utf8");
const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");

test("Night Vision chrome is flat without changing geometry or order authority", () => {
  assert.match(nightVision, /fetchNightVisionReplay/);
  assert.match(nightVision, /public-OI GEX heuristic, not verified dealer positioning/);
  assert.match(nightVision, /missing gamma\/OI stays unavailable/);
  assert.match(nightVision, /role="img"/);
  assert.match(nightVision, /aria-label=\{\`\$\{ticker\} candlestick chart\`\}/);
  assert.match(nightVision, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(nightVision, /<SkeletonChart label=\{`Loading \$\{ticker\} chart and gamma levels…`\}/);
  assert.doesNotMatch(nightVision, /rounded-xl|rounded-lg|rounded-md|rounded-\[10px\]|rounded-\[8px\]/);
  assert.doesNotMatch(nightVision, /submit_order|place_order|create_order|TradingClient|OrderClient/);
  assert.match(geometry, /export function buildNightVisionGeometry/);
});

test("guest Night Vision is hybrid live-or-fallback without touching geometry", () => {
  assert.match(catalog, /\{ label: "Night Vision", section: "ANALYZE", mode: "hybrid" \}/);
  assert.match(host, /<NightVision ticker=\{ticker\} toolbarSlot=\{toolbarSlot\} guestMode=\{guestMode\} \/>/);
  assert.match(workbench, /tab === "Chart" && <NightVision ticker=\{ticker\} guestMode=\{guestMode\} \/>/);
  assert.match(nightVision, /status === "error" && guestMode && \(/);
  assert.match(nightVision, /<GuestShowcase panel="Night Vision"/);
  assert.match(nightVision, />\s*Retry\s*</);
  assert.match(nightVision, /data-guest-source=\{guestMode \? \(status === "ready" \? "live" : status === "error" \? "demo" : status\) : undefined\}/);
  assert.match(nightVision, /Live chart · not demo fallback/);
  assert.match(geometry, /export function buildNightVisionGeometry/);
  assert.doesNotMatch(geometry, /submit_order|place_order|create_order/);
});
