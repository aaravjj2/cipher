/** Browser-facing application server. Credentials remain in the local core service. */
import { createServer } from "node:http";
import { gzip as gzipCb } from "node:zlib";
import { promisify } from "node:util";
import { chmod, readFile } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
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
});
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

const sendJson = (res, status, body) => {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(JSON.stringify(body));
};

const routes = {
  "/api/health": "/health",
  "/api/quote": "/api/quote",
  "/api/governance": "/api/governance",
  "/api/standing": "/api/standing",
  "/api/holdings": "/api/holdings",
  "/api/workspace-layouts": "/api/workspace-layouts",
  "/api/ask": "/api/ask",
  "/api/research-status": "/api/research-status",
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

async function proxyCore(res, requestPath, query, { method = "GET", body = null, headers = {}, acceptEncoding = "" } = {}) {
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

async function proxySSE(req, res, query) {
  const target = new URL("/api/stream", coreUrl);
  for (const [key, value] of query) target.searchParams.set(key, value);
  const controller = new AbortController();
  const onClose = () => controller.abort();
  req.on("close", onClose);
  try {
    const response = await fetch(target, {
      headers: { accept: "text/event-stream" },
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
  if (url.pathname === "/api/scanner-ingest" || url.pathname === "/api/scanner-ingest/") {
    return scannerIngest(req, res);
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
  if (url.pathname === "/api/stream" || url.pathname === "/api/live") {
    const query = new URLSearchParams(url.searchParams);
    if (query.has("symbol") && !query.has("ticker")) {
      query.set("ticker", query.get("symbol"));
      query.delete("symbol");
    }
    return proxySSE(req, res, query);
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
        method, body, headers,
        acceptEncoding: req.headers["accept-encoding"] || "",
      });
    } catch {
      return sendJson(res, 503, {
        error: "Local market-data service is unavailable. Start the Cipher app launcher.",
        read_only: true,
      });
    }
  }
  const requestPath = url.pathname === "/" ? "/index.html" : url.pathname;
  const target = normalize(join(root, "public", requestPath));
  if (!target.startsWith(join(root, "public"))) return sendJson(res, 403, { error: "forbidden" });
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
