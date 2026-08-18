/** Browser-facing application server. Credentials remain in the local core service. */
import { createServer } from "node:http";
import { gzip as gzipCb } from "node:zlib";
import { promisify } from "node:util";
import { chmod, readFile } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createAuthGate } from "./auth.mjs";
import { createSupabaseAuth } from "./supabase_auth.mjs";
import { createProviderSessionClient } from "./provider_session_client.mjs";
import { createAuthSessionStore } from "./auth_session.mjs";
import { createScannerIngestHandler } from "./scanner_ingest.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 8283);
const coreUrl = process.env.CIPHER_CORE_URL || "http://127.0.0.1:8282";
const scannerIngestTokenFile = resolve(
  process.env.CIPHER_SCANNER_INGEST_TOKEN_FILE || join(root, ".scanner-ingest-token"),
);
const scannerIngestToken = (
  process.env.CIPHER_SCANNER_INGEST_TOKEN
  || await readFile(scannerIngestTokenFile, "utf8").catch(() => "")
).trim();
if (!process.env.CIPHER_SCANNER_INGEST_TOKEN && scannerIngestToken) {
  await chmod(scannerIngestTokenFile, 0o600).catch(() => {});
}
const scannerIngest = createScannerIngestHandler({
  dataDir: resolve(
    process.env.CIPHER_SCANNER_INGEST_DIR || join(root, "..", "data", "browser_ingest"),
  ),
  ingestToken: scannerIngestToken,
  forwardUrl: process.env.CIPHER_PAPER_EXECUTOR_URL || "",
});
const authGate = createAuthGate();
const hostedMode = process.env.CIPHER_HOSTED === "1";
const internalProxyToken = String(process.env.CIPHER_INTERNAL_PROXY_TOKEN || "");
const hostedOrigins = new Set(
  String(process.env.CIPHER_HOSTED_ORIGINS || "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
);
const guestMode = process.env.CIPHER_GUEST_MODE === "1";
const guestMarketRoutes = new Set([
  "/api/quote",
  "/api/bars",
  "/api/options-chain",
  "/api/matrix",
  "/api/heatmap",
  "/api/night-vision",
  "/api/provider-capabilities",
]);
const supabaseAuth = createSupabaseAuth({
  supabaseUrl: process.env.SUPABASE_URL,
  anonKey: process.env.SUPABASE_ANON_KEY,
});
const authSessions = createAuthSessionStore({
  inactivityMs: Number(process.env.CIPHER_AUTH_SESSION_INACTIVITY_MS || 30 * 60 * 1000),
  absoluteMs: Number(process.env.CIPHER_AUTH_SESSION_ABSOLUTE_MS || 12 * 60 * 60 * 1000),
});
const providerSessionClient = createProviderSessionClient({
  coreUrl,
  internalToken: internalProxyToken,
});
if (hostedMode && !internalProxyToken) {
  throw new Error("Hosted mode requires CIPHER_INTERNAL_PROXY_TOKEN for the internal core hop.");
}
async function validateHostedRequest(req) {
  const cookieSession = authSessions.get(req);
  if (cookieSession) return cookieSession;
  return supabaseAuth.validateRequest(req);
}

if (authGate.enabled && !authGate.configured && !hostedMode) {
  throw new Error(
    "Cipher authentication is enabled but CIPHER_APP_PASSWORD_HASH is not configured. "
    + "Set a hash or explicitly set CIPHER_APP_AUTH=off for local development.",
  );
}
const loginPagePath = join(root, "login.html");
const accessObsidianLoggerPath = resolve(
  join(root, "..", "scripts", "accessobsidian_browser_logger.js"),
);
const mime = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".txt": "text/plain; charset=utf-8",
};
const gzip = promisify(gzipCb);
// Below this, framing and CPU cost more than the bytes saved.
const GZIP_MIN_BYTES = 1400;

