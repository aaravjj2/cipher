import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const skew = readFileSync(new URL("../src/components/panels/SkewMap.tsx", import.meta.url), "utf8");
const showcase = readFileSync(new URL("../src/components/panels/GuestShowcase.tsx", import.meta.url), "utf8");

test("Skew Map stays PROVISIONAL below 20 stored sessions", () => {
  assert.match(skew, /stays PROVISIONAL until 20 stored sessions/);
  assert.match(skew, /point\.sessions < 20 \? "PROVISIONAL" : point\.quality/);
  assert.match(skew, /\{point\.sessions\}\/20 sessions/);
  assert.doesNotMatch(skew, /week-over-week|WoW|earnings join/);
  assert.match(showcase, /"Skew Map"[\s\S]*PROVISIONAL until 20 stored sessions/);
  assert.doesNotMatch(skew, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
