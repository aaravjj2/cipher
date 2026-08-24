import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const heatmap = readFileSync(new URL("../src/components/panels/HeatmapGrid.tsx", import.meta.url), "utf8");
const matrix = readFileSync(new URL("../src/components/panels/StrikeMatrix.tsx", import.meta.url), "utf8");
const trident = readFileSync(new URL("../src/components/panels/Trident.tsx", import.meta.url), "utf8");
const terminal = readFileSync(new URL("../src/components/panels/OptionsTerminal.tsx", import.meta.url), "utf8");
const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");

test("public-OI GEX surfaces show the OI session date", () => {
  assert.match(heatmap, /OI as of \{oiAsOf \?\? "unknown"\}/);
  assert.match(heatmap, /height: "26px"/);
  assert.match(matrix, /oiAsOf=\{data\?\.coverage\.open_interest_as_of\}/);
  assert.match(matrix, /OI as of \{data\.coverage\.open_interest_as_of \?\? "unknown"\}/);
  assert.match(trident, /<ExposureLegend oiAsOf=\{oiAsOf\} \/>/);
  assert.match(terminal, /OI as of \{oiDate \?\? "unknown"\}/);
  assert.match(nightVision, /OI \{provenance\.oiDate \?\? "n\/a"\}/);
  assert.match(heatmap, /GEX is a public-OI heuristic, not verified dealer positioning/);
  assert.doesNotMatch(heatmap, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
