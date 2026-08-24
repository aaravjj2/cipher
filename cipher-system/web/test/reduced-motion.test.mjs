import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
const reduced = css.slice(css.indexOf("prefers-reduced-motion"));

test("reduced motion drops remaining transitions and loops without killing the skeleton delay", () => {
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(reduced, /transition-duration:\s*0\.01ms\s*!important/);
  assert.match(reduced, /transition-delay:\s*0s\s*!important/);
  assert.match(reduced, /\.animate-spin/);
  assert.match(reduced, /\.animate-pulse/);
  assert.match(reduced, /animation:\s*none\s*!important/);
  assert.match(reduced, /\.cipher-skeleton \{\s*animation: none;/);
  assert.doesNotMatch(reduced, /cipher-skeleton-region/);
  assert.match(css, /animation: cipher-skeleton-reveal 0\.25s step-end/);
  assert.doesNotMatch(css, /submit_order|place_order|TradingClient|OrderClient/);
});
