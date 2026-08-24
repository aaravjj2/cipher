import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (name) => readFileSync(new URL(`../src/components/panels/${name}`, import.meta.url), "utf8");
const CHROME = /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]/;

test("remaining 009–028 panel chrome is flattened without order authority", () => {
  const files = {
    "PortfolioRisk.tsx": ["Research only · no broker sync · no order actions"],
    "ChartWorkbench.tsx": ["public-OI heuristic", "candlestick chart with volume and research overlays"],
    "CompanyContext.tsx": ["conflicts are never silently resolved", "No order capability"],
    "Holdings.tsx": ["does not read a brokerage account or place orders", "overflow-x-auto overflow-y-auto", "Option positions scrollport", "min-w-[760px]"],
    "Standing.tsx": ["Cipher places no live"],
    "Watchlists.tsx": ["They observe; they never trade"],
    "TraderJournal.tsx": ["not option-premium P/L", "overflow-x-auto overflow-y-auto", "Trader journal entries scrollport"],
    "Beliefs.tsx": ["what would change its"],
    "ChartSaves.tsx": ["There is no chart-image capture"],
    "Backtest.tsx": ["next-open · stop-first · research only", "overflow-x-auto overflow-y-auto", "Backtest partition results scrollport"],
    "OptionsBacktest.tsx": ["no arbitrary command or broker access is available"],
    "GexReplay.tsx": ["value == null ? \"unknown\""],
    "Trident.tsx": ["overflow-y-auto overflow-x-hidden"],
    "News.tsx": ["overflow-x-auto overflow-y-auto", "headlines scrollport"],
    "AskCipher.tsx": ["blocked"],
    "Settings.tsx": ["credentials never leave the core service"],
    "OperatorStatus.tsx": ["No execution capability"],
    "FlowTape.tsx": [],
  };
  for (const [file, needles] of Object.entries(files)) {
    const src = read(file);
    assert.doesNotMatch(src, CHROME, file);
    assert.doesNotMatch(src, /submit_order|place_order|create_order|TradingClient|OrderClient/, file);
    for (const needle of needles) {
      assert.ok(src.includes(needle), `${file} missing ${needle}`);
    }
  }
  const spyglass = read("Spyglass.tsx");
  assert.match(spyglass, /overflow-x-auto rounded-\[10px\]/);
  const catalog = read("StrategyCatalog.tsx");
  assert.match(catalog, /overflow-x-auto rounded-\[8px\]/);
  assert.match(catalog, /role="region"/);
  assert.match(catalog, /Strategy catalog verdicts scrollport/);
});
