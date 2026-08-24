import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const scanner = readFileSync(new URL("../src/components/panels/SetupScanner.tsx", import.meta.url), "utf8");

test("a finished scan with no names is an empty job, not a failure or a filter miss", () => {
  assert.match(scanner, /Scan finished with no qualifying setups\. An empty job is a valid result, not a failed scan\./);
  assert.match(scanner, /hasResults && !scanning && rawResults\.length === 0/);
  assert.match(scanner, /isClusterView && rawResults\.length > 0 && filteredRawResults\.length === 0/);
  assert.match(scanner, /No clusters match the current filters/);
  assert.match(scanner, /!isRawView && rawResults\.length > 0/);
  assert.match(scanner, /confidence describes evidence coverage, not a predicted win rate/);
  assert.match(scanner, /it is not P\(profit\)/);
  assert.doesNotMatch(scanner, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
