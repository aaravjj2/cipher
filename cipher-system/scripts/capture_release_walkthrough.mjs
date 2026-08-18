import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { copyFile, rm } from "node:fs/promises";
import { chromium } from "../web/node_modules/@playwright/test/index.mjs";
import { SESSION_COOKIE, sessionSecretFor, signSession } from "../app/auth.mjs";

const output = new URL("../release-artifacts/", import.meta.url);
const detailedOutput = new URL("./detailed/", output);
mkdirSync(output, { recursive: true });
mkdirSync(detailedOutput, { recursive: true });
const demoUrl = process.env.CIPHER_DEMO_URL || "http://127.0.0.1:8283";
const outputPath = new URL("./cipher-release-walkthrough.webm", output).pathname;
const recordingDir = new URL("./.capture-video/", output);
mkdirSync(recordingDir, { recursive: true });

function configValue(name) {
  if (process.env[name]) return process.env[name];
  try {
    for (const raw of readFileSync("/etc/cipher/cipher.env", "utf8").split(/\r?\n/)) {
      const line = raw.trim();
      if (line.startsWith(`${name}=`)) return line.slice(name.length + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
    }
  } catch { /* local capture can run against a deliberately configured demo server */ }
  return "";
}

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: recordingDir.pathname, size: { width: 1440, height: 1000 } },
});

// The capture server is local-authenticated for this release artifact. This signs the
// session without putting a password in the page, screenshots, video, or repository.
const hash = configValue("CIPHER_APP_PASSWORD_HASH");
if (!hash) throw new Error("CIPHER_APP_PASSWORD_HASH is required for a signed-in capture");
const secret = sessionSecretFor(hash, configValue("CIPHER_APP_SESSION_SECRET"));
await context.addCookies([{
  name: SESSION_COOKIE,
  value: signSession(secret),
  url: new URL(demoUrl).origin,
  httpOnly: true,
  sameSite: "Lax",
}]);

const page = await context.newPage();
await page.goto(`${demoUrl}/`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);

const panelSections = new Map([
  ["Morning Brief", "TODAY"],
  ["Research Desk", "TODAY"],
  ["Setup Scanner", "DISCOVER"],
  ["My Watchlists", "DISCOVER"],
  ["News", "DISCOVER"],
  ["Ticker Workbench", "ANALYZE"],
  ["Night Vision", "ANALYZE"],
  ["Options Terminal", "ANALYZE"],
  ["Strike Matrix", "ANALYZE"],
  ["Spyglass", "ANALYZE"],
  ["Paper Portfolios", "REVIEW"],
  ["Settings", "SYSTEM"],
]);

async function selectPanel(label, waitMs = 4500) {
  const panel = page.getByRole("button", { name: label, exact: true });
  if (!(await panel.count())) {
    const section = panelSections.get(label);
    if (!section) throw new Error(`No sidebar section mapping for ${label}`);
    const sectionButton = page.getByRole("button", { name: section, exact: true }).first();
    if ((await sectionButton.getAttribute("aria-expanded")) !== "true") await sectionButton.click();
  }
  await page.getByRole("button", { name: label, exact: true }).first().click();
  await page.waitForTimeout(waitMs);
}

async function screenshot(name) {
  await page.screenshot({ path: new URL(`./${name}`, output).pathname, fullPage: true });
}

// Signed-in daily workflow: context, discovery, validation, structure, paper review.
await selectPanel("Morning Brief", 10_000);
await page.waitForTimeout(7000);
await screenshot("01-morning-brief.png");

await selectPanel("Research Desk", 12_000);
await page.waitForTimeout(7000);
await screenshot("02-research-desk.png");

await selectPanel("Setup Scanner", 15_000);
await page.waitForTimeout(7000);
await screenshot("03-setup-scanner.png");

await selectPanel("Night Vision", 15_000);
const evidence = page.getByRole("button", { name: /Evidence .*·/ }).first();
if (await evidence.count()) {
  await evidence.click();
  await page.getByRole("dialog", { name: "Night Vision evidence details" }).waitFor({ timeout: 10_000 }).catch(() => {});
}
await screenshot("04-night-vision-evidence.png");
await page.waitForTimeout(7000);

await selectPanel("Options Terminal", 15_000);
await screenshot("05-options-terminal.png");
await page.waitForTimeout(7000);

await selectPanel("Paper Portfolios", 12_000);
await screenshot("06-paper-portfolios.png");
await page.waitForTimeout(7000);

await selectPanel("Settings", 10_000);
await screenshot("07-settings-security.png");
await page.waitForTimeout(7000);

const video = page.video();
await context.close();
const videoPath = video ? await video.path() : null;
await browser.close();
if (!videoPath) throw new Error("Playwright did not produce a walkthrough video");
await copyFile(videoPath, outputPath);
await rm(videoPath, { force: true });
await rm(recordingDir, { recursive: true, force: true });

