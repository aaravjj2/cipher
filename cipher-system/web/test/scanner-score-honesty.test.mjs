import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const scanner = readFileSync(new URL("../src/components/panels/SetupScanner.tsx", import.meta.url), "utf8");

test("scanner score is labelled structural, not P(profit)", () => {
  assert.match(scanner, /function StructuralScore/);
  assert.match(scanner, /it is not P\(profit\)/);
  assert.match(scanner, /structural \/100/);
  assert.match(scanner, /Not probability of profit/);
  assert.match(scanner, /<span>Structural<\/span>/);
  assert.match(scanner, /structural \{raw\.score\.toFixed\(1\)\}/);
  assert.match(scanner, /"Structural score"/);
  assert.match(scanner, /confidence describes evidence coverage, not a predicted win rate/);
  assert.doesNotMatch(scanner, /<span>Score<\/span>/);
  assert.doesNotMatch(scanner, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
