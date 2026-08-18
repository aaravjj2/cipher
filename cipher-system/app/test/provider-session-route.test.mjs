import assert from "node:assert/strict";
import { test } from "node:test";
import { createProviderSessionClient } from "../provider_session_client.mjs";


test("connect forwards credentials only to the loopback core and stores an opaque session", async () => {
  const calls = [];
  const client = createProviderSessionClient({
    coreUrl: "http://127.0.0.1:8282",
    internalToken: "internal-token",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ provider_session_id: "opaque-session", status: "connected" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await client.connect({
    userId: "user-a",
    accessToken: "supabase-token",
    key: "alpaca-key",
    secret: "alpaca-secret",
    optionsFeed: "opra",
    stockFeed: "sip",
  });

  assert.deepEqual(result, { provider_session_id: "opaque-session", status: "connected" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8282/internal/provider-session");
  assert.equal(calls[0].init.headers["x-cipher-internal-token"], "internal-token");
  assert.equal(calls[0].init.headers["x-cipher-user-id"], "user-a");
  assert.equal(calls[0].init.headers["x-cipher-access-token"], "supabase-token");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    action: "connect",
    key: "alpaca-key",
    secret: "alpaca-secret",
    options_feed: "opra",
    stock_feed: "sip",
  });

  client.remember("user-a", "opaque-session");
  assert.equal(client.sessionFor("user-a"), "opaque-session");
  client.clear("user-a");
  assert.equal(client.sessionFor("user-a"), null);
});


test("core bridge errors are returned without exposing credential material", async () => {
  const client = createProviderSessionClient({
    coreUrl: "http://127.0.0.1:8282",
    internalToken: "internal-token",
    fetchImpl: async () => new Response(JSON.stringify({ error: "provider unavailable" }), { status: 503 }),
  });

  await assert.rejects(
    client.connect({ userId: "user-a", accessToken: "token", key: "key", secret: "secret", optionsFeed: "opra", stockFeed: "sip" }),
    /provider unavailable/,
  );
});
