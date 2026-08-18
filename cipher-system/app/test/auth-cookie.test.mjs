import assert from "node:assert/strict";
import test from "node:test";
import { createAuthSessionStore } from "../auth_session.mjs";


test("auth cookie store uses an opaque identifier and keeps token material server-side", () => {
  let now = 1_000;
  const store = createAuthSessionStore({
    now: () => now,
    randomId: () => "opaque-session-id",
    inactivityMs: 500,
    absoluteMs: 5_000,
  });

  const cookie = store.create({ userId: "user-a", accessToken: "supabase-access-token" });
  assert.match(cookie, /^cipher_session=opaque-session-id;/);
  assert.doesNotMatch(cookie, /user-a|supabase-access-token/);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /Secure/);

  const context = store.get({ headers: { cookie: "cipher_session=opaque-session-id" } });
  assert.deepEqual(context, { userId: "user-a", accessToken: "supabase-access-token" });

  now += 501;
  assert.equal(store.get({ headers: { cookie: "cipher_session=opaque-session-id" } }), null);
});


test("auth cookie clearing expires the browser cookie and removes server memory", () => {
  const store = createAuthSessionStore({ randomId: () => "clear-me" });
  store.create({ userId: "user-a", accessToken: "token" });
  const cleared = store.clear({ headers: { cookie: "cipher_session=clear-me" } });
  assert.match(cleared, /^cipher_session=;/);
  assert.match(cleared, /Max-Age=0/);
  assert.equal(store.get({ headers: { cookie: "cipher_session=clear-me" } }), null);
});
