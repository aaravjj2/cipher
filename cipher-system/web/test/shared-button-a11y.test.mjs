import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const button = readFileSync(new URL("../src/components/ui/button.tsx", import.meta.url), "utf8");

test("shared Button uses Cipher gold focus instead of shadcn ring-ring", () => {
  assert.match(button, /focus-visible:ring-2 focus-visible:ring-\[var\(--gold\)\]/);
  assert.doesNotMatch(button, /focus-visible:ring-ring/);
  assert.match(button, /data-slot="button"/);
  assert.match(button, /disabled:opacity-50/);
  assert.match(button, /destructive:/);
  assert.doesNotMatch(button, /submit_order|place_order|TradingClient|OrderClient/);
});
