import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");
const geometry = readFileSync(new URL("../src/lib/nightVisionGeometry.ts", import.meta.url), "utf8");

test("Night Vision icon and timeframe controls are named without changing geometry", () => {
  assert.match(nightVision, /aria-label="Refresh chart"/);
  assert.match(nightVision, /<RefreshIcon width=\{13\} height=\{13\} aria-hidden="true" \/>/);
  assert.match(nightVision, /aria-label="Chart timeframe"/);
  assert.match(nightVision, /aria-label=\{`Chart timeframe \$\{tf\}`\}/);
  assert.match(nightVision, /aria-label="More timeframes"/);
  assert.match(nightVision, /aria-haspopup="listbox"/);
  assert.match(nightVision, /aria-label=\{missing \? `Strike \$\{r\.strike\} unknown` : `Strike \$\{r\.strike\}`\}/);
  assert.match(nightVision, /aria-label=\{\`\$\{ticker\} candlestick chart\`\}/);
  assert.match(nightVision, /public-OI GEX heuristic, not verified dealer positioning/);
  assert.match(geometry, /export function buildNightVisionGeometry/);
});
