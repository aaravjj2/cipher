import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const SRC = join(import.meta.dirname, "..", "src");
const read = (relativePath) => readFileSync(join(SRC, relativePath), "utf8");

const HEATMAP = read("components/panels/HeatmapGrid.tsx");
const MATRIX = read("components/panels/StrikeMatrix.tsx");
const TRIDENT = read("components/panels/Trident.tsx");
const FLOW_TAPE = read("components/panels/FlowTape.tsx");
const NEWS = read("components/panels/News.tsx");
const GEX_REPLAY = read("components/panels/GexReplay.tsx");
const OPTIONS_BACKTEST = read("components/panels/OptionsBacktest.tsx");
const STANDING = read("components/panels/Standing.tsx");
const SPYGLASS = read("components/panels/Spyglass.tsx");
const STRATEGY_CATALOG = read("components/panels/StrategyCatalog.tsx");
const SKELETON = read("components/ui/skeleton.tsx");
const NIGHT_VISION = read("components/panels/NightVision.tsx");
const HOLDINGS = read("components/panels/Holdings.tsx");
const GLOBALS = read("app/globals.css");

test("shared heatmap primitives retain the signed-exposure legend contract", () => {
  assert.match(HEATMAP, /export function ExposureLegend/);
  assert.match(HEATMAP, /aria-label="Exposure color scale"/);
  assert.match(HEATMAP, /Positive exposure · purple/);
  assert.match(HEATMAP, /Negative exposure · red/);
  assert.match(HEATMAP, /Largest \|exposure\|/);
  assert.match(HEATMAP, /Intensity is relative within each heatmap/);
});

