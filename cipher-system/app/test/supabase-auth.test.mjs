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

  assert.deepEqual(result, {
    userId: "user-a",
    email: "a@example.com",
    appMetadata: {},
    databaseAccess: null,
    accessToken: "access-a",
  });
  assert.equal(calls[0].url, "https://project.supabase.co/auth/v1/user");
  assert.equal(calls[0].init.headers.authorization, "Bearer access-a");
  assert.equal(calls[0].init.headers.apikey, "public-anon-key");
});

test("reads operator-owned developer access through the user's RLS-scoped Supabase request", async () => {
  const auth = createSupabaseAuth({
    supabaseUrl: "https://project.supabase.co",
    anonKey: "public-anon-key",
    fetchImpl: async (url) => url.includes("/auth/v1/user")
      ? new Response(JSON.stringify({ id: "dev-a", email: "dev@example.com" }), { status: 200 })
      : new Response(JSON.stringify([{ role: "developer", developer_settings: { display_name: "Aarav" } }]), { status: 200 }),
  });
  const result = await auth.validateAccessToken("access-dev");
  assert.equal(result.databaseAccess.role, "developer");
  assert.equal(result.databaseAccess.developer_settings.display_name, "Aarav");
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

test("reports provider reachability without exposing deployment details", async () => {
  const calls = [];
  const auth = createSupabaseAuth({
    supabaseUrl: "https://project.supabase.co",
    anonKey: "public-anon-key",
    healthCacheTtlMs: 60_000,
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return new Response("{}", { status: 200 });
    },
  });

  assert.deepEqual(await auth.health(), { provider: "supabase", configured: true, reachable: true });
  assert.deepEqual(await auth.health(), { provider: "supabase", configured: true, reachable: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://project.supabase.co/auth/v1/settings");
  assert.ok(calls[0].init.signal instanceof AbortSignal);
});

test("reports an unreachable or unconfigured provider as unavailable", async () => {
  const unreachable = createSupabaseAuth({
    supabaseUrl: "https://missing.supabase.co",
    anonKey: "public-anon-key",
    fetchImpl: async () => { throw new Error("getaddrinfo ENOTFOUND secret-host"); },
  });
  const unconfigured = createSupabaseAuth();

  assert.deepEqual(await unreachable.health(), { provider: "supabase", configured: true, reachable: false });
  assert.deepEqual(await unconfigured.health(), { provider: "supabase", configured: false, reachable: false });
});
