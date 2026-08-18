import assert from "node:assert/strict";
import { test } from "node:test";
import { createSupabaseAuth } from "../supabase_auth.mjs";


test("validates a bearer token through Supabase Auth and returns the user context", async () => {
  const calls = [];
  const auth = createSupabaseAuth({
    supabaseUrl: "https://project.supabase.co",
    anonKey: "public-anon-key",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ id: "user-a", email: "a@example.com" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await auth.validateRequest({ headers: { authorization: "Bearer access-a" } });

  assert.deepEqual(result, { userId: "user-a", accessToken: "access-a" });
  assert.equal(calls[0].url, "https://project.supabase.co/auth/v1/user");
  assert.equal(calls[0].init.headers.authorization, "Bearer access-a");
  assert.equal(calls[0].init.headers.apikey, "public-anon-key");
});


test("rejects missing, malformed, and invalid bearer tokens", async () => {
  const auth = createSupabaseAuth({
    supabaseUrl: "https://project.supabase.co/",
    anonKey: "public-anon-key",
    fetchImpl: async () => new Response(JSON.stringify({ error: "invalid token" }), { status: 401 }),
  });

  assert.equal(await auth.validateRequest({ headers: {} }), null);
  assert.equal(await auth.validateRequest({ headers: { authorization: "Basic abc" } }), null);
  assert.equal(await auth.validateRequest({ headers: { authorization: "Bearer bad" } }), null);
});


test("does not expose bearer values in validation errors", async () => {
  const secretToken = "access-token-that-must-not-appear";
  const auth = createSupabaseAuth({
    supabaseUrl: "https://project.supabase.co",
    anonKey: "public-anon-key",
    fetchImpl: async () => { throw new Error(`provider failed for ${secretToken}`); },
  });

  const result = await auth.validateRequest({ headers: { authorization: `Bearer ${secretToken}` } });
  assert.equal(result, null);
});
