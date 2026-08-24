import { expect, test, type Page } from "@playwright/test";

const hostedUrl = process.env.CIPHER_E2E_URL || "";

async function enterGuest(page: Page) {
  await page.goto(hostedUrl || "/", { waitUntil: "domcontentloaded" });
  const guestButton = page.getByRole("button", { name: "Continue as guest" });
  await expect(guestButton).toBeVisible();
  await guestButton.click();
  await expect(guestButton).toBeHidden({ timeout: 15_000 });
}

for (const profile of [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "mobile", width: 390, height: 844, mobile: true },
]) {
  test(`guest workbench Options keeps page overflow at 0 on ${profile.name}`, async ({ page }) => {
    test.skip(!hostedUrl, "set CIPHER_E2E_URL for the hosted overflow check");
    test.setTimeout(120_000);
    await page.setViewportSize({ width: profile.width, height: profile.height });
    await enterGuest(page);
    if (profile.mobile) {
      const openNavigation = page.getByRole("button", { name: "Open navigation" });
      if (await openNavigation.isVisible().catch(() => false)) await openNavigation.click();
    }
    const analyze = page.getByRole("button", { name: "ANALYZE", exact: true });
    if ((await analyze.getAttribute("aria-expanded")) !== "true") await analyze.click();
    await page.getByRole("button", { name: "Ticker Workbench", exact: true }).click();
    const panel = page.locator('[data-guest-panel="Ticker Workbench"]').first();
    await expect(panel).toBeVisible({ timeout: 90_000 });
    await page.getByRole("tab", { name: "Options" }).click();
    await expect(page.getByTestId("options-terminal")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("region", { name: "Option chain" })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}