test("Strike Matrix exposes a read-only table with row and cell labels", () => {
  assert.match(MATRIX, /role="table"/);
  assert.match(MATRIX, /<div role="row"/);
  assert.match(MATRIX, /role="columnheader"/);
  assert.match(HEATMAP, /role=\{ariaLabel \? "rowheader"/);
  assert.match(HEATMAP, /role=\{ariaLabel \? "cell"/);
  assert.match(MATRIX, /ariaLabel=\{`/);
  assert.match(MATRIX, /formatDollar\(value\)/);
  assert.match(MATRIX, /const available = metric === "gex"/);
  assert.match(MATRIX, /value: available \? value : null/);
  assert.match(MATRIX, /value == null \? "unknown"/);
  assert.match(MATRIX, /largest absolute exposure/);
  assert.doesNotMatch(MATRIX, /role="grid"/);
});

test("Trident exposes column headers and full cell context", () => {
  assert.match(TRIDENT, /role="table"/);
  assert.match(TRIDENT, /<div role="row" className="sr-only">/);
  assert.match(TRIDENT, /<div role="columnheader">Strike<\/div>/);
  assert.match(TRIDENT, /role="columnheader">\{column\.metric\.toUpperCase\(\)\} exposure/);
  assert.match(TRIDENT, /ariaLabel=\{`/);
  assert.match(TRIDENT, /formatDollar\(value\)/);
  assert.match(TRIDENT, /const available = metric === "gex"/);
  assert.match(TRIDENT, /value: available \? \(metric === "gex" \? cell\.net_gex : cell\.net_vex\) : null/);
  assert.match(TRIDENT, /value == null \? "unknown"/);
  assert.match(TRIDENT, /largest absolute exposure/);
  assert.match(TRIDENT, /role="row" style=\{\{ display: "contents" \}\}/);
  assert.doesNotMatch(TRIDENT, /role="grid"/);
});

test("Trident Auto controls the existing refresh interval", () => {
  assert.match(TRIDENT, /if \(!flags\.auto\) return;/);
  assert.match(TRIDENT, /setInterval\(\(\) => load\(undefined, true\), AUTO_REFRESH_MS\)/);
  assert.match(TRIDENT, /\[flags\.auto, load\]/);
});

test("unavailable exposure remains unknown instead of being displayed as zero", () => {
  assert.match(HEATMAP, /const unavailable = value == null/);
  assert.match(HEATMAP, /Unknown: no listed\/calculable exposure/);
  assert.match(HEATMAP, /unavailable && "unknown"/);
  assert.match(TRIDENT, /value == null \? "unknown"/);
  assert.match(TRIDENT, /modeled: Boolean\(cell\.gamma_modeled/);
});

test("loading primitives preserve an announced status region", () => {
  assert.match(SKELETON, /export function SkeletonRegion/);
  assert.match(SKELETON, /role="status"/);
  assert.match(SKELETON, /aria-live="polite"/);
  assert.match(SKELETON, /export function SkeletonGrid/);
  assert.match(MATRIX, /<SkeletonGrid/);
  assert.match(TRIDENT, /<SkeletonGrid label="Loading live Trident exposure…"/);
});

test("the placeholder is suppressed for fast loads rather than flashing", () => {
  // Standing resolves in ~144ms and Holdings in ~3ms. A skeleton shown for that long is a
  // flash, so the region starts hidden and reveals only if the fetch is still running.
  assert.match(SKELETON, /cipher-skeleton-region/);
  assert.match(GLOBALS, /@keyframes cipher-skeleton-reveal/);
  assert.match(GLOBALS, /animation: cipher-skeleton-reveal 0\.25s step-end/);
  // step-end is what makes this a delay rather than a fade; a linear/ease curve would make
  // the placeholder visible immediately at partial opacity and defeat the point.
  assert.match(GLOBALS, /from \{ visibility: hidden; \}/);
  // The pulse still has to be dropped under reduced motion, and the reveal still has to
  // apply, so the exemption must name only the pulse.
  const reduced = GLOBALS.slice(GLOBALS.indexOf("prefers-reduced-motion"));
  assert.match(reduced, /\.cipher-skeleton \{\s*animation: none;/);
  assert.doesNotMatch(reduced, /cipher-skeleton-region/);
});

test("Night Vision uses a chart-shaped placeholder, and fast panels use none", () => {
  // The night-vision payload measured 742 KB / 4.2s warm, which earns a placeholder.
  assert.match(NIGHT_VISION, /import \{ SkeletonChart \} from "@\/components\/ui\/skeleton"/);
  assert.match(NIGHT_VISION, /<SkeletonChart label=\{`Loading live \$\{ticker\} chart and gamma levels…`\}/);
  assert.match(SKELETON, /export function SkeletonChart/);
  // A grid-shaped placeholder in front of a candlestick chart moves the layout instead of
  // holding it, which is most of the reason to prefer a skeleton over a line of text.
  assert.doesNotMatch(NIGHT_VISION, /<SkeletonGrid/);
  // Holdings is a 3ms fetch: it must keep its text rather than gain a placeholder.
  assert.doesNotMatch(HOLDINGS, /Skeleton/);
});

test("Plan 2 panels use shaped loading states instead of bare loading labels", () => {
  assert.match(FLOW_TAPE, /import \{ Skeleton, SkeletonRegion \} from "@\/components\/ui\/skeleton"/);
  assert.match(FLOW_TAPE, /Loading flow prints for \$\{ticker\}/);
  assert.match(NEWS, /import \{ SkeletonCards \} from "@\/components\/ui\/skeleton"/);
  assert.match(NEWS, /<SkeletonCards label=\{`Loading \$\{ticker\} headlines…`\}/);
  assert.match(GEX_REPLAY, /<SkeletonCards label=\{`Loading captured GEX history for \$\{ticker\}…`\}/);
  assert.match(GEX_REPLAY, /<SkeletonGrid label="Loading captured GEX strike profiles…"/);
  assert.match(GEX_REPLAY, /<SkeletonGrid label="Loading selected GEX strike profile…"/);
  assert.match(GEX_REPLAY, /catalogRequest/);
  assert.match(GEX_REPLAY, /currentPayload/);
  assert.match(GEX_REPLAY, /loadedSnapshotId/);
  assert.match(GEX_REPLAY, /overflow-x-auto[\s\S]*min-w-\[620px\][\s\S]*max-h-\[620px\] overflow-y-auto/);
  assert.match(SPYGLASS, /Contract Search has six dense, fixed-content columns[\s\S]*overflow-x-auto rounded-\[10px\][\s\S]*min-w-\[560px\][\s\S]*<table/);
  assert.match(STRATEGY_CATALOG, /seven-column verdict register readable on narrow screens[\s\S]*overflow-x-auto rounded-\[8px\][\s\S]*min-w-\[760px\]/);
  assert.match(OPTIONS_BACKTEST, /<SkeletonCards label="Loading historical options research catalog…"/);
  assert.match(OPTIONS_BACKTEST, /no research datasets are registered yet/);
  assert.match(STANDING, /<SkeletonCards label="Loading standing and accrual status…"/);
  assert.match(STANDING, /statusLoading/);
  assert.match(STANDING, /unavailable while the core service is offline/);
});

test("dense tables expose accessible labels and column headers", () => {
  assert.match(GEX_REPLAY, /role="table"[\s\S]*captured GEX strike profile/);
  assert.match(GEX_REPLAY, /role="columnheader">Call GEX/);
  assert.match(GEX_REPLAY, /\["Call GEX", "Put GEX", "Net GEX"\]/);
  assert.match(GEX_REPLAY, /role="cell" aria-label=\{`\$\{\["Call GEX", "Put GEX", "Net GEX"\]\[cell\]\}/);
  assert.match(SPYGLASS, /aria-label="Options flow prints"/);
  assert.match(SPYGLASS, /aria-label="Contract search trade tape"/);
  assert.match(SPYGLASS, /scope="col"/);
  assert.match(STRATEGY_CATALOG, /aria-label="Strategy catalog verdicts"/);
  assert.match(STRATEGY_CATALOG, /scope="col"/);
  assert.match(STRATEGY_CATALOG, /<th scope="row"/);
});

test("Plan 2 empty states explain the next useful action", () => {
  assert.match(FLOW_TAPE, /Lower the premium threshold or keep Live on/);
  assert.match(GEX_REPLAY, /Capture history first, then return here to replay it/);
});
