import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const matrix = readFileSync(new URL("../src/components/panels/StrikeMatrix.tsx", import.meta.url), "utf8");

test("Strike Matrix toolbar pills are a keyboard radiogroup", () => {
  assert.match(matrix, /role="radiogroup"/);
  assert.match(matrix, /ArrowRight/);
  assert.match(matrix, /ArrowLeft/);
  assert.match(matrix, /tabIndex=\{active \? 0 : -1\}/);
  assert.match(matrix, /role="radio"/);
  assert.match(matrix, /aria-checked=\{active\}/);
  assert.match(matrix, /ariaLabel="Matrix density"/);
  assert.match(matrix, /ariaLabel="Strike range"/);
  assert.match(matrix, /ariaLabel="Exposure metric"/);
  assert.match(matrix, /ariaLabel="Matrix mode"/);
  assert.match(matrix, /ariaLabel="Refresh matrix"/);
  assert.match(matrix, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(matrix, /role="table"/);
  assert.match(matrix, /value == null \? "unknown"/);
  assert.match(matrix, /overflow-auto/);
  assert.match(matrix, /data-guest-source=\{guestMode \? \(status === "ready" \? "live" : status === "error" \? "demo" : status\) : undefined\}/);
  assert.match(matrix, /<GuestShowcase panel="Strike Matrix"/);
  assert.match(matrix, />\s*Retry\s*</);
  assert.match(matrix, /Live matrix · not demo fallback/);
  assert.match(matrix, /Loading strike matrix for \$\{ticker\}/);
  assert.doesNotMatch(matrix, /submit_order|place_order|TradingClient|OrderClient/);
});
