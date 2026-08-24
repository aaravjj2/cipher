import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const spyglass = readFileSync(new URL("../src/components/panels/Spyglass.tsx", import.meta.url), "utf8");

test("Spyglass Bio and Contract Search tabs are a keyboard tablist", () => {
  assert.match(spyglass, /role="tablist"/);
  assert.match(spyglass, /aria-label="Spyglass views"/);
  assert.match(spyglass, /role="tab"/);
  assert.match(spyglass, /aria-selected=\{active\}/);
  assert.match(spyglass, /tabIndex=\{active \|\| \(!onExplicitTab && tab === "bio"\) \? 0 : -1\}/);
  assert.match(spyglass, /ArrowRight/);
  assert.match(spyglass, /ArrowLeft/);
  assert.match(spyglass, /p\.side === "buy" \? "ASK"/);
  assert.match(spyglass, /The inference is stated, not buried/);
  assert.match(spyglass, /overflow-x-auto rounded-\[10px\]/);
  assert.match(spyglass, /min-w-\[560px\]/);
  assert.match(spyglass, /role="region" aria-label="Contract search trade tape scrollport"/);
  assert.match(spyglass, /role="region" aria-label="Options flow prints scrollport"/);
  assert.match(spyglass, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.doesNotMatch(spyglass, /submit_order|place_order|TradingClient|OrderClient/);
});
