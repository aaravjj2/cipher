import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const SRC = join(import.meta.dirname, "..", "src");
const read = (relativePath) => readFileSync(join(SRC, relativePath), "utf8");
const CAVEAT = /GEX is a public-OI heuristic, not verified dealer positioning/;

test("GEX-facing remaining panels state the public-OI heuristic", () => {
  assert.match(read("components/panels/HeatmapGrid.tsx"), CAVEAT);
  assert.match(read("components/panels/MorningBrief.tsx"), CAVEAT);
  assert.match(read("components/panels/GexReplay.tsx"), CAVEAT);
  assert.match(read("components/panels/Alerts.tsx"), CAVEAT);
  const showcase = read("components/panels/GuestShowcase.tsx");
  assert.match(showcase, /"Strike Matrix"[\s\S]*GEX is a public-OI heuristic, not verified dealer positioning/);
  assert.match(showcase, /"Trident"[\s\S]*GEX is a public-OI heuristic, not verified dealer positioning/);
  assert.match(read("components/panels/NightVision.tsx"), /public-OI GEX heuristic, not verified dealer positioning/);
  assert.match(read("components/panels/ChartWorkbench.tsx"), /public-OI heuristic/);
  assert.match(read("components/panels/GexReplay.tsx"), /overflow-x-auto[\s\S]*min-w-\[620px\]/);
  assert.match(read("components/panels/HeatmapGrid.tsx"), /height: "26px"/);
});
