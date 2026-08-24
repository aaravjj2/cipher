import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const auth = readFileSync(new URL("../src/components/auth/AuthPanel.tsx", import.meta.url), "utf8");

test("Auth panel has labelled fields, password-manager autocomplete, and gold focus", () => {
  assert.match(auth, /htmlFor="cipher-auth-email"/);
  assert.match(auth, /id="cipher-auth-email"/);
  assert.match(auth, /name="email"/);
  assert.match(auth, /autoComplete="email"/);
  assert.match(auth, /htmlFor="cipher-auth-password"/);
  assert.match(auth, /id="cipher-auth-password"/);
  assert.match(auth, /name="password"/);
  assert.match(auth, /autoComplete=\{mode === "sign-in" \? "current-password" : "new-password"\}/);
  assert.match(auth, /Continue as guest/);
  assert.match(auth, /no broker-order authority/);
  assert.match(auth, /establishGuestSession/);
  const buttons = auth.match(/<button\b/g) ?? [];
  const rings = auth.match(/focus-visible:ring-\[var\(--gold\)\]/g) ?? [];
  assert.equal(buttons.length, 4);
  assert.ok(rings.length >= 6, "inputs and buttons share gold focus-visible rings");
  assert.doesNotMatch(auth, /localStorage|sessionStorage/);
  assert.doesNotMatch(auth, /SUPABASE_SERVICE_ROLE_KEY|ALPACA_(?:API_)?SECRET/);
  assert.doesNotMatch(auth, /submit_order|place_order|create_order|TradingClient|OrderClient/);
});
