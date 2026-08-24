import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const header = readFileSync(new URL("../src/components/Header.tsx", import.meta.url), "utf8");

test("Header controls replace missing outlines with gold focus rings", () => {
  assert.match(header, /aria-label="Open navigation"/);
  assert.match(header, /role="combobox"/);
  assert.match(header, /id="ticker-suggestions"/);
  assert.match(header, /setUniverse\(\[\.\.\.GUEST_TICKERS\]\)/);
  assert.match(header, /Research only/);
  assert.match(header, /aria-label=\{`Workspace \$\{n\}`\}/);
  assert.match(header, /\+ Watchlist/);
  assert.match(header, /outline-none uppercase/);
  assert.match(header, /search relative[\s\S]*focus-within:ring-\[var\(--gold\)\]/);
  assert.match(header, /nav-toggle[\s\S]*focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(header, /Workspace \$\{n\}[\s\S]*focus-visible:ring-\[var\(--gold\)\]/);
  const buttons = header.match(/<button\b/g) ?? [];
  const rings = header.match(/focus-visible:ring-\[var\(--gold\)\]/g) ?? [];
  assert.ok(buttons.length >= 4, "header still has interactive buttons");
  assert.equal(rings.length, buttons.length, "every Header button has a gold focus-visible ring");
  assert.doesNotMatch(header, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
