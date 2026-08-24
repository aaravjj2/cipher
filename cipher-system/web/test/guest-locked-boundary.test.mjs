import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const root = new URL("../src/", import.meta.url);
const catalog = readFileSync(new URL("lib/guestCatalog.ts", root), "utf8");
const showcase = readFileSync(new URL("components/panels/GuestShowcase.tsx", root), "utf8");
const host = readFileSync(new URL("components/PanelHost.tsx", root), "utf8");

const locked = [...catalog.matchAll(/\{ label: "([^"]+)", section: "[^"]+", mode: "locked" \}/g)].map((m) => m[1]);
const sentence =
  "This surface stays locked for guests: no writes, no saved private state, and no broker or LLM connection.";

test("locked guest panels share one boundary sentence", () => {
  assert.deepEqual(locked, ["My Watchlists", "Ask Cipher", "Holdings", "Alerts", "Trader Journal", "Chart Saves"]);
  assert.equal((showcase.match(new RegExp(sentence.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) || []).length, 1);
  assert.match(showcase, /guestPanelMode\(panel\) === "locked" \? GUEST_LOCKED_BOUNDARY/);
  assert.doesNotMatch(showcase, /locked:/);
  assert.doesNotMatch(showcase, /Saving and editing watchlists|Anonymous LLM requests|Alert writes and delivery|Journal writes are disabled|Chart writes and deletion|never connect to a broker/);
  assert.match(catalog, /\{ label: "My Watchlists", section: "DISCOVER", mode: "locked" \}/);
  assert.match(host, /guestModeType !== "hybrid"/);
});
