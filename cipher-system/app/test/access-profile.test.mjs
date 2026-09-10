import assert from "node:assert/strict";
import test from "node:test";
import { createAccessProfileResolver } from "../access_profile.mjs";

test("developer access is granted only by trusted Supabase/operator fields", () => {
  const profiles = createAccessProfileResolver({ developerEmails: "aarav@example.com" });
  assert.equal(profiles.authenticated({ userId: "a", email: "aarav@example.com" }).mode, "developer");
  assert.equal(profiles.authenticated({ userId: "b", email: "other@example.com", appMetadata: { cipher_role: "developer" } }).mode, "developer");
  assert.equal(profiles.authenticated({ userId: "c", email: "other@example.com" }, { role: "developer" }).mode, "developer");
  assert.equal(profiles.authenticated({ userId: "d", email: "other@example.com", userMetadata: { cipher_role: "developer" } }).mode, "member");
});

test("all profiles preserve the no-live-order invariant and guest is constrained", () => {
  const profiles = createAccessProfileResolver();
  assert.equal(profiles.authenticated({ userId: "a", appMetadata: { cipher_role: "developer" } }).capabilities.liveOrders, false);
  const guest = profiles.guest();
  assert.equal(guest.capabilities.research, true);
  assert.equal(guest.capabilities.liveOrders, false);
  assert.equal(guest.capabilities.savedWorkspace, false);
  assert.equal(guest.capabilities.providerConnection, false);
});

test("local host profile has developer diagnostics without live-order authority", () => {
  const profile = createAccessProfileResolver().operator();
  assert.equal(profile.mode, "developer");
  assert.equal(profile.capabilities.developerTools, true);
  assert.equal(profile.capabilities.liveOrders, false);
  assert.equal(profile.settings.displayName, "Host");
});

test("developer settings are sanitized before reaching the browser", () => {
  const profiles = createAccessProfileResolver();
  const profile = profiles.authenticated(
    { userId: "a" },
    { role: "developer", developer_settings: { display_name: "Aarav", default_ticker: "nvda", default_panel: "Operator Status", secret: "no" } },
  );
  assert.deepEqual(profile.settings, { displayName: "Aarav", defaultTicker: "NVDA", defaultPanel: "Operator Status" });
});
