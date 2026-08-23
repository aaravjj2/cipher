import { expect, test, type Page } from "@playwright/test";
import { GUEST_PANEL_CATALOG } from "../src/lib/guestCatalog";

const hostedUrl = process.env.CIPHER_E2E_URL || "";

function observe(page: Page) {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const privateRequests: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("request", (request) => {
    if (/\/(api\/operator-status|api\/settings|api\/holdings|api\/watchlists|api\/alerts)/.test(request.url())) {
      privateRequests.push(request.url());
    }
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
  });
  return { consoleErrors, pageErrors, privateRequests, failedResponses };
}

async function enterGuest(page: Page) {
  await page.goto(hostedUrl || "/", { waitUntil: "domcontentloaded" });
  const guestButton = page.getByRole("button", { name: "Continue as guest" });
  await expect(guestButton).toBeVisible();
  await guestButton.click();
  await expect(guestButton).toBeHidden({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "guest", exact: true })).toBeVisible();
  await expect(page.locator('[data-guest-panel="Autopilot"]')).toBeVisible();
}

async function openPanel(page: Page, label: string, section: string, mobile: boolean) {
  if (mobile) {
    const openNavigation = page.getByRole("button", { name: "Open navigation" });
    if (await openNavigation.isVisible().catch(() => false)) await openNavigation.click();
  }
  const sectionButton = page.getByRole("button", { name: section, exact: true });
  if ((await sectionButton.getAttribute("aria-expanded")) !== "true") await sectionButton.click();
  await page.getByRole("button", { name: label, exact: true }).click();
  if (mobile) await page.waitForTimeout(250);
  const panel = page.locator(`[data-guest-panel="${label}"]`).first();
  await expect(panel).toBeVisible({ timeout: 90_000 });
  await expect(panel).not.toBeEmpty();
  return panel;
}

for (const profile of [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "mobile", width: 390, height: 844, mobile: true },
]) {
  test(`complete guest catalog renders on ${profile.name}`, async ({ page }, testInfo) => {
    test.skip(!hostedUrl, "set CIPHER_E2E_URL for the hosted guest audit");
    test.setTimeout(300_000);
    await page.setViewportSize({ width: profile.width, height: profile.height });
    const observed = observe(page);
    await enterGuest(page);

    for (const definition of GUEST_PANEL_CATALOG) {
      const panel = await openPanel(page, definition.label, definition.section, profile.mobile);
      const source = await panel.getAttribute("data-guest-source");
      expect(["demo", "live", "loading", "error"].includes(source || "")).toBe(true);
      if (definition.mode === "demo" || definition.mode === "locked") {
        await expect(panel.getByText(/Illustrative/).first()).toBeVisible();
      }
    }

    expect(await page.getByRole("button", { name: "Operator Status" }).count()).toBe(0);
    expect(await page.getByRole("button", { name: "Settings", exact: true }).count()).toBe(0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    expect(observed.consoleErrors, observed.failedResponses.join("\n")).toEqual([]);
    expect(observed.pageErrors).toEqual([]);
    expect(observed.privateRequests).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`guest-${profile.name}-complete.png`), fullPage: true });
  });
}
