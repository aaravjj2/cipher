import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { join } from "node:path";
import test from "node:test";

async function listen(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return server.address().port;
}

async function unusedPort() {
  const server = createServer();
  const port = await listen(server);
  await new Promise((resolve) => server.close(resolve));
  return port;
}


test("hosted mode requires Supabase Auth and forwards trusted user context", async (t) => {
  const forwarded = [];
  const core = createServer((req, res) => {
    forwarded.push({
      path: req.url,
      userId: req.headers["x-cipher-user-id"],
      internalToken: req.headers["x-cipher-internal-token"],
      accessToken: req.headers["x-cipher-access-token"],
    });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ read_only: true, path: req.url }));
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const auth = createServer((req, res) => {
    if (req.url !== "/auth/v1/user" || req.headers.authorization !== "Bearer hosted-token") {
      res.writeHead(401);
      res.end(JSON.stringify({ error: "invalid" }));
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ id: "user-a" }));
  });
  const authPort = await listen(auth);
  t.after(() => new Promise((resolve) => auth.close(resolve)));

  const appPort = await unusedPort();
  const child = spawn(process.execPath, [join(import.meta.dirname, "..", "server.mjs")], {
    env: {
      ...process.env,
      PORT: String(appPort),
      CIPHER_CORE_URL: `http://127.0.0.1:${corePort}`,
      CIPHER_HOSTED: "1",
      CIPHER_APP_AUTH: "off",
      SUPABASE_URL: `http://127.0.0.1:${authPort}`,
      SUPABASE_ANON_KEY: "public-anon-key",
      CIPHER_INTERNAL_PROXY_TOKEN: "internal-only-token",
      CIPHER_HOSTED_ORIGINS: "https://cipher.vercel.app",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(() => {
    if (!child.killed) child.kill("SIGTERM");
  });
  await Promise.race([
    once(child.stdout, "data"),
    new Promise((_, reject) => setTimeout(() => reject(new Error("app server did not start")), 5_000)),
  ]);

  const unauthenticated = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`);
  assert.equal(unauthenticated.status, 401);
  assert.equal(forwarded.length, 0);

  const health = await fetch(`http://127.0.0.1:${appPort}/api/health`);
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { status: "ok" });

  const authenticated = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: {
      authorization: "Bearer hosted-token",
      origin: "https://cipher.vercel.app",
    },
  });
  assert.equal(authenticated.status, 200);
  assert.deepEqual(forwarded, [{
    path: "/api/quote?ticker=SPY",
    userId: "user-a",
    internalToken: "internal-only-token",
    accessToken: "hosted-token",
  }]);
  assert.equal(authenticated.headers.get("access-control-allow-origin"), "https://cipher.vercel.app");
});


test("hosted guest mode allows only bounded read-only market routes", async (t) => {
  const forwarded = [];
  const core = createServer((req, res) => {
    forwarded.push({
      path: req.url,
      userId: req.headers["x-cipher-user-id"],
      guest: req.headers["x-cipher-guest"],
      accessToken: req.headers["x-cipher-access-token"],
    });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ read_only: true, path: req.url }));
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const appPort = await unusedPort();
  const child = spawn(process.execPath, [join(import.meta.dirname, "..", "server.mjs")], {
    env: {
      ...process.env,
      PORT: String(appPort),
      CIPHER_CORE_URL: `http://127.0.0.1:${corePort}`,
      CIPHER_HOSTED: "1",
      CIPHER_APP_AUTH: "off",
      SUPABASE_URL: "http://127.0.0.1:1",
      SUPABASE_ANON_KEY: "public-anon-key",
      CIPHER_INTERNAL_PROXY_TOKEN: "internal-only-token",
      CIPHER_HOSTED_ORIGINS: "https://cipher.vercel.app",
      CIPHER_GUEST_MODE: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(() => {
    if (!child.killed) child.kill("SIGTERM");
  });
  await Promise.race([
    once(child.stdout, "data"),
    new Promise((_, reject) => setTimeout(() => reject(new Error("app server did not start")), 5_000)),
  ]);

  const guestQuote = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: { origin: "https://cipher.vercel.app" },
  });
  assert.equal(guestQuote.status, 200);
  assert.deepEqual(forwarded, [{
    path: "/api/quote?ticker=SPY",
    userId: "guest",
    guest: "1",
    accessToken: undefined,
  }]);

  const guestState = await fetch(`http://127.0.0.1:${appPort}/api/watchlists`, {
    headers: { origin: "https://cipher.vercel.app" },
  });
  assert.equal(guestState.status, 401);
  assert.equal(forwarded.length, 1);

  const guestProvider = await fetch(`http://127.0.0.1:${appPort}/api/provider-session`, {
    headers: { origin: "https://cipher.vercel.app" },
  });
  assert.equal(guestProvider.status, 401);
});
