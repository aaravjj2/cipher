import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const skeleton = readFileSync(new URL("../src/components/ui/skeleton.tsx", import.meta.url), "utf8");
const autopilot = readFileSync(new URL("../src/components/panels/Autopilot.tsx", import.meta.url), "utf8");
const paper = readFileSync(new URL("../src/components/panels/PaperPortfolios.tsx", import.meta.url), "utf8");
const earnings = readFileSync(new URL("../src/components/panels/EarningsRadar.tsx", import.meta.url), "utf8");
const holdings = readFileSync(new URL("../src/components/panels/Holdings.tsx", import.meta.url), "utf8");
const company = readFileSync(new URL("../src/components/panels/CompanyContext.tsx", import.meta.url), "utf8");
const chart = readFileSync(new URL("../src/components/panels/ChartWorkbench.tsx", import.meta.url), "utf8");
const options = readFileSync(new URL("../src/components/panels/OptionsTerminal.tsx", import.meta.url), "utf8");
const spyglass = readFileSync(new URL("../src/components/panels/Spyglass.tsx", import.meta.url), "utf8");
const scanner = readFileSync(new URL("../src/components/panels/SetupScanner.tsx", import.meta.url), "utf8");

test("text loading states announce politely without adding holdings skeletons", () => {
  assert.match(skeleton, /export function LoadingStatus/);
  assert.match(skeleton, /role="status" aria-live="polite"/);
  assert.match(skeleton, /export function SkeletonRegion[\s\S]*aria-live="polite"/);
  assert.match(autopilot, /<LoadingStatus[\s\S]*Loading the paper-agent trace/);
  assert.match(paper, /<LoadingStatus[\s\S]*Loading shadow portfolios/);
  assert.match(earnings, /<LoadingStatus[\s\S]*Loading earnings radar/);
  assert.match(holdings, /<LoadingStatus[\s\S]*Loading holdings/);
  assert.doesNotMatch(holdings, /<Skeleton/);
  assert.match(company, /<LoadingStatus[\s\S]*Loading official context/);
  assert.match(chart, /<LoadingStatus[\s\S]*Loading \$\{ticker\} chart/);
  assert.match(options, /<LoadingStatus[\s\S]*Loading option chain/);
  assert.match(spyglass, /<LoadingStatus[\s\S]*Reading the trade tape/);
  assert.match(spyglass, /overflow-x-auto rounded-\[10px\]/);
  assert.match(scanner, /<LoadingStatus[\s\S]*Loading saved scans/);
  assert.doesNotMatch(autopilot, /submit_order|place_order|TradingClient|OrderClient/);
});