const corsHeaders = (origin) => {
  const value = String(origin || "");
  if (!hostedMode || !hostedOrigins.has(value)) return {};
  return {
    "access-control-allow-origin": value,
    "access-control-allow-credentials": "true",
    vary: "Origin",
  };
};

const sendJson = (res, status, body, headers = {}) => {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    ...headers,
  });
  res.end(JSON.stringify(body));
};

const readBoundedBody = async (req, limit = 8192) => {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new RangeError("request body too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
};

/**
 * Rate-limit bucket for a request.
 *
 * `req.socket.remoteAddress` alone is wrong behind the Funnel. Tailscale proxies
 * `https://…:8443` to `http://127.0.0.1:8283`, so the connection node sees always originates
 * on localhost and *every* internet client shares one bucket. With a 5-failure threshold and
 * a 15-minute cap that is an availability hole, not a hardening gap: anyone on the internet
 * can hold the real user out indefinitely with a few wrong guesses a minute, and the per-IP
 * isolation the limiter was written for never applies.
 *
 * `x-forwarded-for` is trustworthy *here specifically* because Tailscale sets it: a probe
 * through the Funnel with a spoofed `X-Forwarded-For: 203.0.113.99` arrived as the real
 * client address, overwritten. The last entry is taken rather than the first, which is
 * correct whether the proxy overwrites the header or appends to it — the first entry is the
 * one a client can choose.
 *
 * `x-real-ip` is deliberately not consulted. The same probe showed a spoofed
 * `X-Real-IP: 198.51.100.7` passing through verbatim, so it is attacker-controlled; keying
 * on it would let one client mint unlimited buckets and skip rate limiting entirely.
 */
const clientKey = (req) => {
  const forwarded = String(req.headers?.["x-forwarded-for"] || "");
  if (forwarded) {
    const hops = forwarded.split(",").map((hop) => hop.trim()).filter(Boolean);
    if (hops.length) return hops[hops.length - 1];
  }
  return String(req.socket?.remoteAddress || "shared-client");
};

const guestRateBuckets = new Map();
const GUEST_RATE_WINDOW_MS = 60_000;
const GUEST_RATE_MAX = 60;

function guestRateAllowed(req) {
  const key = clientKey(req);
  const now = Date.now();
  const current = guestRateBuckets.get(key);
  if (!current || current.expiresAt <= now) {
    guestRateBuckets.set(key, { count: 1, expiresAt: now + GUEST_RATE_WINDOW_MS });
    return true;
  }
  if (current.count >= GUEST_RATE_MAX) return false;
  current.count += 1;
  return true;
}

function guestAccessAllowed(req, url) {
  if (!hostedMode || !guestMode) return false;
  if ((req.method || "GET").toUpperCase() !== "GET") return false;
  if (!guestMarketRoutes.has(url.pathname)) return false;
  if (!hostedOrigins.has(String(req.headers.origin || ""))) return false;
  return guestRateAllowed(req);
}

async function sendLoginPage(res, status = 200) {
  const page = await readFile(loginPagePath);
  res.writeHead(status, {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  });
  res.end(page);
}

const routes = {
  "/api/health": "/health",
  "/api/quote": "/api/quote",
  "/api/governance": "/api/governance",
  "/api/standing": "/api/standing",
  "/api/holdings": "/api/holdings",
  "/api/news": "/api/news",
  "/api/workspace-layouts": "/api/workspace-layouts",
  "/api/chart-saves": "/api/chart-saves",
  "/api/standing-notes": "/api/standing-notes",
  "/api/ask": "/api/ask",
  "/api/research-status": "/api/research-status",
  "/api/provider-capabilities": "/api/provider-capabilities",
  // The Beliefs panel. This map is an explicit allowlist, not a prefix rule, so adding the
  // endpoint to core/app.py is not enough — without an entry here the panel renders empty
  // against a 404 from this layer, which is exactly how it first shipped.
  "/api/research-ranking": "/api/research-ranking",
  "/api/product-status": "/api/product-status",
  "/api/morning-brief": "/api/morning-brief",
  "/api/research-desk": "/api/research-desk",
  "/api/paper-portfolios": "/api/paper-portfolios",
  "/api/prospective-fronttests": "/api/prospective-fronttests",
  "/api/autopilot-status": "/api/autopilot-status",
  "/api/options-chain": "/api/options-chain",
  "/api/options-builder": "/api/options-builder",
  "/api/portfolio-risk": "/api/portfolio-risk",
  "/api/watchlists": "/api/watchlists",
  "/api/screens": "/api/screens",
  "/api/journal": "/api/journal",
  "/api/company-context": "/api/company-context",
  "/api/operator-status": "/api/operator-status",
  "/api/options-backtest": "/api/options-backtest",
  "/api/gex-replay": "/api/gex-replay",
  "/api/alerts": "/api/alerts",
  "/api/alert-metric": "/api/alert-metric",
  "/api/evidence-status": "/api/evidence-status",
  "/api/signal-backtest": "/api/signal-backtest",
  "/api/strategies": "/api/strategies",
  "/api/matrix": "/api/matrix",
  "/api/heatmap": "/api/heatmap",
  "/api/night-vision": "/api/night-vision",
  "/api/bars": "/api/bars",
  "/api/flow": "/api/flow",
  "/api/spyglass": "/api/flow",
  "/api/flow/job": "/api/flow/job",
  "/api/contract-search": "/api/contract-search",
  "/api/scan": "/api/scan",
  "/api/scanner": "/api/scan",
  "/api/scan/job": "/api/scan/job",
  "/api/scan/history": "/api/scan/history",
  "/api/flash-agentic/live": "/api/flash-agentic/live",
  "/api/scan/universe": "/api/scan/universe",
  "/api/ranking-lab": "/api/ranking-lab",
  "/api/weight-lab": "/api/weight-lab",
  "/api/backtest": "/api/backtest",
};

function trustedCoreHeaders(userContext) {
  if (!hostedMode || !userContext) return {};
  const headers = {
    "x-cipher-internal-token": internalProxyToken,
    "x-cipher-user-id": userContext.userId,
  };
  if (userContext.guest) {
    headers["x-cipher-guest"] = "1";
    return headers;
  }
  headers["x-cipher-access-token"] = userContext.accessToken;
  const providerSessionId = providerSessionClient.sessionFor(userContext.userId);
  if (providerSessionId) headers["x-cipher-provider-session"] = providerSessionId;
  return headers;
}

async function proxyCore(res, requestPath, query, { method = "GET", body = null, headers = {}, acceptEncoding = "", requestOrigin = "" } = {}) {
  const target = new URL(requestPath, coreUrl);
  for (const [key, value] of query) target.searchParams.set(key, value);
  const init = { method, headers: { accept: "application/json", ...headers } };
  if (body != null) {
    init.body = body;
    if (!init.headers["content-type"] && !init.headers["Content-Type"]) {
      init.headers["content-type"] = "application/json";
    }
  }
  const response = await fetch(target, init);
  const data = Buffer.from(await response.arrayBuffer());
  // Grid payloads are large and highly repetitive: SPY's full chain is 1.43 MB
  // raw and 152 KB gzipped, a 9.4x reduction. Uncompressed that is the dominant
  // cost of a depth change, and it becomes the dominant cost of everything once
  // this is served over a network rather than from localhost.
  const outHeaders = {
    "content-type": response.headers.get("content-type") || "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    vary: "accept-encoding",
    ...corsHeaders(requestOrigin),
  };
  const accepts = String(acceptEncoding || "");
  if (data.length > GZIP_MIN_BYTES && /\bgzip\b/.test(accepts)) {
    try {
      const packed = await gzip(data);
      outHeaders["content-encoding"] = "gzip";
      res.writeHead(response.status, outHeaders);
      res.end(packed);
      return;
    } catch {
      /* fall through to the uncompressed response */
    }
  }
  res.writeHead(response.status, outHeaders);
  res.end(data);
}

async function proxySSE(req, res, query, userContext = null) {
  const target = new URL("/api/stream", coreUrl);
  for (const [key, value] of query) target.searchParams.set(key, value);
  const controller = new AbortController();
  const onClose = () => controller.abort();
  req.on("close", onClose);
  try {
    const response = await fetch(target, {
      headers: { accept: "text/event-stream", ...trustedCoreHeaders(userContext) },
      signal: controller.signal,
    });
    res.writeHead(response.status, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-store",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    });
    if (!response.body) {
      res.end();
      return;
    }
    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
    }
    res.end();
  } catch (error) {
    if (!res.headersSent) {
      sendJson(res, 503, {
        error: "Live stream unavailable. Start the Cipher core service.",
        detail: String(error?.message || error),
        read_only: true,
      });
    } else {
      try { res.end(); } catch { /* ignore */ }
    }
  } finally {
    req.off("close", onClose);
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || "127.0.0.1"}`);
  if (hostedMode && (req.method || "GET").toUpperCase() === "OPTIONS") {
    const allowed = corsHeaders(req.headers.origin);
    if (!allowed["access-control-allow-origin"]) return sendJson(res, 403, { error: "origin not allowed" });
    res.writeHead(204, {
      ...allowed,
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "Authorization, Content-Type, Accept",
    });
    return res.end();
  }
  if (url.pathname === "/api/scanner-ingest" || url.pathname === "/api/scanner-ingest/") {
    if (hostedMode) return sendJson(res, 401, { error: "authentication required" }, corsHeaders(req.headers.origin));
    return scannerIngest(req, res);
  }
  if (hostedMode && url.pathname === "/accessobsidian-browser-logger.js") {
    return sendJson(res, 404, { error: "not found" });
  }
  if (url.pathname === "/accessobsidian-browser-logger.js") {
    if ((req.method || "GET").toUpperCase() !== "GET") {
      return sendJson(res, 405, { error: "method not allowed" });
    }
    try {
      const file = await readFile(accessObsidianLoggerPath);
      res.writeHead(200, {
        "content-type": "text/javascript; charset=utf-8",
        "cache-control": "no-store",
        "access-control-allow-origin": "*",
        "x-content-type-options": "nosniff",
      });
      return res.end(file);
    } catch {
      return sendJson(res, 404, { error: "logger not found" });
    }
  }
  if (url.pathname === "/api/health") {
    // Deliberately reachable without a session so an uptime check does not need one —
    // which means this exact response is public the moment the port is published.
    // The core's /health reports service name, market_data_configured, and the
    // configured feeds; verify-cloudflare-access.py treats "market_data_configured" in
    // an unauthenticated body as disclosure, and it is right to. So an anonymous caller
    // gets liveness and nothing that identifies the deployment or its data sources.
    // Authenticated callers still get the full core health below.
    if (hostedMode) {
      const userContext = await validateHostedRequest(req);
      if (!userContext) return sendJson(res, 200, { status: "ok" }, corsHeaders(req.headers.origin));
      try {
        return await proxyCore(res, "/health", new URLSearchParams(), {
          acceptEncoding: req.headers["accept-encoding"] || "",
          headers: trustedCoreHeaders(userContext),
          requestOrigin: req.headers.origin || "",
        });
      } catch {
        return sendJson(res, 503, { status: "unavailable", read_only: true }, corsHeaders(req.headers.origin));
      }
    }
    if (!authGate.isAuthenticated(req)) return sendJson(res, 200, { status: "ok" });
    try {
      return await proxyCore(res, "/health", new URLSearchParams(), {
        acceptEncoding: req.headers["accept-encoding"] || "",
      });
    } catch {
      return sendJson(res, 503, { status: "unavailable", read_only: true });
    }
  }
  if (hostedMode && url.pathname === "/auth/session") {
    const method = (req.method || "GET").toUpperCase();
    const headers = corsHeaders(req.headers.origin);
    if (method === "GET") {
      const session = authSessions.get(req);
      if (!session) return sendJson(res, 401, { authenticated: false }, headers);
      return sendJson(res, 200, { authenticated: true, user: { id: session.userId } }, headers);
    }
    if (method === "POST") {
      const validated = await supabaseAuth.validateRequest(req);
      if (!validated) return sendJson(res, 401, { error: "authentication required" }, headers);
      const cookie = authSessions.create(validated);
      return sendJson(
        res,
        200,
        { authenticated: true, user: { id: validated.userId } },
        { ...headers, "set-cookie": cookie },
      );
    }
    if (method === "DELETE") {
      const session = authSessions.get(req);
      if (session) {
        await providerSessionClient.disconnect(session).catch(() => {});
      }
      const cookie = authSessions.clear(req);
      return sendJson(res, 200, { authenticated: false }, { ...headers, "set-cookie": cookie });
    }
    return sendJson(res, 405, { error: "method not allowed" }, { ...headers, allow: "GET, POST, DELETE" });
  }
  if (hostedMode && (url.pathname === "/api/login" || url.pathname === "/api/logout")) {
    return sendJson(res, 404, { error: "local authentication is disabled in hosted mode" }, corsHeaders(req.headers.origin));
  }
  if (url.pathname === "/api/login") {
    if ((req.method || "GET").toUpperCase() !== "POST") {
      return sendJson(res, 405, { error: "method not allowed" }, { allow: "POST" });
    }
    let raw;
    try {
      raw = await readBoundedBody(req);
    } catch (error) {
      return sendJson(res, error instanceof RangeError ? 413 : 400, { error: "invalid request" });
    }
    let password = "";
    try {
      if (String(req.headers["content-type"] || "").includes("application/json")) {
        password = String(JSON.parse(raw).password || "");
      } else {
        password = String(new URLSearchParams(raw).get("password") || "");
      }
    } catch {
      return sendJson(res, 400, { error: "invalid request" });
    }
    const result = await authGate.login(password, clientKey(req));
    if (!result.ok) {
      const retrySeconds = Math.max(1, Math.ceil((result.retryAfterMs || 0) / 1000));
      return sendJson(
        res,
        result.retryAfterMs > 0 ? 429 : 401,
        { error: "invalid credentials", retry_after_seconds: retrySeconds },
        result.retryAfterMs > 0 ? { "retry-after": String(retrySeconds) } : {},
      );
    }
    return sendJson(res, 200, { ok: true }, result.cookie ? { "set-cookie": result.cookie } : {});
  }
  if (url.pathname === "/api/logout") {
    if ((req.method || "GET").toUpperCase() !== "POST") {
      return sendJson(res, 405, { error: "method not allowed" }, { allow: "POST" });
    }
    return sendJson(res, 200, { ok: true }, { "set-cookie": authGate.logoutCookie() });
  }
  let hostedUser = null;
  let guestContext = null;
  if (hostedMode) {
    hostedUser = await validateHostedRequest(req);
    if (!hostedUser && guestAccessAllowed(req, url)) {
      guestContext = { userId: "guest", accessToken: null, guest: true };
    }
    if (!hostedUser && !guestContext) {
      if (url.pathname.startsWith("/api/") || url.pathname === "/api/stream" || url.pathname === "/api/live") {
        return sendJson(res, 401, { error: "authentication required" }, corsHeaders(req.headers.origin));
      }
      try {
        return await sendLoginPage(res);
      } catch {
        return sendJson(res, 503, { error: "login page unavailable" });
      }
    }
  } else if (!authGate.isAuthenticated(req)) {
    if (url.pathname.startsWith("/api/") || url.pathname === "/api/stream" || url.pathname === "/api/live") {
      return sendJson(res, 401, { error: "authentication required" });
    }
    if ((req.method || "GET").toUpperCase() !== "GET") {
      return sendJson(res, 401, { error: "authentication required" });
    }
    try {
      return await sendLoginPage(res);
    } catch {
      return sendJson(res, 503, { error: "login page unavailable" });
    }
  }
  if (hostedMode && url.pathname === "/api/provider-session") {
    const method = (req.method || "GET").toUpperCase();
    try {
      if (method === "GET") {
        return sendJson(res, 200, await providerSessionClient.status(hostedUser), corsHeaders(req.headers.origin));
      }
      if (method !== "POST") return sendJson(res, 405, { error: "method not allowed" }, corsHeaders(req.headers.origin));
      const raw = await readBoundedBody(req, 4096);
      let body;
      try { body = JSON.parse(raw || "{}"); } catch { return sendJson(res, 400, { error: "invalid request" }, corsHeaders(req.headers.origin)); }
      const action = String(body.action || "connect").toLowerCase();
      if (action === "connect") {
        const result = await providerSessionClient.connect({
          userId: hostedUser.userId,
          accessToken: hostedUser.accessToken,
          key: body.key,
          secret: body.secret,
          optionsFeed: body.options_feed,
          stockFeed: body.stock_feed,
        });
        return sendJson(res, 200, { status: result.status, read_only: true }, corsHeaders(req.headers.origin));
      }
      if (action === "disconnect") {
        await providerSessionClient.disconnect(hostedUser);
        return sendJson(res, 200, { status: "disconnected", read_only: true }, corsHeaders(req.headers.origin));
      }
      return sendJson(res, 400, { error: "unknown provider session action" }, corsHeaders(req.headers.origin));
    } catch (error) {
      return sendJson(res, 503, { error: String(error?.message || "provider session unavailable").slice(0, 240), read_only: true }, corsHeaders(req.headers.origin));
    }
  }
  const coreUserContext = hostedUser || guestContext;
  if (url.pathname === "/api/stream" || url.pathname === "/api/live") {
    const query = new URLSearchParams(url.searchParams);
    if (query.has("symbol") && !query.has("ticker")) {
      query.set("ticker", query.get("symbol"));
      query.delete("symbol");
    }
    return proxySSE(req, res, query, coreUserContext);
  }
  if (routes[url.pathname]) {
    const query = new URLSearchParams(url.searchParams);
    if (query.has("symbol") && !query.has("ticker")) {
      query.set("ticker", query.get("symbol"));
      query.delete("symbol");
    }
    try {
      const method = (req.method || "GET").toUpperCase();
      if (method === "OPTIONS") {
        res.writeHead(204, {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET, POST, OPTIONS",
          "access-control-allow-headers": "Content-Type, Accept",
        });
        return res.end();
      }
      let body = null;
      const headers = {};
      if (method === "POST" || method === "PUT" || method === "PATCH") {
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        body = Buffer.concat(chunks);
        const ct = req.headers["content-type"];
        if (ct) headers["content-type"] = ct;
      }
      return await proxyCore(res, routes[url.pathname], query, {
        method,
        body,
        headers: { ...headers, ...trustedCoreHeaders(coreUserContext) },
        acceptEncoding: req.headers["accept-encoding"] || "",
        requestOrigin: req.headers.origin || "",
      });
    } catch {
      return sendJson(res, 503, {
        error: "Local market-data service is unavailable. Start the Cipher app launcher.",
        read_only: true,
      });
    }
  }
  const publicDir = join(root, "public");
  const requestPath = url.pathname === "/" ? "/index.html" : url.pathname;
  const target = normalize(join(publicDir, requestPath));
  if (target !== publicDir && !target.startsWith(publicDir + "/")) return sendJson(res, 403, { error: "forbidden" });
  try {
    const file = await readFile(target);
    res.writeHead(200, {
      "content-type": mime[extname(target)] || "application/octet-stream",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    });
    res.end(file);
  } catch {
    sendJson(res, 404, { error: "not found" });
  }
}).listen(port, "127.0.0.1", () => console.log(`Cipher Research app: http://127.0.0.1:${port}`));
