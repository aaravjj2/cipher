import { readFileSync } from "node:fs";
import { test, expect, type BrowserContext } from "@playwright/test";
import { SESSION_COOKIE, sessionSecretFor, signSession } from "../../app/auth.mjs";

function configValue(name: string): string {
  if (process.env[name]) return process.env[name] as string;
  try {
    for (const raw of readFileSync("/etc/cipher/cipher.env", "utf8").split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#") || !line.startsWith(`${name}=`)) continue;
      return line.slice(name.length + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
    }
  } catch { /* CI may not have the deployed environment file. */ }
  return "";
}

async function authenticate(context: BrowserContext) {
  const hash = configValue("CIPHER_APP_PASSWORD_HASH");
  test.skip(!hash, "deployed app password hash is unavailable");
  const secret = sessionSecretFor(hash, configValue("CIPHER_APP_SESSION_SECRET"));
  await context.addCookies([{ name: SESSION_COOKIE, value: signSession(secret), url: "http://127.0.0.1:8283", httpOnly: true, sameSite: "Lax" }]);
}

test("Settings exposes provider compatibility without credentials or execution authority", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Provider compatibility" })).toBeVisible();
  await expect(page.getByText(/ALPACA (OPRA \/ SIP|INDICATIVE \/ IEX|CUSTOM|UNCONFIGURED)/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Capture only", { exact: true })).toBeVisible();
  await expect(page.getByText("Unsupported", { exact: true })).toBeVisible();
  await expect(page.getByText(/credentials never leave the core service/)).toBeVisible();
});

test("authenticated desktop shell opens operator status with no execution surface", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await page.getByRole("button", { name: "Operator Status" }).click();
  // The first operator request performs read-only SQLite probes and can take a
  // few seconds on a cold disk; wait for the actual outcome, not an arbitrary
  // 5-second render race.
  await expect(page.getByRole("heading", { name: "Local operator status" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("No execution capability.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Restore readiness" })).toBeVisible();
  await page.screenshot({ path: "test-results/operator-status-desktop.png", fullPage: true });
});

test("mobile shell opens navigation and keeps the operator panel within viewport", async ({ page, context }) => {
  await authenticate(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Operator Status" }).click();
  await expect(page.getByRole("heading", { name: "Local operator status" })).toBeVisible({ timeout: 15_000 });
  const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
  expect(bodyWidth).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/operator-status-mobile.png", fullPage: true });
});

test("Morning Brief keeps priority order and paper boundary on mobile", async ({ page, context }) => {
  await authenticate(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("1 · Needs attention")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("2 · Active paper observations")).toBeVisible();
  await expect(page.getByText(/No backfill, broker connection, or execution authority/)).toBeVisible();
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/morning-brief-v4-mobile.png", fullPage: true });
});

test("Ticker Workbench tabs support keyboard navigation", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "ANALYZE" }).click();
  await page.getByRole("button", { name: "Ticker Workbench" }).click();
  const overview = page.getByRole("tab", { name: "Overview" });
  await overview.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Chart" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Agent" })).toHaveAttribute("aria-selected", "true");
});

test("scheduled Research Desk exposes intraday and weekly evidence without execution", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "Research Desk" }).click();
  await expect(page.getByRole("heading", { name: "Research Desk" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Rankings are research heuristics/)).toBeVisible();
  await expect(page.getByText(/cannot place orders/)).toBeVisible();
  await page.getByRole("button", { name: "weekly" }).click();
  await expect(page.getByRole("region", { name: "weekly research candidates" })).toBeVisible();
  await page.screenshot({ path: "test-results/research-desk-desktop.png", fullPage: true });
});

test("Paper Portfolios exposes constrained and counterfactual outcomes without option-PnL claims", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "REVIEW" }).click();
  await page.getByRole("button", { name: "Paper Portfolios" }).click();
  // This panel performs three independent read-only ledger snapshots on first
  // mount; allow the same cold-disk budget used by other data-heavy journeys.
  await expect(page.getByRole("heading", { name: "Paper Portfolios" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Skipped → target")).toBeVisible();
  await expect(page.getByText("Paper autopilot control plane")).toBeVisible();
  await expect(page.getByText("Comparable strategy cohorts")).toBeVisible();
  await expect(page.getByText("Liquidation equity", { exact: true })).toBeVisible();
  await expect(page.getByText("NO RANKING YET")).toBeVisible();
  await expect(page.getByText(/not hypothetical option fills or P&L/i)).toBeVisible();
  await expect(page.getByText(/EXECUTION CAPABILITY: FALSE/)).toBeVisible();
  await page.screenshot({ path: "test-results/paper-portfolios-v3-desktop.png", fullPage: true });
});

test("Setup Scanner uses presets, a rejection funnel, and compact expandable comparison", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "DISCOVER" }).click();
  await page.getByRole("button", { name: "Setup Scanner" }).click();
  await expect(page.getByRole("region", { name: "Scanner presets" })).toBeVisible();
  await expect(page.getByText(/confidence describes evidence coverage/)).toBeVisible();
  await page.getByRole("button", { name: "History" }).click();
  await page.locator("button").filter({ hasText: /5\/7 qualified/i }).first().click();
  await expect(page.getByRole("region", { name: "Scan funnel" })).toBeVisible();
  await expect(page.getByText(/Rejection funnel:/)).toBeVisible();
  const first = page.locator(".setup-scanner details").nth(1);
  await first.locator("summary").click();
  await expect(first.getByText(/OPRA cells/)).toBeVisible();
  await first.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(page.getByRole("region", { name: "Setup comparison tray" })).toContainText("1/3 selected");
  await expect(page.getByRole("region", { name: "Setup comparison tray" })).toContainText("Expected moveNot observed");
  await expect(page.getByRole("region", { name: "Setup comparison tray" })).toContainText("CatalystNot observed");
  await page.screenshot({ path: "test-results/setup-scanner-v2-desktop.png", fullPage: true });
});

