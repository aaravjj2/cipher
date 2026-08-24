import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const paper = readFileSync(new URL("../src/components/panels/PaperPortfolios.tsx", import.meta.url), "utf8");
const morning = readFileSync(new URL("../src/components/panels/MorningBrief.tsx", import.meta.url), "utf8");
const earnings = readFileSync(new URL("../src/components/panels/EarningsRadar.tsx", import.meta.url), "utf8");

test("paper P&L copy separates captured marks from estimated premiums", () => {
  assert.match(paper, /marked \(captured mid\)/);
  assert.match(paper, /realized \(captured fills\)/);
  assert.match(paper, /Estimated heuristic premiums are not used here/);
  assert.match(paper, /Unknown marks stay labelled, never invented/);
  assert.match(paper, /not hypothetical option fills or P&amp;L/);
  assert.match(morning, /Captured paper P&amp;L/);
  assert.match(morning, /not estimated premiums/);
  assert.match(earnings, /Estimated P&amp;L/);
  assert.match(earnings, /heuristic premiums/);
  assert.doesNotMatch(paper, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
