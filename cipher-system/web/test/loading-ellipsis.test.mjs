import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const spyglass = readFileSync(new URL("../src/components/panels/Spyglass.tsx", import.meta.url), "utf8");
const tape = readFileSync(new URL("../src/components/panels/FlowTape.tsx", import.meta.url), "utf8");

test("loading copy uses a typographic ellipsis", () => {
  assert.match(spyglass, /Scanning \$\{ticker\}…/);
  assert.doesNotMatch(spyglass, /Scanning \$\{ticker\}\.\.\./);
  assert.match(tape, /Loading flow prints for \$\{ticker\}…/);
  assert.match(spyglass, /overflow-x-auto rounded-\[10px\]/);
  assert.doesNotMatch(spyglass, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
