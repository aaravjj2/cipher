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


test("hosted login uses an opaque HttpOnly cookie session", async (t) => {
  const forwarded = [];
  const core = createServer((req, res) => {
    forwarded.push({
      path: req.url,
      accessToken: req.headers["x-cipher-access-token"],
      guest: req.headers["x-cipher-guest"],
    });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ read_only: true, path: req.url }));
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const auth = createServer((req, res) => {
    if (req.url !== "/auth/v1/user" || req.headers.authorization !== "Bearer temporary-token") {
      res.writeHead(401);
      res.end(JSON.stringify({ error: "invalid" }));
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ id: "user-a", email: "user@example.com" }));
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
      CIPHER_GUEST_MODE: "1",
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

  const origin = "https://cipher.vercel.app";
  const exchanged = await fetch(`http://127.0.0.1:${appPort}/auth/session`, {
    method: "POST",
    headers: { origin, authorization: "Bearer temporary-token" },
  });
  assert.equal(exchanged.status, 200);
  const setCookie = exchanged.headers.get("set-cookie");
  assert.match(setCookie, /cipher_session=/);
  assert.match(setCookie, /HttpOnly/);
  assert.match(setCookie, /Secure/);
  assert.doesNotMatch(setCookie, /temporary-token|user-a/);
  const cookie = setCookie.split(";", 1)[0];

  const session = await fetch(`http://127.0.0.1:${appPort}/auth/session`, {
    headers: { origin, cookie },
  });
  assert.equal(session.status, 200);
  assert.deepEqual((await session.json()).user, { id: "user-a" });

  const quote = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: { origin, cookie },
  });
  assert.equal(quote.status, 200);
  assert.deepEqual(forwarded, [{ path: "/api/quote?ticker=SPY", accessToken: "temporary-token", guest: undefined }]);

  const loggedOut = await fetch(`http://127.0.0.1:${appPort}/auth/session`, {
    method: "DELETE",
    headers: { origin, cookie },
  });
  assert.equal(loggedOut.status, 200);
  assert.match(loggedOut.headers.get("set-cookie"), /Max-Age=0/);

  const afterLogout = await fetch(`http://127.0.0.1:${appPort}/api/watchlists`, {
    headers: { origin, cookie },
  });
  assert.equal(afterLogout.status, 401);
});
