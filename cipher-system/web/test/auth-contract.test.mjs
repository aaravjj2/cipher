import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const read = (relativePath) => readFileSync(new URL(relativePath, root), "utf8");


test("browser auth modules exist and use only public Supabase variables", () => {
  assert.equal(existsSync(new URL("src/lib/supabase.ts", root)), true);
  assert.equal(existsSync(new URL("src/lib/auth.ts", root)), true);
  const source = `${read("src/lib/supabase.ts")}\n${read("src/lib/auth.ts")}`;
  assert.match(source, /NEXT_PUBLIC_SUPABASE_URL/);
  assert.match(source, /NEXT_PUBLIC_SUPABASE_ANON_KEY/);
  assert.doesNotMatch(source, /SUPABASE_SERVICE_ROLE_KEY/);
  assert.doesNotMatch(source, /ALPACA_(?:API_)?SECRET/);
});


test("hosted login exchanges the temporary Supabase token for a cookie session", () => {
  const auth = read("src/lib/auth.ts");
  const supabase = read("src/lib/supabase.ts");
  const api = read("src/lib/api.ts");
  assert.match(auth, /\/auth\/session/);
  assert.match(auth, /credentials: "include"/);
  assert.match(supabase, /persistSession: false/);
  assert.match(api, /credentials: "include"/);
});


test("auth UI exposes an explicit anonymous guest path without provider credentials", () => {
  const source = read("src/components/auth/AuthPanel.tsx");
  assert.match(source, /Continue as guest/);
  assert.match(source, /guest/i);
  assert.doesNotMatch(source, /ALPACA_(?:API_)?SECRET/);
});


test("auth UI has no provider credential persistence path", () => {
  assert.equal(existsSync(new URL("src/components/auth/AuthPanel.tsx", root)), true);
  const source = read("src/components/auth/AuthPanel.tsx");
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
  assert.doesNotMatch(source, /SUPABASE_SERVICE_ROLE_KEY|ALPACA_(?:API_)?SECRET/);
});


test("browser package declares the Supabase client", () => {
  const packageJson = JSON.parse(read("package.json"));
  assert.equal(typeof packageJson.dependencies?.["@supabase/supabase-js"], "string");
});


test("provider connection UI and API helpers are session-only", () => {
  assert.equal(existsSync(new URL("src/components/auth/ProviderConnectionPanel.tsx", root)), true);
  const provider = read("src/components/auth/ProviderConnectionPanel.tsx");
  const apiSource = `${read("src/lib/api.ts")}\n${read("src/lib/supabase.ts")}`;
  assert.match(provider, /session-only/i);
  assert.match(apiSource, /connectProviderSession/);
  assert.match(apiSource, /fetchProviderSessionStatus/);
  assert.match(apiSource, /disconnectProviderSession/);
  assert.match(apiSource, /NEXT_PUBLIC_CIPHER_API_ORIGIN/);
  assert.match(provider, /setKey\(""\)/);
  assert.match(provider, /setSecret\(""\)/);
  assert.doesNotMatch(provider, /localStorage|sessionStorage/);
  assert.doesNotMatch(provider, /SUPABASE_SERVICE_ROLE_KEY/);
});
