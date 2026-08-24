import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const desk = readFileSync(new URL("../src/components/panels/ResearchDesk.tsx", import.meta.url), "utf8");

test("Research Desk chrome is flat and remains read-only", () => {
  assert.match(desk, /eligible_for_deeper_review/);
  assert.match(desk, /cannot place orders/);
  assert.match(desk, /intraday/);
  assert.match(desk, /weekly/);
  assert.match(desk, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.doesNotMatch(desk, /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]|rounded-\[10px\]|rounded-\[8px\]/);
  assert.doesNotMatch(desk, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
