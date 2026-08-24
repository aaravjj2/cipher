import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const palette = readFileSync(new URL("../src/components/CommandPalette.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/app/page.tsx", import.meta.url), "utf8");

test("Command palette keeps keyboard selection visible and stays guest-hidden", () => {
  assert.match(palette, /NAV_SECTIONS\.flatMap/);
  assert.match(palette, /function rankTickers/);
  assert.match(palette, /MAX_TICKER_RESULTS = 8/);
  assert.match(palette, /shouldFilter=\{false\}/);
  assert.match(palette, /Jump to a panel or ticker…/);
  assert.match(palette, /export function useCommandPaletteShortcut/);
  assert.match(palette, /role="dialog"/);
  assert.match(palette, /aria-modal="true"/);
  assert.match(palette, /outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(palette, /data-\[selected=true\]:bg-\[var\(--nav-active\)\]/);
  assert.match(palette, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(page, /guestMode \? undefined : handlePaletteOpen/);
  assert.match(page, /\{!guestMode && <CommandPalette/);
  assert.doesNotMatch(palette, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
