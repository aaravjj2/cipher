import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { join } from "node:path";
import test from "node:test";
import { hashPassword } from "../auth.mjs";

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
    if (req.url === "/internal/provider-session") {
      res.writeHead(503, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "provider offline" }));
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ read_only: true, path: req.url }));
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const auth = createServer((req, res) => {
    if (req.url === "/auth/v1/settings") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
      return;
    }
    if (req.url.startsWith("/rest/v1/account_access")) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end("[]");
      return;
    }
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
  const hostPassword = "correct horse battery staple";
  const passwordHash = await hashPassword(hostPassword);
  const child = spawn(process.execPath, [join(import.meta.dirname, "..", "server.mjs")], {
    env: {
      ...process.env,
      PORT: String(appPort),
      CIPHER_CORE_URL: `http://127.0.0.1:${corePort}`,
      CIPHER_HOSTED: "1",
      CIPHER_GUEST_MODE: "1",
      CIPHER_APP_AUTH: "on",
      CIPHER_APP_PASSWORD_HASH: passwordHash,
      ALPACA_ALGO_KEY: "test-key",
      ALPACA_ALGO_SECRET: "test-secret",
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
  const initial = await fetch(`http://127.0.0.1:${appPort}/auth/session`, { headers: { origin } });
  assert.equal(initial.status, 200);
  assert.deepEqual(await initial.json(), { authenticated: false });
  const authStatus = await fetch(`http://127.0.0.1:${appPort}/auth/status`, { headers: { origin } });
  assert.equal(authStatus.status, 200);
  assert.deepEqual(await authStatus.json(), { provider: "supabase", configured: true, reachable: true });
  assert.equal(authStatus.headers.get("access-control-allow-origin"), origin);
  const refusedHost = await fetch(`http://127.0.0.1:${appPort}/auth/operator`, {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify({ password: "wrong password" }),
  });
  assert.equal(refusedHost.status, 401);
  const hostLogin = await fetch(`http://127.0.0.1:${appPort}/auth/operator`, {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify({ password: hostPassword }),
  });
  assert.equal(hostLogin.status, 200);
  const hostPayload = await hostLogin.json();
  assert.equal(hostPayload.mode, "developer");
  assert.equal(hostPayload.capabilities.developerTools, true);
  assert.equal(hostPayload.capabilities.liveOrders, false);
  const hostCookie = hostLogin.headers.get("set-cookie").split(";", 1)[0];
  assert.equal(hostPayload.provider_connected, false);
  for (const path of ["/api/earnings-radar", "/api/autopilot-status", "/api/paper-portfolios", "/api/research-ranking"]) {
    const start = forwarded.length;
    const report = await fetch(`http://127.0.0.1:${appPort}${path}`, { headers: { origin, cookie: hostCookie } });
    assert.equal(report.status, 200);
    assert.deepEqual(forwarded.slice(start).map((row) => row.path), [path]);
  }
  const hostPrivate = await fetch(`http://127.0.0.1:${appPort}/api/watchlists`, {
    headers: { origin, cookie: hostCookie },
  });
  assert.equal(hostPrivate.status, 503);
  assert.equal(forwarded.at(-1).accessToken, undefined);
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
  const sessionPayload = await session.json();
  assert.deepEqual(sessionPayload.user, { id: "user-a", email: "user@example.com" });
  assert.equal(sessionPayload.mode, "member");
  assert.equal(sessionPayload.capabilities.liveOrders, false);

  const quote = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: { origin, cookie },
  });
  assert.equal(quote.status, 200);
  assert.deepEqual(forwarded.at(-1), { path: "/api/quote?ticker=SPY", accessToken: "temporary-token", guest: undefined });

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

  const guestLogin = await fetch(`http://127.0.0.1:${appPort}/auth/guest`, {
    method: "POST",
    headers: { origin },
  });
  assert.equal(guestLogin.status, 200);
  const guestPayload = await guestLogin.json();
  assert.equal(guestPayload.mode, "guest");
  assert.equal(guestPayload.authenticated, false);
  assert.equal(guestPayload.capabilities.savedWorkspace, false);
  const guestCookie = guestLogin.headers.get("set-cookie").split(";", 1)[0];

  const guestQuote = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: { origin, cookie: guestCookie },
  });
  assert.equal(guestQuote.status, 200);
  assert.equal(forwarded.at(-1).guest, "1");
  assert.equal(forwarded.at(-1).accessToken, undefined);

  const guestMatrix = await fetch(`http://127.0.0.1:${appPort}/api/matrix?symbol=SPY&expirations=12&depth=full`, {
    headers: { origin, cookie: guestCookie },
  });
  assert.equal(guestMatrix.status, 200);
  assert.equal(forwarded.at(-1).path, "/api/matrix?expirations=4&depth=0.06&ticker=SPY");

  const guestPrivate = await fetch(`http://127.0.0.1:${appPort}/api/watchlists`, {
    headers: { origin, cookie: guestCookie },
  });
  assert.equal(guestPrivate.status, 403);
  const guestUnknownSymbol = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=GME`, {
    headers: { origin, cookie: guestCookie },
  });
  assert.equal(guestUnknownSymbol.status, 403);
});