// The hero walkthrough stays concise. This second, unrecorded pass creates a complete
// signed-in visual index in a separate folder so reviewers can inspect every product
// surface without making the main demo video unwieldy.
const detailBrowser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
});
const detailContext = await detailBrowser.newContext({ viewport: { width: 1440, height: 1000 } });
await detailContext.addCookies([{
  name: SESSION_COOKIE,
  value: signSession(secret),
  url: new URL(demoUrl).origin,
  httpOnly: true,
  sameSite: "Lax",
}]);
const detailPage = await detailContext.newPage();
await detailPage.goto(`${demoUrl}/`, { waitUntil: "domcontentloaded" });
await detailPage.waitForTimeout(3000);

const allPanelSections = new Map([
  ["Morning Brief", "TODAY"], ["Research Desk", "TODAY"],
  ["Setup Scanner", "DISCOVER"], ["My Watchlists", "DISCOVER"], ["News", "DISCOVER"],
  ["Ticker Workbench", "ANALYZE"], ["Night Vision", "ANALYZE"], ["Options Terminal", "ANALYZE"],
  ["Strike Matrix", "ANALYZE"], ["Spyglass", "ANALYZE"], ["Company Context", "ANALYZE"], ["Ask Cipher", "ANALYZE"],
  ["Chart Workbench", "PLAN"], ["Portfolio Risk", "PLAN"], ["Holdings", "PLAN"], ["Alerts", "PLAN"],
  ["Paper Portfolios", "REVIEW"], ["Trader Journal", "REVIEW"], ["Standing", "REVIEW"], ["Strategies", "REVIEW"],
  ["Backtest", "LABS"], ["Options Backtest", "LABS"], ["GEX Replay", "LABS"], ["Trident", "LABS"], ["Chart Saves", "LABS"], ["Beliefs", "LABS"],
  ["Operator Status", "SYSTEM"], ["Settings", "SYSTEM"],
]);
const detailedFiles = [];
const detailedErrors = [];

async function selectDetailedPanel(label, waitMs = 3500) {
  const panel = detailPage.getByRole("button", { name: label, exact: true });
  if (!(await panel.count())) {
    const section = allPanelSections.get(label);
    if (!section) throw new Error(`No detailed section mapping for ${label}`);
    const sectionButton = detailPage.getByRole("button", { name: section, exact: true }).first();
    if ((await sectionButton.getAttribute("aria-expanded")) !== "true") await sectionButton.click();
  }
  await detailPage.getByRole("button", { name: label, exact: true }).first().click();
  await detailPage.waitForTimeout(waitMs);
}

async function detailShot(fileName) {
  await detailPage.screenshot({ path: new URL(`./${fileName}`, detailedOutput).pathname, fullPage: true });
  detailedFiles.push(fileName);
}

