import { test, expect } from "@playwright/test";

const email = process.env.CIPHER_E2E_EMAIL || "";
const password = process.env.CIPHER_E2E_PASSWORD || "";
const hostedUrl = process.env.CIPHER_E2E_URL || "";
const signupEmail = process.env.CIPHER_E2E_SIGNUP_EMAIL || "";
const signupPassword = process.env.CIPHER_E2E_SIGNUP_PASSWORD || "";

test.describe("hosted guest profile", () => {
  test.skip(!hostedUrl, "set CIPHER_E2E_URL for the hosted guest fixture");

  test("persists a constrained guest session and hides private surfaces", async ({ page }) => {
    await page.goto(hostedUrl);
    await page.getByRole("button", { name: "Continue as guest" }).click();
    await expect(page.getByText(/Guest demo · read-only cached market views/)).toBeVisible();
    await expect(page.getByRole("button", { name: "guest" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Ticker Workbench" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Settings" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Workspace", exact: true })).toHaveCount(0);

    await page.reload();
    await expect(page.getByText(/Guest demo · read-only cached market views/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Settings" })).toHaveCount(0);
  });
});

test.describe("hosted public signup", () => {
  test.skip(!hostedUrl || !signupEmail || !signupPassword, "set the hosted URL and disposable signup credentials");

  test("creates an immediately usable member session", async ({ page }) => {
    await page.goto(hostedUrl);
    await page.getByRole("button", { name: "Need an account? Sign up" }).click();
    await page.getByLabel("Email").fill(signupEmail);
    await page.getByLabel("Password").fill(signupPassword);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByRole("button", { name: "member" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Morning Brief" })).toBeVisible();

    const logout = page.waitForResponse((response) => response.url().includes("/auth/session") && response.request().method() === "DELETE");
    await page.getByRole("button", { name: "member" }).click();
    await expect((await logout).status()).toBe(200);
    await expect(page.getByTestId("auth-panel")).toBeVisible();
    await page.getByLabel("Email").fill(signupEmail);
    await page.getByLabel("Password").fill(signupPassword);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("button", { name: "member" })).toBeVisible();
  });
});

// This journey is deliberately opt-in: it uses a disposable operator-created
// Supabase user and never contains credentials in the repository or CI output.
test.describe("hosted auth and provider session", () => {
  test.skip(!hostedUrl || !email || !password, "set CIPHER_E2E_URL, CIPHER_E2E_EMAIL, and CIPHER_E2E_PASSWORD for the manual hosted fixture");

  test("signs in and clears Alpaca fields after the session-only connect flow", async ({ page }) => {
    await page.route("**/api/provider-session", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ json: { status: "disconnected", options_feed: null, stock_feed: null, expires_at: null, read_only: true } });
        return;
      }
      const body = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        json: body.action === "disconnect"
          ? { status: "disconnected", read_only: true }
          : { status: "connected", options_feed: body.options_feed, stock_feed: body.stock_feed, expires_at: null, read_only: true },
      });
    });

    await page.goto(hostedUrl);
    if (await page.getByTestId("auth-panel").isVisible().catch(() => false)) {
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(password);
      await page.getByRole("button", { name: "Sign in" }).click();
    }
    await page.getByRole("button", { name: "Settings" }).click();
    await expect(page.getByRole("heading", { name: "Alpaca session connection" })).toBeVisible();

    await page.getByLabel("Alpaca key").fill("fixture-key");
    await page.getByLabel("Alpaca secret").fill("fixture-secret");
    await page.getByRole("button", { name: "Connect for this session" }).click();
    await expect(page.getByRole("status")).toContainText("not persisted");
    await expect(page.getByLabel("Alpaca key")).toHaveValue("");
    await expect(page.getByLabel("Alpaca secret")).toHaveValue("");

    await page.getByRole("button", { name: "Disconnect" }).click();
    await expect(page.getByRole("status")).toContainText("session cleared");
  });
});