test("Setup Scanner preset workflow remains within a mobile viewport", async ({ page, context }) => {
  await authenticate(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "DISCOVER" }).click();
  await page.getByRole("button", { name: "Setup Scanner" }).click();
  await expect(page.getByRole("region", { name: "Scanner presets" })).toBeVisible();
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/setup-scanner-v2-mobile.png", fullPage: true });
});

test("Night Vision provides bounded ranges, regime context, volume, and OHLC crosshair", async ({ page, context }) => {
  await authenticate(context);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "ANALYZE" }).click();
  await page.getByRole("button", { name: "Night Vision" }).click();
  await expect(page.getByRole("region", { name: "Night Vision regime summary" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "30 bars" }).click();
  const chart = page.getByRole("img", { name: /candlestick chart/ });
  await expect(chart).toBeVisible();
  await expect(chart.locator('[aria-label="Volume bars"]')).toBeAttached();
  await chart.hover({ position: { x: 500, y: 250 } });
  await expect(page.getByText(/O \d+\.\d{2}/)).toBeVisible();
  await page.screenshot({ path: "test-results/night-vision-v2-desktop.png", fullPage: true });
});

test("Night Vision chart and controls remain bounded on mobile", async ({ page, context }) => {
  await authenticate(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "ANALYZE" }).click();
  await page.getByRole("button", { name: "Night Vision" }).click();
  await expect(page.getByRole("img", { name: /candlestick chart/ })).toBeVisible({ timeout: 30_000 });
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/night-vision-v2-mobile.png", fullPage: true });
});

test("Backtest exposes locked costs and holdout protocol before running", async ({ page, context }) => {
  await authenticate(context);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "LABS" }).click();
  await page.getByRole("button", { name: "Backtest", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Signal backtest" })).toBeVisible();
  await page.getByText("Experiment protocol", { exact: true }).click();
  await expect(page.getByText(/Parameters are hashed before the run/)).toBeVisible();
  await expect(page.getByText(/Research only; places no orders/)).toBeVisible();
  await page.screenshot({ path: "test-results/backtest-v2-desktop.png", fullPage: true });
});

test("Options Terminal exposes honest IV-history coverage", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "ANALYZE" }).click();
  await page.getByRole("button", { name: "Options Terminal" }).click();
  await expect(page.getByRole("heading", { name: "Options Terminal" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/IV rank: unavailable · \d+\/20 sessions/)).toBeVisible();
  await page.screenshot({ path: "test-results/options-history-v2-desktop.png", fullPage: true });
});

test("Trader Journal progressively discloses exact option legs", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  await page.getByRole("button", { name: "REVIEW" }).click();
  await page.getByRole("button", { name: "Trader Journal" }).click();
  await expect(page.getByRole("heading", { name: "Trader journal" })).toBeVisible();
  await page.getByText("Exact option legs (optional)").click();
  await expect(page.getByText(/Captured marks are valuation evidence, not fills/)).toBeVisible();
  await page.screenshot({ path: "test-results/trader-journal-v2-desktop.png", fullPage: true });
});

test("daily workflow carries a ticker from research through validation and recording", async ({ page, context }) => {
  await authenticate(context);
  await page.goto("/");
  const workflow = page.getByRole("navigation", { name: "Daily research workflow" });
  await expect(workflow).toBeVisible();
  await workflow.getByRole("button", { name: "1 Research" }).click();
  await expect(page.getByRole("heading", { name: "Research Desk" })).toBeVisible({ timeout: 30_000 });
  const firstCandidate = page.getByRole("region", { name: "intraday research candidates" }).locator("article").first();
  await firstCandidate.locator("button").first().click();
  await firstCandidate.getByRole("button", { name: "Chart" }).click();
  await expect(page.getByRole("img", { name: /candlestick chart/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Options Terminal" }).click();
  await expect(page.getByRole("heading", { name: "Options Terminal" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Record thesis" }).click();
  await expect(page.getByRole("heading", { name: "Trader journal" })).toBeVisible();
});
