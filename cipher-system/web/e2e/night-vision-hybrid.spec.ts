import { expect, test, type Page } from "@playwright/test";

const hostedUrl = process.env.CIPHER_E2E_URL || "";

async function enterGuest(page: Page) {
  await page.goto(hostedUrl || "/", { waitUntil: "domcontentloaded" });
  const guestButton = page.getByRole("button", { name: "Continue as guest" });
  await expect(guestButton).toBeVisible();
  await guestButton.click();
  await expect(guestButton).toBeHidden({ timeout: 15_000 });
}

async function openAnalyze(page: Page, mobile: boolean, label: string) {
  if (mobile) {
    const openNavigation = page.getByRole("button", { name: "Open navigation" });
    if (await openNavigation.isVisible().catch(() => false)) await openNavigation.click();
  }
  const analyze = page.getByRole("button", { name: "ANALYZE", exact: true });
  if ((await analyze.getAttribute("aria-expanded")) !== "true") await analyze.click();
  await page.getByRole("button", { name: label, exact: true }).click();
}

for (const profile of [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "mobile", width: 390, height: 844, mobile: true },
]) {
  test(`guest Night Vision hybrid keeps page overflow at 0 on ${profile.name}`, async ({ page }) => {
    test.skip(!hostedUrl, "set CIPHER_E2E_URL for the hosted hybrid check");
    test.setTimeout(180_000);
    await page.setViewportSize({ width: profile.width, height: profile.height });
    await enterGuest(page);

    await openAnalyze(page, profile.mobile, "Night Vision");
    const nv = page.locator('[data-guest-panel="Night Vision"]').first();
    await expect(nv).toBeVisible({ timeout: 90_000 });
    const nvSource = await nv.getAttribute("data-guest-source");
    expect(["live", "loading", "demo"].includes(nvSource || "")).toBe(true);
    if (nvSource === "live") {
      await expect(page.getByRole("img", { name: /candlestick chart/i }).first()).toBeVisible();
      await expect(nv.getByText(/Live chart · not demo fallback/).first()).toBeVisible();
    }
    if (nvSource === "demo") {
      await expect(nv.getByText(/Illustrative/).first()).toBeVisible();
    }
    let overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);

    await openAnalyze(page, profile.mobile, "Ticker Workbench");
    await expect(page.locator('[data-guest-panel="Ticker Workbench"]').first()).toBeVisible({ timeout: 90_000 });
    await page.getByRole("tab", { name: "Chart" }).click();
    await expect(page.locator('[data-guest-panel="Night Vision"]').first()).toBeVisible({ timeout: 90_000 });
    overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}
