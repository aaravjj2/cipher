(function () {
  "use strict";

  const LOCAL_EXECUTOR_URL = "http://127.0.0.1:8787/api/scanner-ingest";
  const REMOTE_FALLBACK_URL = window.CIPHER_REMOTE_SCANNER_INGEST_URL || "";
  const LOCAL_TIMEOUT_MS = 900;

  function stableStringify(value) {
    if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]";
    if (value && typeof value === "object") {
      return "{" + Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + stableStringify(value[key])).join(",") + "}";
    }
    return JSON.stringify(value);
  }

  async function digest(text) {
    const bytes = new TextEncoder().encode(text);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(hash)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function withIds(payload) {
    const copy = Object.assign({}, payload);
    copy.source = copy.source || "access_obsidian_browser";
    copy.captured_at = copy.captured_at || new Date().toISOString();
    copy.checksum = copy.checksum || await digest(stableStringify(copy));
    copy.batch_id = copy.batch_id || "ao_" + copy.checksum.slice(0, 32);
    return copy;
  }

  async function postJson(url, payload, timeoutMs) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        method: "POST",
        mode: "cors",
        cache: "no-store",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      return response.ok;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function deliverScannerSnapshot(payload) {
    const enriched = await withIds(payload);
    try {
      if (await postJson(LOCAL_EXECUTOR_URL, enriched, LOCAL_TIMEOUT_MS)) {
        return { delivered: "local", batch_id: enriched.batch_id };
      }
    } catch (_) {
      // Local executor may be offline; fallback keeps capture delivery available.
    }
    if (REMOTE_FALLBACK_URL) {
      const ok = await postJson(REMOTE_FALLBACK_URL, enriched, 4000);
      return { delivered: ok ? "remote_fallback" : "failed", batch_id: enriched.batch_id };
    }
    return { delivered: "failed", batch_id: enriched.batch_id };
  }

  window.CipherAccessObsidianLogger = Object.freeze({
    deliverScannerSnapshot
  });
})();
