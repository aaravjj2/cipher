import puppeteer from "puppeteer-core";

const browserURL = process.env.CHROME_BROWSER_URL || "http://127.0.0.1:64437";
const targetUrl = "https://www.accessobsidian.com/app#CI";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const browser = await puppeteer.connect({ browserURL });
const pages = await browser.pages();
const page = pages.find((candidate) => candidate.url().startsWith(targetUrl));

if (!page) {
  console.error(`No open tab found for ${targetUrl}`);
  console.error("Open tabs:");
  for (const candidate of pages) {
    console.error(`- ${candidate.url()}`);
  }
  await browser.disconnect();
  process.exit(1);
}

console.log(`Matched tab: ${page.url()}`);
await page.bringToFront();

try {
  await page.waitForNetworkIdle({ idleTime: 750, timeout: 10000 });
} catch {
  console.log("Network did not fully idle before timeout; continuing with current DOM.");
}

await page.waitForSelector("body", { timeout: 10000 });
await sleep(1000);

const candidate = await page.evaluate(() => {
  const selectors = [
    "main a[href]",
    "main button",
    "main [role='button']",
    "main [role='link']",
    "[data-testid*='card' i]",
    "[data-testid*='item' i]",
    "[class*='card' i]",
    "[class*='tile' i]",
    "a[href]",
    "button",
    "[role='button']",
    "[role='link']",
    "[onclick]",
  ];

  const isVisible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return (
      style &&
      style.visibility !== "hidden" &&
      style.display !== "none" &&
      Number(style.opacity || "1") > 0.05 &&
      rect.width >= 24 &&
      rect.height >= 24 &&
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth
    );
  };

  const badText = /^(menu|search|login|sign in|settings|profile|account|help|close|dismiss)$/i;
  const seen = new Set();

  for (const selector of selectors) {
    for (const element of document.querySelectorAll(selector)) {
      if (seen.has(element) || !isVisible(element)) continue;
      seen.add(element);

      const rect = element.getBoundingClientRect();
      const text = (element.innerText || element.getAttribute("aria-label") || element.title || "")
        .replace(/\s+/g, " ")
        .trim();
      if (badText.test(text)) continue;

      return {
        selector,
        text: text.slice(0, 160),
        ariaLabel: element.getAttribute("aria-label"),
        role: element.getAttribute("role"),
        href: element.href || element.getAttribute("href"),
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
        width: rect.width,
        height: rect.height,
      };
    }
  }

  return null;
});

if (!candidate) {
  console.error("No visible clickable candidate found.");
  await browser.disconnect();
  process.exit(2);
}

console.log("Click candidate:");
console.log(JSON.stringify(candidate, null, 2));

await page.mouse.click(candidate.x, candidate.y);
await sleep(1500);

console.log(`After click URL: ${page.url()}`);
await browser.disconnect();
