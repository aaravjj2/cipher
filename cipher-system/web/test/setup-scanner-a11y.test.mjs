import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const scanner = readFileSync(new URL("../src/components/panels/SetupScanner.tsx", import.meta.url), "utf8");

test("Setup Scanner CSV and history controls have accessible names", () => {
  assert.match(scanner, /aria-label="Download scan results as CSV"/);
  assert.match(scanner, /<DownloadIcon width=\{13\} height=\{13\} aria-hidden="true" \/>/);
  assert.match(scanner, /aria-label="Saved scan history"/);
  assert.match(scanner, /aria-label=\{`Load saved \$\{entry\.strategy\} \$\{entry\.mode\} scan`\}/);
  assert.match(scanner, /role="listbox"/);
  assert.match(scanner, /role="option"/);
  assert.match(scanner, /confidence describes evidence coverage, not a predicted win rate/);
  assert.doesNotMatch(scanner, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
