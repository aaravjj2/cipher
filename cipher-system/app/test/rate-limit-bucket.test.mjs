/**
 * The login limiter has to bucket per client, and behind the Tailscale Funnel it could not.
 *
 * Funnel proxies `https://…:8443` to `http://127.0.0.1:8283`, so `req.socket.remoteAddress`
 * is localhost for every internet client and they all shared one bucket. At 5 failures with a
 * 15-minute cap that is an availability hole rather than a hardening gap: anyone can hold the
 * real user out indefinitely with a few wrong guesses a minute.
 *
 * A probe through the real Funnel established which header is safe to key on. A spoofed
 * `X-Forwarded-For` arrived overwritten with the true client address, so Tailscale sets it;
 * a spoofed `X-Real-IP` passed through verbatim, so it is attacker-controlled. These tests
 * pin both halves — isolation via the trusted header, and no bucket-minting via the untrusted
 * one, which would let a single client skip rate limiting entirely.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { join } from "node:path";
import test from "node:test";

import { hashPassword } from "../auth.mjs";

const PASSWORD = "bucket isolation terminal";
// mirrors MAX_FAILS_BEFORE_LOCKOUT in auth.mjs. The Nth failure is itself refused with 429:
// recordFailure sets lockedUntil once `fails >= MAX`, and login returns that remaining time
// on the same call. So N-1 attempts are plain rejections and the Nth is already the lockout.
const MAX_FAILS = 5;
const PLAIN_REJECTIONS = MAX_FAILS - 1;

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

async function startApp(t) {
  const core = createServer((req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ read_only: true }));
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const appPort = await unusedPort();
  const passwordHash = await hashPassword(PASSWORD, Buffer.alloc(16, 11));
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
  return appPort;
}

const attempt = (port, password, headers = {}) =>
  fetch(`http://127.0.0.1:${port}/api/login`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", ...headers },
    body: new URLSearchParams({ password }),
  });

test("one client's lockout does not lock out another client", async (t) => {
  const port = await startApp(t);

  // Exhaust the allowance for a single forwarded client.
  for (let i = 0; i < PLAIN_REJECTIONS; i += 1) {
    const res = await attempt(port, "wrong", { "x-forwarded-for": "203.0.113.1" });
    assert.equal(res.status, 401, `failure ${i + 1} should be a plain rejection`);
  }
  const locked = await attempt(port, "wrong", { "x-forwarded-for": "203.0.113.1" });
  assert.equal(locked.status, 429, "the exhausted client must be locked out");
  assert.ok(Number(locked.headers.get("retry-after")) >= 1);

  // A different forwarded client is unaffected. Before keying on the forwarded address this
  // returned 429 too, because both requests arrived from localhost.
  const other = await attempt(port, "wrong", { "x-forwarded-for": "203.0.113.2" });
  assert.equal(other.status, 401, "a different client must not inherit the lockout");

  // And the real user can still log in from elsewhere while an attacker is locked out.
  const success = await attempt(port, PASSWORD, { "x-forwarded-for": "203.0.113.3" });
  assert.equal(success.status, 200);
  assert.match(success.headers.get("set-cookie") || "", /cipher_session=/);
});

test("x-real-ip cannot be used to mint fresh buckets", async (t) => {
  const port = await startApp(t);

  // Same forwarded client throughout; only the untrusted header varies. If x-real-ip were
  // consulted, each value would start a new allowance and rate limiting would be bypassable.
  for (let i = 0; i < PLAIN_REJECTIONS; i += 1) {
    const res = await attempt(port, "wrong", {
      "x-forwarded-for": "203.0.113.9",
      "x-real-ip": `198.51.100.${i}`,
    });
    assert.equal(res.status, 401);
  }
  const locked = await attempt(port, "wrong", {
    "x-forwarded-for": "203.0.113.9",
    "x-real-ip": "198.51.100.250",
  });
  assert.equal(locked.status, 429, "changing x-real-ip must not reset the allowance");
});

test("the last forwarded hop is used, so a client-supplied prefix cannot split buckets", async (t) => {
  const port = await startApp(t);

  // A client that prepends its own hops still lands in one bucket, because the trailing entry
  // is the one the proxy appends. Keying on the first entry would make this bypassable.
  for (let i = 0; i < PLAIN_REJECTIONS; i += 1) {
    const res = await attempt(port, "wrong", {
      "x-forwarded-for": `10.0.0.${i}, 203.0.113.77`,
    });
    assert.equal(res.status, 401);
  }
  const locked = await attempt(port, "wrong", {
    "x-forwarded-for": "10.0.0.250, 203.0.113.77",
  });
  assert.equal(locked.status, 429, "the trailing hop must determine the bucket");
});
