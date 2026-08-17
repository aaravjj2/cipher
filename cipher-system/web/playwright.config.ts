import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: 0,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: process.env.CIPHER_E2E_URL || "http://127.0.0.1:8283",
    headless: true,
    launchOptions: { executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium" },
    screenshot: "only-on-failure",
  },
});
