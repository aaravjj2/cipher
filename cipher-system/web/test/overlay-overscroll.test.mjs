import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
const palette = readFileSync(new URL("../src/components/CommandPalette.tsx", import.meta.url), "utf8");
const sidebar = readFileSync(new URL("../src/components/Sidebar.tsx", import.meta.url), "utf8");
const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const spyglass = readFileSync(new URL("../src/components/panels/Spyglass.tsx", import.meta.url), "utf8");
const heatmap = readFileSync(new URL("../src/components/panels/HeatmapGrid.tsx", import.meta.url), "utf8");

test("dialogs and the mobile drawer contain overscroll without changing heatmap scrollports", () => {
  assert.match(css, /\[role="dialog"\][\s\S]{0,80}overscroll-behavior:\s*contain/);
  assert.match(palette, /role="dialog"/);
  assert.match(palette, /overflow-y-auto overscroll-contain/);
  assert.match(palette, /max-h-\[52vh\] overflow-x-hidden overflow-y-auto overscroll-contain/);
  assert.match(sidebar, /lg:hidden fixed inset-y-0[\s\S]*overscroll-contain/);
  assert.match(sidebar, /overflow-y-auto overscroll-contain/);
  assert.match(nightVision, /function EvidenceDrawer/);
  assert.match(nightVision, /overscroll-contain border border-\[var\(--line\)\]/);
  assert.match(spyglass, /overflow-x-auto rounded-\[10px\]/);
  assert.doesNotMatch(heatmap, /overscroll-contain/);
  assert.doesNotMatch(palette, /submit_order|place_order|TradingClient|OrderClient/);
});
