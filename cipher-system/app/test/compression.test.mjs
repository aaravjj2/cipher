/**
 * The proxy's gzip path had no coverage, and it is the single biggest determinant of how
 * long a panel waits for data once Cipher is served over the Tailscale Funnel rather than
 * from localhost. A live SPY matrix measured 731 KB raw against 70 KB gzipped -- a 10.4x
 * reduction -- so a silent regression here would not break a page, it would just make every
 * grid load roughly ten times slower and look like a slow network.
 *
 * Two properties are worth locking rather than just the happy path:
 *
 *  * `Vary: accept-encoding` must be present whether or not the response was compressed.
 *    Without it a cache that saw one variant can serve it to a client that cannot decode it.
 *  * The compressed body must decode to exactly the bytes the core returned. The proxy has a
 *    `catch` that silently falls through to the uncompressed response, which is the right
 *    behaviour for a failed compression but would also hide a corrupted one.
 *
 * These use `node:http` rather than `fetch` on purpose: undici transparently decodes gzip,
 * so `content-encoding` is not reliably observable through it and the test would pass
 * whether or not compression happened.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import { once } from "node:events";
import { join } from "node:path";
import test from "node:test";
import { gunzipSync } from "node:zlib";

import { hashPassword } from "../auth.mjs";

// Mirrors GZIP_MIN_BYTES in server.mjs. Compressing a payload smaller than a single packet
// costs CPU and saves nothing, so the threshold existing is itself the behaviour under test.
const GZIP_MIN_BYTES = 1400;

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

/** Raw request so `content-encoding` survives to be asserted on. */
function rawGet(port, path, headers) {
  return new Promise((resolve, reject) => {
    const req = httpRequest(
      { host: "127.0.0.1", port, path, method: "GET", headers },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () =>
          resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }),
        );
      },
    );
    req.on("error", reject);
    req.end();
  });
}

test("the proxy compresses large grid payloads and leaves small ones alone", async (t) => {
  // A repetitive body, which is what a strike grid actually is: the same keys and the same
  // boolean flags repeated per cell. Compression ratio depends on that repetition, so a
  // random buffer would test the wrong thing.
  const bigPayload = {
    read_only: true,
    rows: Array.from({ length: 400 }, (_, index) => ({
      strike: 500 + index,
      cells: [{ net_gex: null, available: false, listed: true, oi_available: false }],
    })),
  };
  const bigBody = JSON.stringify(bigPayload);
  const smallBody = JSON.stringify({ status: "ok", read_only: true });
  assert.ok(bigBody.length > GZIP_MIN_BYTES, "the large fixture must exceed the threshold");
  assert.ok(smallBody.length < GZIP_MIN_BYTES, "the small fixture must sit under the threshold");

  const core = createServer((req, res) => {
    const body = req.url.startsWith("/api/matrix") ? bigBody : smallBody;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(body);
  });
  const corePort = await listen(core);
  t.after(() => new Promise((resolve) => core.close(resolve)));

  const appPort = await unusedPort();
  const passwordHash = await hashPassword("compression terminal", Buffer.alloc(16, 5));
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

  const login = await fetch(`http://127.0.0.1:${appPort}/api/login`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ password: "compression terminal" }),
  });
  assert.equal(login.status, 200);
  const cookie = login.headers.get("set-cookie").split(";")[0];

  const compressed = await rawGet(appPort, "/api/matrix?ticker=SPY", {
    cookie,
    "accept-encoding": "gzip",
  });
  assert.equal(compressed.status, 200);
  assert.equal(compressed.headers["content-encoding"], "gzip");
  assert.equal(compressed.headers.vary, "accept-encoding");
  // The bytes must survive the round trip: a silent fallback is acceptable, silent
  // corruption is not.
  assert.equal(gunzipSync(compressed.body).toString("utf8"), bigBody);
  assert.ok(
    compressed.body.length < bigBody.length / 2,
    `expected a real saving, got ${compressed.body.length} of ${bigBody.length} bytes`,
  );

  // A client that did not offer gzip must still get a body it can read.
  const plain = await rawGet(appPort, "/api/matrix?ticker=SPY", { cookie });
  assert.equal(plain.status, 200);
  assert.equal(plain.headers["content-encoding"], undefined);
  assert.equal(plain.headers.vary, "accept-encoding");
  assert.equal(plain.body.toString("utf8"), bigBody);

  // Below the threshold compression is skipped even when it is offered, but the response
  // still has to declare that it varies -- otherwise a cache can pin the wrong variant.
  const tiny = await rawGet(appPort, "/api/governance", { cookie, "accept-encoding": "gzip" });
  assert.equal(tiny.status, 200);
  assert.equal(tiny.headers["content-encoding"], undefined);
  assert.equal(tiny.headers.vary, "accept-encoding");
  assert.equal(tiny.body.toString("utf8"), smallBody);
});
