import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// The panel already listed decision_date_count, but as one row among eighteen next to a
// min/max span, which reads as a continuous range. These tests pin the derived density
// line that states the distinction, and pin that it refuses to guess.
const SOURCE = readFileSync(
  join(import.meta.dirname, "..", "src", "components", "panels", "OptionsBacktest.tsx"),
  "utf8",
);

/** Mirror of samplingDensity in the panel, kept in step by the assertions below. */
function samplingDensity(coverage) {
  const dates = coverage.decision_date_count;
  const min = coverage.decision_date_min;
  const max = coverage.decision_date_max;
  if (typeof dates !== "number" || typeof min !== "string" || typeof max !== "string") return null;
  const spanDays = Math.round((Date.parse(max) - Date.parse(min)) / 86_400_000);
  if (!Number.isFinite(spanDays) || spanDays <= 0) return null;
  const sessions = (spanDays * 252) / 365;
  return { dates, spanDays, pctOfSessions: sessions > 0 ? (dates / sessions) * 100 : 0 };
}

test("the real leveraged-ETF wheel manifest reads as sparse, not as 18 months", () => {
  // Exactly the values /api/options-backtest returns for the leveraged_etf_wheel dataset.
  const d = samplingDensity({
    decision_date_count: 48,
    decision_date_min: "2025-01-02",
    decision_date_max: "2026-07-24",
  });
  assert.equal(d.dates, 48);
  assert.equal(d.spanDays, 568);
  // 48 dates over ~392 sessions is about 12%: sparse, and the panel now says so.
  assert.ok(d.pctOfSessions > 11 && d.pctOfSessions < 13, `got ${d.pctOfSessions}`);
});

test("a densely sampled dataset is not mislabelled as sparse", () => {
  const d = samplingDensity({
    decision_date_count: 250,
    decision_date_min: "2025-01-02",
    decision_date_max: "2026-01-02",
  });
  assert.ok(d.pctOfSessions > 95, `expected near-complete coverage, got ${d.pctOfSessions}`);
});

test("missing or nonsensical manifest fields produce nothing rather than a guess", () => {
  assert.equal(samplingDensity({}), null);
  assert.equal(samplingDensity({ decision_date_count: 48 }), null);
  assert.equal(
    samplingDensity({ decision_date_count: 48, decision_date_min: "2026-07-24", decision_date_max: "2026-07-24" }),
    null,
    "a zero-length span has no density to report",
  );
  assert.equal(
    samplingDensity({ decision_date_count: "48", decision_date_min: "2025-01-02", decision_date_max: "2026-07-24" }),
    null,
    "a string count must not be coerced",
  );
});

test("the panel renders the density line and says the range is a span", () => {
  assert.match(SOURCE, /Sampled, not continuous/);
  assert.match(SOURCE, /span of the\s+sample, not its density/);
  // It must be driven by the manifest, never hardcoded.
  assert.match(SOURCE, /samplingDensity\(dataset\.coverage\)/);
});

// --- Alerts panel copy ---------------------------------------------------------------
// The panel claimed "Evaluated every 30 seconds while Cipher is open", which stopped being
// true when cipher-market-alert.timer began evaluating rules server-side and pushing to
// Telegram. A panel that misdescribes when its own alerts fire is the difference between
// closing the tab confidently and missing a crossing, so the claim is pinned here.
const ALERTS = readFileSync(
  join(import.meta.dirname, "..", "src", "components", "panels", "Alerts.tsx"),
  "utf8",
);

test("the alerts panel no longer claims it only works while the tab is open", () => {
  assert.doesNotMatch(ALERTS, /every 30 seconds while Cipher is open/);
  assert.match(ALERTS, /whether or not this tab is open/);
  assert.match(ALERTS, /Telegram/);
});

test("the alerts panel states the crossing and staleness semantics", () => {
  // Both are load-bearing promises of the evaluator, so the UI has to say them.
  assert.match(ALERTS, /on the crossing, not repeatedly/);
  assert.match(ALERTS, /re-arms once it returns to clear/);
  assert.match(ALERTS, /older than 10 minutes/);
});

test("the browser-notification button is not presented as the delivery mechanism", () => {
  // "Notifications: default" read as though alerts depended on granting it; they do not.
  assert.match(ALERTS, /Browser notifications:/);
  assert.match(ALERTS, /Telegram delivery is independent/);
});

test("the alerts panel keeps its no-execution statement", () => {
  assert.match(ALERTS, /cannot place, stage, or transmit orders/);
});
