import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { join } from "node:path";
import test from "node:test";

import {
  createAuthGate,
  hashPassword,
  parseCookies,
  sessionSecretFor,
  signSession,
  verifyPassword,
  verifySession,
} from "../auth.mjs";


test("malformed cookie encoding is ignored instead of crashing the request", () => {
  assert.deepEqual(parseCookies("cipher_session=%E0%A4%A; safe=value"), { safe: "value" });
});


test("password hashes verify without storing plaintext", async () => {
  const hash = await hashPassword("correct horse", Buffer.alloc(16, 7));
  assert.match(hash, /^scrypt\$/);
  assert.equal(await verifyPassword("correct horse", hash), true);
  assert.equal(await verifyPassword("wrong", hash), false);
  assert.equal(hash.includes("correct horse"), false);
});


test("sessions reject tampering and expiry", () => {
  const secret = sessionSecretFor("stored-password-hash");
  const token = signSession(secret, { now: 1_000, ttlMs: 500 });
  assert.ok(verifySession(token, secret, { now: 1_499 }));
  assert.equal(verifySession(token, secret, { now: 1_500 }), null);
  assert.equal(verifySession(`${token}x`, secret, { now: 1_100 }), null);
});


test("the auth gate issues a secure HTTP-only session", async () => {
  const passwordHash = await hashPassword("one user", Buffer.alloc(16, 3));
  const gate = createAuthGate({ passwordHash, now: () => 50_000 });
  const login = await gate.login("one user", "client");
  assert.equal(login.ok, true);
  assert.match(login.cookie, /HttpOnly/);
  assert.match(login.cookie, /Secure/);
  assert.match(login.cookie, /SameSite=Lax/);
  assert.equal(gate.isAuthenticated({ headers: { cookie: login.cookie.split(";")[0] } }), true);
  assert.equal(gate.isAuthenticated({ headers: { cookie: "cipher_session=tampered" } }), false);
});


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


test("server exposes health but no market data without a session", async (t) => {
  let quoteRequests = 0;
  const core = createServer((req, res) => {
    if (req.url.startsWith("/api/quote")) quoteRequests += 1;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ path: req.url, read_only: true }));
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const appPort = await unusedPort();
  const passwordHash = await hashPassword("private terminal", Buffer.alloc(16, 9));
  const child = spawn(process.execPath, [join(import.meta.dirname, "..", "server.mjs")], {
    env: {
      ...process.env,
      PORT: String(appPort),
      CIPHER_CORE_URL: `http://127.0.0.1:${corePort}`,
      CIPHER_APP_PASSWORD_HASH: passwordHash,
      CIPHER_APP_INSECURE_COOKIES: "1",
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
  assert.equal(quoteRequests, 0);

  const loginPage = await fetch(`http://127.0.0.1:${appPort}/`);
  assert.equal(loginPage.status, 200);
  assert.match(loginPage.headers.get("content-security-policy"), /connect-src 'self'/);

  const health = await fetch(`http://127.0.0.1:${appPort}/api/health`);
  assert.equal(health.status, 200);

  const rejected = await fetch(`http://127.0.0.1:${appPort}/api/login`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ password: "wrong" }),
  });
  assert.equal(rejected.status, 401);

  const accepted = await fetch(`http://127.0.0.1:${appPort}/api/login`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ password: "private terminal" }),
  });
  assert.equal(accepted.status, 200);
  const cookie = accepted.headers.get("set-cookie").split(";")[0];

  const quote = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: { cookie },
  });
  assert.equal(quote.status, 200);
  assert.equal(quoteRequests, 1);

  const tampered = await fetch(`http://127.0.0.1:${appPort}/api/quote?ticker=SPY`, {
    headers: { cookie: `${cookie}x` },
  });
  assert.equal(tampered.status, 401);
  assert.equal(quoteRequests, 1);
});
