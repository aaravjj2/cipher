import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const paper = readFileSync(new URL("../src/components/panels/PaperPortfolios.tsx", import.meta.url), "utf8");

test("Paper Portfolios chrome is flat without adding execution authority", () => {
  assert.match(paper, /No simulated fill was created/);
  assert.match(paper, /PAPER ONLY · READ ONLY · EXECUTION CAPABILITY: FALSE/);
  assert.match(paper, /not hypothetical option fills or P&amp;L/);
  assert.doesNotMatch(paper, /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]|rounded-\[10px\]|rounded-\[8px\]/);
  assert.match(paper, /function ScrollTable/);
  assert.match(paper, /overflow-x-auto overflow-y-auto/);
  assert.match(paper, /role="region"/);
  assert.match(paper, /Normalized strategy comparison scrollport/);
  assert.doesNotMatch(paper, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
