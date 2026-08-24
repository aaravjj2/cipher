import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const host = readFileSync(new URL("../src/components/PanelHost.tsx", import.meta.url), "utf8");
const showcase = readFileSync(new URL("../src/components/panels/GuestShowcase.tsx", import.meta.url), "utf8");
const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const matrix = readFileSync(new URL("../src/components/panels/StrikeMatrix.tsx", import.meta.url), "utf8");
const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");

test("panels that lacked headings get one sr-only h1 without duplicating visible titles", () => {
  assert.match(host, /function headed\(name: string, node: ReactNode\)/);
  assert.match(host, /<h1 className="sr-only">\{name\}<\/h1>/);
  for (const name of [
    "Strike Matrix",
    "Night Vision",
    "Spyglass",
    "News",
    "Beliefs",
    "Portfolio Risk",
    "Backtest",
    "Strategies",
    "Options Backtest",
    "GEX Replay",
    "Alerts",
    "Trident",
  ]) {
    assert.match(host, new RegExp(`headed\\(\\s*"${name.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}"`));
  }
  assert.doesNotMatch(host, /headed\("Morning Brief"/);
  assert.doesNotMatch(host, /headed\("Holdings"/);
  assert.match(showcase, /titleTag: Title = "h1"/);
  assert.match(nightVision, /titleTag="h2"/);
  assert.match(matrix, /titleTag="h2"/);
  assert.match(workbench, /<h1 className="mt-1 font-sans text-xl font-semibold">Ticker Workbench<\/h1>/);
  assert.match(matrix, /overflow-auto rounded-\[10px\]/);
  assert.doesNotMatch(host, /submit_order|place_order|TradingClient|OrderClient/);
});