async function capturePanel(label, fileName, waitMs = 3500) {
  try {
    await selectDetailedPanel(label, waitMs);
    await detailShot(fileName);
  } catch (error) {
    detailedErrors.push(`${label}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

const detailedPanels = [
  ["Morning Brief", "01-morning-brief-full.png"], ["Research Desk", "02-research-desk-full.png"],
  ["Setup Scanner", "03-setup-scanner-controls.png"], ["My Watchlists", "04-watchlists.png"], ["News", "05-news.png"],
  ["Ticker Workbench", "06-ticker-workbench.png"], ["Night Vision", "07-night-vision-chart.png"],
  ["Options Terminal", "08-options-terminal-chain.png"], ["Strike Matrix", "09-strike-matrix.png"],
  ["Spyglass", "10-spyglass-flow.png"], ["Company Context", "11-company-context.png"], ["Ask Cipher", "12-ask-cipher.png"],
  ["Chart Workbench", "13-chart-workbench.png"], ["Portfolio Risk", "14-portfolio-risk.png"], ["Holdings", "15-holdings.png"],
  ["Alerts", "16-alerts.png"], ["Paper Portfolios", "17-paper-portfolios-overview.png"], ["Trader Journal", "18-trader-journal.png"],
  ["Standing", "19-standing.png"], ["Strategies", "20-strategies.png"], ["Backtest", "21-backtest.png"],
  ["Options Backtest", "22-options-backtest.png"], ["GEX Replay", "23-gex-replay.png"], ["Trident", "24-trident.png"],
  ["Chart Saves", "25-chart-saves.png"], ["Beliefs", "26-beliefs.png"], ["Operator Status", "27-operator-status.png"], ["Settings", "28-settings-security.png"],
];
for (const [label, fileName] of detailedPanels) await capturePanel(label, fileName);

// Expanded states expose the evidence and structure details that are easy to miss in a
// single overview frame. Each state is best-effort because a provider can legitimately
// return an unavailable/empty state during a cold capture.
try {
  await selectDetailedPanel("Setup Scanner", 5000);
  const firstResult = detailPage.locator("details").first();
  if (await firstResult.count()) {
    await firstResult.locator("summary").click();
    await detailPage.waitForTimeout(1000);
    await detailShot("29-setup-scanner-expanded-result.png");
  }
} catch (error) { detailedErrors.push(`Setup Scanner expanded: ${error instanceof Error ? error.message : String(error)}`); }

try {
  await selectDetailedPanel("Night Vision", 5000);
  const evidenceButton = detailPage.getByRole("button", { name: /^Evidence / }).first();
  if (await evidenceButton.count()) {
    await evidenceButton.click();
    await detailPage.waitForTimeout(800);
    await detailShot("30-night-vision-evidence-drawer.png");
  } else {
    await detailShot("30-night-vision-evidence-unavailable.png");
  }
} catch (error) { detailedErrors.push(`Night Vision evidence: ${error instanceof Error ? error.message : String(error)}`); }

try {
  await selectDetailedPanel("Options Terminal", 5000);
  const vertical = detailPage.getByRole("button", { name: "Bull call vertical", exact: true });
  if (await vertical.count()) {
    await vertical.click();
    await detailPage.waitForTimeout(2500);
    await detailShot("31-options-terminal-defined-risk-structure.png");
  }
} catch (error) { detailedErrors.push(`Options structure: ${error instanceof Error ? error.message : String(error)}`); }

try {
  await selectDetailedPanel("Spyglass", 5000);
  const bio = detailPage.getByRole("button", { name: "Bio", exact: true });
  if (await bio.count()) {
    await bio.click();
    await detailPage.waitForTimeout(2500);
    await detailShot("32-spyglass-bio.png");
  }
  const contractSearch = detailPage.getByRole("button", { name: "Contract Search", exact: true });
  if (await contractSearch.count()) {
    await contractSearch.click();
    await detailPage.waitForTimeout(2500);
    await detailShot("33-spyglass-contract-search.png");
  }
} catch (error) { detailedErrors.push(`Spyglass subviews: ${error instanceof Error ? error.message : String(error)}`); }

try {
  await selectDetailedPanel("Paper Portfolios", 5000);
  await detailPage.locator("details").evaluateAll((nodes) => nodes.forEach((node) => { node.open = true; }));
  await detailPage.waitForTimeout(800);
  await detailShot("34-paper-portfolios-expanded-tables.png");
} catch (error) { detailedErrors.push(`Paper Portfolios expanded: ${error instanceof Error ? error.message : String(error)}`); }

const visualLink = (label, fileName) => detailedFiles.includes(fileName)
  ? `- [${label}](./${fileName})`
  : `- ${label} — unavailable in this capture`;
const indexLines = [
  "# Cipher detailed signed-in visual index",
  "",
  "These screenshots were captured from the authenticated local release workflow. They contain no passwords, Supabase tokens, Alpaca keys, or service secrets.",
  "",
  "## Main product surfaces",
  ...detailedPanels.map(([label, fileName]) => visualLink(label, fileName)),
  "",
  "## Expanded feature states",
  visualLink("Setup Scanner expanded result", "29-setup-scanner-expanded-result.png"),
  visualLink("Night Vision evidence drawer", "30-night-vision-evidence-drawer.png"),
  visualLink("Night Vision evidence-unavailable state", "30-night-vision-evidence-unavailable.png"),
  visualLink("Options Terminal defined-risk structure", "31-options-terminal-defined-risk-structure.png"),
  visualLink("Spyglass Bio", "32-spyglass-bio.png"),
  visualLink("Spyglass Contract Search", "33-spyglass-contract-search.png"),
  visualLink("Paper Portfolios expanded tables", "34-paper-portfolios-expanded-tables.png"),
  "",
  `Captured files: ${detailedFiles.length}`,
  `Capture warnings: ${detailedErrors.length}`,
];
if (detailedErrors.length) indexLines.push("", "## Capture warnings", ...detailedErrors.map((warning) => `- ${warning}`));
writeFileSync(new URL("./README.md", detailedOutput), `${indexLines.join("\n")}\n`);
await detailContext.close();
await detailBrowser.close();

console.log(JSON.stringify({
  output: output.pathname,
  video: outputPath,
  screenshots: [
    "01-morning-brief.png",
    "02-research-desk.png",
    "03-setup-scanner.png",
    "04-night-vision-evidence.png",
    "05-options-terminal.png",
    "06-paper-portfolios.png",
    "07-settings-security.png",
  ],
  detailed: { directory: detailedOutput.pathname, files: detailedFiles, warnings: detailedErrors },
}));
