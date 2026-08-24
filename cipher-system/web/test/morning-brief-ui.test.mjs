import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const morning = readFileSync(new URL("../src/components/panels/MorningBrief.tsx", import.meta.url), "utf8");

test("Morning Brief chrome is flat without expanding copy or adding order authority", () => {
  for (const section of ["Market now", "Focus ·", "Paper status", "Setups to review"]) {
    assert.match(morning, new RegExp(section));
  }
  assert.match(morning, /Flow refresh pending; unknown, not zero/);
  assert.match(morning, /Flow unavailable; no observation inferred/);
  assert.match(morning, /no broker-order capability/);
  assert.match(morning, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.ok(morning.split("<Card").length - 1 <= 5);
  assert.doesNotMatch(morning, /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]|rounded-\[10px\]|rounded-\[8px\]/);
  assert.doesNotMatch(morning, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
