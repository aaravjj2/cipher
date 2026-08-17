import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const cache = readFileSync(new URL("../src/lib/requestCache.ts", import.meta.url), "utf8");
const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const sidebar = readFileSync(new URL("../src/components/Sidebar.tsx", import.meta.url), "utf8");
const scanner = readFileSync(new URL("../src/components/panels/SetupScanner.tsx", import.meta.url), "utf8");
const paper = readFileSync(new URL("../src/components/panels/PaperPortfolios.tsx", import.meta.url), "utf8");
const workbench = readFileSync(new URL("../src/components/panels/TickerWorkbench.tsx", import.meta.url), "utf8");
const morning = readFileSync(new URL("../src/components/panels/MorningBrief.tsx", import.meta.url), "utf8");
const home = readFileSync(new URL("../src/app/page.tsx", import.meta.url), "utf8");
const nightVision = readFileSync(new URL("../src/components/panels/NightVision.tsx", import.meta.url), "utf8");

test("all typed GET helpers pass through the shared request coordinator", () => {
  assert.match(api, /return coordinatedGet\(path/);
  assert.match(cache, /const inFlight = new Map/);
  assert.match(cache, /subscriber abort only detaches/i);
  assert.match(cache, /America\/New_York/);
});

test("mutations invalidate cached read models", () => {
  assert.match(api, /invalidateRequests\(\);/);
});

test("active package identity is Cipher and not the clone template", () => {
  assert.equal(pkg.name, "cipher-local-trader-terminal");
  assert.equal(pkg.private, true);
  assert.equal(pkg.license, "UNLICENSED");
  assert.doesNotMatch(JSON.stringify(pkg), /website-clone|JCodesMore|reverse-engineering/i);
});

test("sidebar progressively discloses labs while keeping the daily workflow visible", () => {
  for (const section of ["TODAY", "DISCOVER", "ANALYZE", "PLAN", "REVIEW", "LABS", "SYSTEM"]) {
    assert.match(sidebar, new RegExp(`label: "${section.replace(/[&]/g, "&")}"`));
  }
  assert.match(sidebar, /new Set\(\["TODAY", "SYSTEM"\]\)/);
  assert.match(sidebar, /aria-expanded=\{expanded\}/);
  assert.match(sidebar, /containsActive \|\| openSections\.has/);
  assert.doesNotMatch(sidebar, /CipherXBadge/);
  assert.match(sidebar, /label: "DISCOVER"[\s\S]*Setup Scanner/);
  assert.match(sidebar, /label: "ANALYZE"[\s\S]*Ticker Workbench[\s\S]*Night Vision[\s\S]*Options Terminal/);
  assert.match(sidebar, /label: "REVIEW"[\s\S]*Paper Portfolios[\s\S]*Trader Journal/);
});

test("ticker workbench unifies the daily analysis context without an order surface", () => {
  for (const view of ["Overview", "Chart", "Options", "Flow", "Company", "Agent"]) {
    assert.match(workbench, new RegExp(`"${view}"`));
  }
  for (const panel of ["NightVision", "OptionsTerminal", "Spyglass", "CompanyContext", "AskCipher"]) {
    assert.match(workbench, new RegExp(`<${panel}`));
  }
  assert.match(workbench, /research only/);
  assert.match(workbench, /role="tablist"/);
  assert.match(workbench, /role="tabpanel"/);
  assert.match(workbench, /ArrowRight/);
  assert.match(workbench, /ArrowLeft/);
  assert.match(workbench, /tabIndex=\{tab === item \? 0 : -1\}/);
  assert.doesNotMatch(workbench, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});

test("morning brief prioritizes integrity and prospective truth before market context", () => {
  const attention = morning.indexOf("1 · Needs attention");
  const observations = morning.indexOf("2 · Active paper observations");
  const setups = morning.indexOf("3 · Review-worthy setups");
  const market = morning.indexOf("4 · Broad market");
  const portfolios = morning.indexOf("5 · Six shadow portfolios");
  assert.ok(attention >= 0 && attention < observations);
  assert.ok(observations < setups && setups < market && market < portfolios);
  assert.match(morning, /No backfill, broker connection, or execution authority/);
  assert.match(home, /p-3 sm:p-6/);
});

test("scanner leads with jobs and compares quality-gated evidence compactly", () => {
  for (const preset of ["Intraday", "Weekly", "Momentum", "Mean reversion", "Index momentum", "Exposure zones"]) {
    assert.match(scanner, new RegExp(`label: "${preset}"`));
  }
  assert.match(scanner, /confidence describes evidence coverage, not a predicted win rate/);
  assert.match(scanner, /Rejection funnel:/);
  assert.match(scanner, /function ResultComparison/);
  assert.match(scanner, /<details key=\{raw\.ticker\}/);
  assert.match(scanner, /Select up to three candidates/);
  assert.match(scanner, /activeSelectedTickers\.length >= 3/);
  assert.match(scanner, /Expected move<\/dt><dd>Not observed/);
  assert.match(scanner, /Catalyst<\/dt><dd>Not observed/);
  assert.match(scanner, /raw\.evidence_snapshot\.snapshot_id\.slice\(0, 12\)/);
  assert.match(nightVision, /nightVision\.evidence_snapshot\.snapshot_id\.slice\(0, 12\)/);
  assert.match(nightVision, /Frozen scanner replay/);
  assert.match(nightVision, /fetchNightVisionReplay/);
  assert.match(scanner, /cipher:night-vision-replay/);
  assert.match(api, /export type EvidenceSnapshot/);
  assert.match(api, /api\/night-vision-replay/);
});

test("paper portfolios distinguish blocked opportunity paths from option P&L", () => {
  assert.match(paper, /Skipped → target/);
  assert.match(paper, /underlying path after every signal/);
  assert.match(paper, /not hypothetical option fills or P&amp;L/);
  assert.match(paper, /Recent signals and subsequent path/);
  assert.match(api, /underlying_path_counterfactual/);
});
