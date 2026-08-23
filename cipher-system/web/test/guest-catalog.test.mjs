import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const root = new URL("../src/", import.meta.url);
const catalog = readFileSync(new URL("lib/guestCatalog.ts", root), "utf8");
const sidebar = readFileSync(new URL("components/Sidebar.tsx", root), "utf8");
const page = readFileSync(new URL("app/page.tsx", root), "utf8");
const header = readFileSync(new URL("components/Header.tsx", root), "utf8");
const showcase = readFileSync(new URL("components/panels/GuestShowcase.tsx", root), "utf8");
const host = readFileSync(new URL("components/PanelHost.tsx", root), "utf8");

const panels = [...catalog.matchAll(/\{ label: "([^"]+)", section: "([^"]+)", mode: "([^"]+)" \}/g)]
  .map((match) => ({ label: match[1], section: match[2], mode: match[3] }));
const tickers = catalog.match(/export const GUEST_TICKERS = \[([\s\S]*?)\] as const;/)?.[1]
  .match(/"[A-Z]+"/g)?.map((ticker) => ticker.slice(1, -1)) ?? [];

test("guest catalog is the complete unique safe product surface", () => {
  assert.equal(panels.length, 29);
  assert.equal(new Set(panels.map((panel) => panel.label)).size, panels.length);
  assert.deepEqual(new Set(panels.map((panel) => panel.section)), new Set(["TODAY", "DISCOVER", "ANALYZE", "PLAN", "REVIEW", "LABS"]));
  assert.equal(panels.some((panel) => panel.label === "Autopilot" && panel.mode === "demo"), true);
  assert.equal(panels.some((panel) => panel.section === "SYSTEM"), false);
  assert.match(sidebar, /items\.filter\(\(item\) => GUEST_PANEL_LABELS\.has\(item\.label\)\)/);
});

test("all guest tickers have one shared catalog", () => {
  assert.deepEqual(tickers, ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "MU", "AVGO"]);
  assert.match(page, /GUEST_TICKER_SET\.has/);
  assert.match(header, /setUniverse\(\[\.\.\.GUEST_TICKERS\]\)/);
});

test("every demo or locked panel has substantive content and every hybrid panel has a fallback", () => {
  for (const panel of panels) {
    assert.match(showcase, new RegExp(`(?:"${panel.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"|${panel.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}): \\{ title:`), panel.label);
  }
  assert.match(host, /guestModeType !== "hybrid"/);
  assert.match(host, /guestMode=\{guestMode\}/);
});
