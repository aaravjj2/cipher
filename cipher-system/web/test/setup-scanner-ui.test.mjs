import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const scanner = readFileSync(new URL("../src/components/panels/SetupScanner.tsx", import.meta.url), "utf8");

test("Setup Scanner chrome is flat without changing jobs, scoring copy, or order authority", () => {
  for (const preset of ["Intraday", "Weekly", "Momentum", "Mean reversion", "Index momentum", "Exposure zones"]) {
    assert.match(scanner, new RegExp(`label: "${preset}"`));
  }
  assert.match(scanner, /confidence describes evidence coverage, not a predicted win rate/);
  assert.match(scanner, /Rejection funnel:/);
  assert.match(scanner, /function ResultComparison/);
  assert.match(scanner, /<details key=\{raw\.ticker\}/);
  assert.match(scanner, /Select up to three candidates/);
  assert.match(scanner, /aria-label="Ranked scan results"/);
  assert.match(scanner, /overflow-x-auto overflow-y-auto border" role="region" aria-label="Ranked scan results"/);
  assert.match(scanner, /activeSelectedTickers\.length >= 3/);
  assert.match(scanner, /Expected move<\/dt><dd>Not observed/);
  assert.match(scanner, /Catalyst<\/dt><dd>Not observed/);
  assert.match(scanner, /raw\.evidence_snapshot\.snapshot_id\.slice\(0, 12\)/);
  assert.match(scanner, /cipher:night-vision-replay/);
  assert.match(scanner, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(scanner, /tone = "accent"/);
  assert.doesNotMatch(scanner, /rounded-xl|rounded-lg|rounded-md|rounded-\[12px\]|rounded-\[10px\]|rounded-\[8px\]/);
  assert.doesNotMatch(scanner, /tone = "purple"|tone\?: "purple"/);
  assert.doesNotMatch(scanner, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
