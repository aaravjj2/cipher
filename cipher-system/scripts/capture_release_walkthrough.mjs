import { mkdirSync, readFileSync } from "node:fs";
import { chromium } from "../web/node_modules/@playwright/test/index.mjs";
import { SESSION_COOKIE, sessionSecretFor, signSession } from "../app/auth.mjs";

const root = new URL("../", import.meta.url);
const output = new URL("../release-artifacts/", import.meta.url);
mkdirSync(output, { recursive: true });

function configValue(name) {
  if (process.env[name]) return process.env[name];
  try {
    for (const raw of readFileSync("/etc/cipher/cipher.env", "utf8").split(/\r?\n/)) {
      const line = raw.trim();
      if (line.startsWith(`${name}=`)) return line.slice(name.length + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
    }
  } catch { /* local capture can run against an unauthenticated dev server */ }
  return "";
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: new URL("./", output).pathname, size: { width: 1440, height: 1000 } },
});
const hash = configValue("CIPHER_APP_PASSWORD_HASH");
if (hash) {
  const secret = sessionSecretFor(hash, configValue("CIPHER_APP_SESSION_SECRET"));
  await context.addCookies([{ name: SESSION_COOKIE, value: signSession(secret), url: "http://127.0.0.1:8283", httpOnly: true, sameSite: "Lax" }]);
}
const page = await context.newPage();
await page.goto("http://127.0.0.1:8283/", { waitUntil: "domcontentloaded" });
await page.screenshot({ path: new URL("./01-home.png", output).pathname, fullPage: true });

await page.getByRole("button", { name: "ANALYZE" }).click();
await page.getByRole("button", { name: "Night Vision" }).click();
await page.getByRole("region", { name: "Night Vision regime summary" }).waitFor({ timeout: 30_000 }).catch(() => {});
await page.screenshot({ path: new URL("./02-night-vision.png", output).pathname, fullPage: true });
const evidence = page.getByRole("button", { name: /Evidence .*·/ }).first();
if (await evidence.count()) {
  await evidence.click();
  await page.getByRole("dialog", { name: "Night Vision evidence details" }).waitFor();
  await page.screenshot({ path: new URL("./03-evidence-timeline.png", output).pathname, fullPage: true });
}

await page.getByRole("button", { name: "REVIEW" }).click();
await page.getByRole("button", { name: "Paper Portfolios" }).click();
await page.getByRole("heading", { name: "Paper Portfolios" }).waitFor({ timeout: 30_000 }).catch(() => {});
await page.screenshot({ path: new URL("./04-paper-portfolios.png", output).pathname, fullPage: true });

await page.getByRole("button", { name: "DISCOVER" }).click();
await page.getByRole("button", { name: "Setup Scanner" }).click();
await page.getByRole("region", { name: "Scanner presets" }).waitFor({ timeout: 30_000 }).catch(() => {});
await page.screenshot({ path: new URL("./05-setup-scanner.png", output).pathname, fullPage: true });

const video = page.video();
await context.close();
await browser.close();
console.log(JSON.stringify({ output: output.pathname, video: video ? await video.path() : null }));
