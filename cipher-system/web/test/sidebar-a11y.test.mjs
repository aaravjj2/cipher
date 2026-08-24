import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const sidebar = readFileSync(new URL("../src/components/Sidebar.tsx", import.meta.url), "utf8");

test("Sidebar section and panel buttons expose gold keyboard focus", () => {
  assert.match(sidebar, /GUEST_NAV_SECTIONS[\s\S]*GUEST_PANEL_LABELS\.has/);
  assert.match(sidebar, /filter\(\(section\) => section\.items\.length > 0\)/);
  assert.match(sidebar, /aria-label="Primary"/);
  assert.match(sidebar, /aria-label="Open navigation"/);
  assert.match(sidebar, /aria-label="Close navigation"/);
  assert.match(sidebar, /aria-label=\{collapsed \? "Expand sidebar" : "Collapse sidebar"\}/);
  assert.match(sidebar, /const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-\[var\(--gold\)\]"/);
  const starts = [];
  for (const match of sidebar.matchAll(/<button\b/g)) starts.push(match.index ?? 0);
  assert.equal(starts.length, 7);
  for (const start of starts) {
    const chunk = sidebar.slice(start, start + 700);
    assert.ok(
      chunk.includes("FOCUS") || chunk.includes("focus-visible:ring-[var(--gold)]"),
      "sidebar button missing gold focus-visible",
    );
  }
  assert.doesNotMatch(sidebar, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
