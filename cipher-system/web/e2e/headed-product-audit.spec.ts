import { test, expect, type Page } from "@playwright/test";

const hostedUrl = process.env.CIPHER_E2E_URL || "";
const sessionCookie = process.env.CIPHER_E2E_SESSION_COOKIE || "";

function observeBrowser(page: Page) {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400 && /\/(api|auth)\//.test(response.url())) {
      failedResponses.push(`${response.status()} ${response.url()}`);
    }
  });
  return { consoleErrors, pageErrors, failedResponses };
}

test.describe("headed hosted product audit", () => {
  test.skip(!hostedUrl, "set CIPHER_E2E_URL for the live hosted audit");
  test.setTimeout(120_000);

  test("guest Strike Matrix renders real data without browser or network errors", async ({ page }, testInfo) => {
    await page.goto(hostedUrl);
    await page.getByRole("button", { name: "Continue as guest" }).click();
    const observed = observeBrowser(page);

    const matrixResponse = page.waitForResponse(
      (response) => response.url().includes("/api/matrix") && response.request().method() === "GET",
      { timeout: 90_000 },
    );
    await page.getByRole("button", { name: "Strike Matrix" }).click();
    expect((await matrixResponse).status()).toBe(200);
    await expect(page.getByRole("table", { name: /exposure by strike and expiration/i })).toBeVisible();
    await expect(page.getByText(/contracts ·/)).toBeVisible();
    await expect(page.getByText(/could not convert string to float/i)).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("guest-strike-matrix.png"), fullPage: true });

    await page.getByRole("button", { name: "Morning Brief" }).click();
    await expect(page.getByTestId("guest-showcase")).toBeVisible();
    await expect(page.getByText(/Illustrative judge demo/)).toBeVisible();
    await expect(page.getByText(/A two-minute plan before the bell/)).toBeVisible();

    await page.getByRole("button", { name: "ANALYZE" }).click();
    await page.getByRole("button", { name: "Options Terminal" }).click();
    await expect(page.getByText(/Structure before prediction/)).toBeVisible();

    await page.getByRole("button", { name: "PLAN" }).click();
    await page.getByRole("button", { name: "Holdings" }).click();
    await expect(page.getByText(/Guest holdings are illustrative/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Operator Status" })).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("guest-full-showcase.png"), fullPage: true });

    expect(observed.pageErrors).toEqual([]);
    expect(observed.consoleErrors).toEqual([]);
    expect(observed.failedResponses).toEqual([]);
  });

  test("developer GEX Replay and all three Trident matrices render", async ({ context, page }, testInfo) => {
    test.skip(!sessionCookie, "set CIPHER_E2E_SESSION_COOKIE for the authenticated audit");
    await context.addCookies([{
      name: "cipher_session", value: sessionCookie, url: hostedUrl,
      httpOnly: true, secure: true, sameSite: "None",
    }]);
    const observed = observeBrowser(page);
    await page.goto(hostedUrl);
    await expect(page.getByText(/Developer profile · operational panels/)).toBeVisible();
    await page.getByRole("button", { name: "LABS" }).click();

    const catalogResponse = page.waitForResponse((response) => response.url().includes("/api/gex-replay?action=catalog"));
    await page.getByRole("button", { name: "GEX Replay" }).click();
    expect((await catalogResponse).status()).toBe(200);
    await expect(page.getByText(/captured GEX replay|No captured GEX snapshots/)).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: testInfo.outputPath("developer-gex-replay.png"), fullPage: true });

    const matrixResponses = ["SPY", "QQQ", "IWM"].map((symbol) => page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === "/api/matrix" && (url.searchParams.get("symbol") || url.searchParams.get("ticker")) === symbol;
    }, { timeout: 90_000 }));
    await page.getByRole("button", { name: "Trident" }).click();
    expect((await Promise.all(matrixResponses)).map((response) => response.status())).toEqual([200, 200, 200]);
    for (const symbol of ["SPY", "QQQ", "IWM"]) {
      await expect(page.getByRole("table", { name: `${symbol} GEX exposure by strike` })).toBeVisible();
    }
    await page.screenshot({ path: testInfo.outputPath("developer-trident.png"), fullPage: true });
    expect(observed.pageErrors).toEqual([]);
    expect(observed.failedResponses).toEqual([]);
  });

  test("developer Earnings Radar, saved scans, and operator freshness are coherent", async ({ context, page }, testInfo) => {
    test.skip(!sessionCookie, "set CIPHER_E2E_SESSION_COOKIE for the authenticated audit");
    const observed = observeBrowser(page);
    await context.addCookies([{
      name: "cipher_session",
      value: sessionCookie,
      url: hostedUrl,
      httpOnly: true,
      secure: true,
      sameSite: "None",
    }]);
    await page.goto(hostedUrl);
    await expect(page.getByText(/Developer profile · operational panels/)).toBeVisible();

    await page.getByRole("button", { name: "Morning Brief" }).click();
    await expect(page.getByRole("heading", { name: "Morning Brief" })).toBeVisible();
    for (const section of ["Market now", "Paper status", "Setups to review"]) {
      await expect(page.getByRole("heading", { name: section })).toBeVisible();
    }
    await expect(page.getByText("AI Executive Market Synthesis")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("developer-simple-morning-brief.png"), fullPage: true });

    const radarResponse = page.waitForResponse((response) => response.url().includes("/api/earnings-radar"));
    await page.getByRole("button", { name: "Earnings Radar" }).click();
    expect((await radarResponse).status()).toBe(200);
    await expect(page.getByRole("heading", { name: "Earnings Radar" })).toBeVisible();
    await expect(page.getByText(/current ·|stale ·|unavailable/).first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("developer-earnings-radar.png"), fullPage: true });

    const statusResponse = page.waitForResponse((response) => response.url().includes("/api/operator-status"));
    await page.getByRole("button", { name: "Operator Status" }).click();
    expect((await statusResponse).status()).toBe(200);
    const savedScans = page.getByText(/saved scans/i, { exact: true }).locator("..");
    await expect(savedScans).toContainText("AVAILABLE");

    await page.getByRole("button", { name: "DISCOVER" }).click();
    await page.getByRole("button", { name: "Setup Scanner" }).click();
    const historyResponse = page.waitForResponse((response) => response.url().includes("/api/scan/history"));
    await page.getByRole("button", { name: "History", exact: true }).click();
    expect((await historyResponse).status()).toBe(200);
    await expect(page.getByText(/qualified|No saved scans yet/).first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("developer-saved-scans.png"), fullPage: true });

    expect(observed.pageErrors).toEqual([]);
    expect(observed.consoleErrors).toEqual([]);
    expect(observed.failedResponses).toEqual([]);
  });

  test("developer Holdings includes option state and Ask Cipher fits Groq's free-tier budget", async ({ context, page }, testInfo) => {
    test.skip(!sessionCookie, "set CIPHER_E2E_SESSION_COOKIE for the authenticated audit");
    await context.addCookies([{
      name: "cipher_session", value: sessionCookie, url: hostedUrl,
      httpOnly: true, secure: true, sameSite: "None",
    }]);
    const observed = observeBrowser(page);
    await page.goto(hostedUrl);

    await page.getByRole("button", { name: "PLAN" }).click();
    const holdingsResponse = page.waitForResponse((response) => response.url().includes("/api/holdings"));
    const riskResponse = page.waitForResponse((response) => response.url().includes("/api/portfolio-risk"));
    await page.getByRole("button", { name: "Holdings" }).click();
    expect((await holdingsResponse).status()).toBe(200);
    expect((await riskResponse).status()).toBe(200);
    await expect(page.getByText(/Options · auto-included/)).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("developer-holdings-options.png"), fullPage: true });

    await page.getByRole("button", { name: "ANALYZE" }).click();
    await page.getByRole("button", { name: "Ask Cipher" }).click();
    await page.getByPlaceholder(/Ask Cipher about/).fill("Use the quote tool and report the current NVDA quote in one short sentence.");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("ask-assistant-message").last()).toBeVisible({ timeout: 90_000 });
    // Empty input intentionally keeps the completed-state Ask button disabled.
    // Its label changing back from "Asking…" to "Ask" proves the SSE turn closed.
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeVisible();
    await expect(page.getByText(/HTTP 413|Request too large|TPM/i)).toHaveCount(0);
    await expect(page.getByText(/checking a live quote/i)).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("developer-ask-cipher-groq.png"), fullPage: true });

    expect(observed.pageErrors).toEqual([]);
    expect(observed.failedResponses).toEqual([]);
  });
});
