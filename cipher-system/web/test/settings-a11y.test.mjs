import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const settings = readFileSync(new URL("../src/components/panels/Settings.tsx", import.meta.url), "utf8");
const provider = readFileSync(new URL("../src/components/auth/ProviderConnectionPanel.tsx", import.meta.url), "utf8");

test("Settings form controls have labels and stay session-only", () => {
  assert.match(settings, /id="cipher-refresh-interval-label"/);
  assert.match(settings, /aria-labelledby="cipher-refresh-interval-label"/);
  assert.match(settings, /role="radiogroup"/);
  assert.match(settings, /role="radio"/);
  assert.match(settings, /aria-checked=\{active\}/);
  assert.match(settings, /ArrowRight/);
  assert.match(settings, /ArrowLeft/);
  assert.match(settings, /focus-visible:ring-\[var\(--gold\)\]/);
  assert.match(settings, /Cipher never places trades or generates orders/);
  assert.match(provider, /htmlFor="cipher-provider-key"/);
  assert.match(provider, /id="cipher-provider-key"/);
  assert.match(provider, /htmlFor="cipher-provider-secret"/);
  assert.match(provider, /id="cipher-provider-secret"/);
  assert.match(provider, /htmlFor="cipher-provider-options-feed"/);
  assert.match(provider, /htmlFor="cipher-provider-stock-feed"/);
  assert.match(provider, /autoComplete="off"/);
  assert.match(provider, /type="password"/);
  assert.match(provider, /setKey\(""\)/);
  assert.match(provider, /setSecret\(""\)/);
  assert.doesNotMatch(provider, /localStorage|sessionStorage/);
  assert.doesNotMatch(settings, /submit_order|place_order|TradingClient|OrderClient/);
  assert.doesNotMatch(provider, /submit_order|place_order|TradingClient|OrderClient/);
});
