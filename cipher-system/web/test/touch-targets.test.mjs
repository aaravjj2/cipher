import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const header = readFileSync(new URL("../src/components/Header.tsx", import.meta.url), "utf8");
const sidebar = readFileSync(new URL("../src/components/Sidebar.tsx", import.meta.url), "utf8");
const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const chartSaves = readFileSync(new URL("../src/components/panels/ChartSaves.tsx", import.meta.url), "utf8");
const heatmap = readFileSync(new URL("../src/components/panels/HeatmapGrid.tsx", import.meta.url), "utf8");
const matrix = readFileSync(new URL("../src/components/panels/StrikeMatrix.tsx", import.meta.url), "utf8");

test("chrome icon controls are 32px without enlarging heatmap cells", () => {
  assert.match(header, /nav-toggle[\s\S]{0,80}w-8 h-8/);
  assert.match(header, /aria-label=\{`Workspace \$\{n\}`\}[\s\S]{0,200}w-8 h-8/);
  assert.match(sidebar, /side-collapse[\s\S]{0,80}w-8 h-8/);
  assert.match(sidebar, /aria-label="Close navigation"[\s\S]{0,200}w-8 h-8/);
  assert.match(nightVision, /aria-label="Refresh chart"[\s\S]{0,220}h-8 w-8/);
  assert.match(chartSaves, /w-8 h-8 rounded-full/);
  assert.match(heatmap, /height: "26px"/);
  assert.match(matrix, /w-\[30px\] h-\[30px\]/);
  assert.doesNotMatch(header, /submit_order|place_order|TradingClient|OrderClient/);
});
