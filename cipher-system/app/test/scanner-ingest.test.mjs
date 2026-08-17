import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createScannerIngestHandler,
  normalizeScannerPayload,
  persistScannerPayload,
} from "../scanner_ingest.mjs";

function mockRequest({ body, origin = "", token = "" }) {
  const encoded = Buffer.from(JSON.stringify(body));
  return {
    method: "POST",
    headers: {
      "content-length": String(encoded.length),
      ...(origin ? { origin } : {}),
      ...(token ? { "x-cipher-ingest-token": token } : {}),
    },
    async *[Symbol.asyncIterator]() {
      yield encoded;
    },
  };
}

function mockResponse() {
  return {
    status: null,
    headers: {},
    body: "",
    writeHead(status, headers = {}) {
      this.status = status;
      this.headers = headers;
    },
    end(chunk = "") {
      this.body += String(chunk);
    },
  };
}

test("normalizes Flash payload metadata and card fields", () => {
  const normalized = normalizeScannerPayload({
    source: "accessobsidian",
    scan_type: "Flash BETA",
    captured_at: "2026-07-27T13:00:00-04:00",
    setups: [
      {
        ticker: "aapl",
        direction: "bull",
        setup_type: "Breakout Continuation",
        score: 97,
        spot: 220,
        pivot: "221 (trigger)",
        target: 224,
        invalidation: 217,
      },
    ],
  });

  assert.equal(normalized.source, "accessobsidian");
  assert.equal(normalized.scanType, "flash");
  assert.equal(normalized.clientTimestamp, "2026-07-27T13:00:00-04:00");
  assert.equal(normalized.cards.length, 1);
  assert.equal(normalized.cards[0].ticker, "AAPL");
  assert.equal(normalized.cards[0].direction, "BULLISH");
  assert.equal(normalized.cards[0].setupFamily, "breakout_continuation");
  assert.equal(normalized.cards[0].pivot, 221);
  assert.equal(normalized.cards[0].geometryValid, true);
  assert.equal(normalized.cards[0].actionable, true);
});

test("moves an overloaded Cluster score into strength", () => {
  const normalized = normalizeScannerPayload({
    mode: "cluster",
    cards: [{
      symbol: "REGN",
      direction: "upside",
      setup_type: "QUAD UPSIDE",
      cluster_target: 590,
      score: 272,
    }],
  });

  const card = normalized.cards[0];
  assert.equal(normalized.scanType, "cluster");
  assert.equal(card.ticker, "REGN");
  assert.equal(card.score, "");
  assert.equal(card.strength, 272);
  assert.deepEqual(card.validationErrors, []);
  assert.match(card.validationWarnings.join(" "), /moved_to_strength/);
});

test("flags cross-card target leakage instead of treating it as actionable", () => {
  const normalized = normalizeScannerPayload({
    scan_type: "flash_agentic",
    cards: [{
      ticker: "TSLA",
      direction: "BULLISH",
      setup_type: "FLOOR BOUNCE",
      score: 99,
      spot: 330,
      pivot: 327.5,
      target: 195,
      invalidation: 325,
    }],
  });

  const card = normalized.cards[0];
  assert.equal(card.geometryValid, false);
  assert.equal(card.actionable, false);
  assert.ok(card.validationErrors.includes("bullish_target_not_above_spot"));
  assert.ok(card.validationErrors.includes("target_more_than_12pct_from_spot"));
});

test("persists stable signal IDs and marks repeated polls as updates", async () => {
  const dataDir = await mkdtemp(join(tmpdir(), "cipher-ingest-"));
  try {
    const payload = {
      source: "accessobsidian",
      scan_type: "flash",
      captured_at: "2026-07-27T13:00:00-04:00",
      setups: [{
        ticker: "AMD",
        direction: "BULLISH",
        state: "ARMING",
        setup_type: "Breakout Continuation",
        score: 95,
        spot: 170.5,
        pivot: 171,
        target: 174,
        invalidation: 168,
      }],
    };
    const first = await persistScannerPayload({
      dataDir,
      now: new Date("2026-07-27T17:00:01.000Z"),
      requestId: "request-1",
      payload,
    });
    const second = await persistScannerPayload({
      dataDir,
      now: new Date("2026-07-27T17:01:01.000Z"),
      requestId: "request-2",
      payload: {
        ...payload,
        captured_at: "2026-07-27T13:01:00-04:00",
        setups: [{ ...payload.setups[0], state: "TRIGGERED", score: 97, spot: 171.2 }],
      },
    });

    assert.equal(first.recordsWritten, 1);
    assert.equal(first.newSignals, 1);
    assert.equal(second.recordsWritten, 1);
    assert.equal(second.newSignals, 0);
    assert.equal(first.cards[0].signalId, second.cards[0].signalId);
    assert.equal(second.cards[0].seenCount, 2);
    assert.equal(second.cards[0].state, "TRIGGERED");

    const jsonl = await readFile(join(dataDir, first.jsonlName), "utf8");
    const events = jsonl.trim().split("\n").map((line) => JSON.parse(line));
    assert.equal(events.length, 2);
    assert.equal(events[0].schema_version, 2);
    assert.equal(events[0].new_signals, 1);
    assert.equal(events[1].new_signals, 0);
    assert.equal(events[1].normalized_cards[0].seen_count, 2);

    assert.match(first.jsonlName, /^flash-scans-v2-/);
    assert.match(first.csvName, /^flash-observations-v2-/);
    assert.match(first.signalCsvName, /^flash-signals-v2-/);

    const csv = await readFile(join(dataDir, first.csvName), "utf8");
    assert.match(csv, /signal_id/);
    assert.match(csv, /scan_type/);
    assert.match(csv, /"flash"/);
    assert.match(csv, /"TRIGGERED"/);
    assert.equal(csv.trim().split("\n").length, 3);

    const signalsCsv = await readFile(join(dataDir, first.signalCsvName), "utf8");
    assert.equal(signalsCsv.trim().split("\n").length, 2);
    assert.doesNotMatch(signalsCsv, /"TRIGGERED"/);
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
});

test("starts a new signal episode after the ten-minute gap", async () => {
  const dataDir = await mkdtemp(join(tmpdir(), "cipher-ingest-gap-"));
  try {
    const card = {
      ticker: "NVDA",
      direction: "BEARISH",
      setup_type: "CEILING REJECTION",
      spot: 210,
      pivot: 210,
      target: 208,
      invalidation: 212.5,
    };
    const first = await persistScannerPayload({
      dataDir,
      now: new Date("2026-07-27T17:00:00.000Z"),
      payload: { scan_type: "flash_agentic", captured_at: "2026-07-27T17:00:00.000Z", cards: [card] },
    });
    const second = await persistScannerPayload({
      dataDir,
      now: new Date("2026-07-27T17:11:01.000Z"),
      payload: { scan_type: "flash_agentic", captured_at: "2026-07-27T17:11:01.000Z", cards: [card] },
    });

    assert.equal(second.newSignals, 1);
    assert.notEqual(first.cards[0].signalId, second.cards[0].signalId);
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
});

test("allows the AccessObsidian browser origin without exposing the ingest token", async () => {
  const dataDir = await mkdtemp(join(tmpdir(), "cipher-ingest-origin-"));
  try {
    const handler = createScannerIngestHandler({
      dataDir,
      ingestToken: "secret-token",
      logger: { info() {}, error() {} },
    });
    const response = mockResponse();
    await handler(
      mockRequest({
        origin: "https://www.accessobsidian.com",
        body: { scan_type: "flash", cards: [] },
      }),
      response,
    );
    assert.equal(response.status, 202);
    assert.equal(JSON.parse(response.body).schema_version, 2);
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
});

test("requires the token for non-browser callers", async () => {
  const dataDir = await mkdtemp(join(tmpdir(), "cipher-ingest-token-"));
  try {
    const handler = createScannerIngestHandler({
      dataDir,
      ingestToken: "secret-token",
      logger: { info() {}, error() {} },
    });
    const rejected = mockResponse();
    await handler(
      mockRequest({ body: { scan_type: "flash", cards: [] } }),
      rejected,
    );
    assert.equal(rejected.status, 401);

    const accepted = mockResponse();
    await handler(
      mockRequest({
        token: "secret-token",
        body: { scan_type: "flash", cards: [] },
      }),
      accepted,
    );
    assert.equal(accepted.status, 202);
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
});

test("logs an empty scan in JSONL without manufacturing a CSV row", async () => {
  const dataDir = await mkdtemp(join(tmpdir(), "cipher-ingest-empty-"));
  try {
    const result = await persistScannerPayload({
      dataDir,
      now: new Date("2026-07-27T18:10:00.000Z"),
      requestId: "empty-request",
      payload: { scan_type: "cluster", cards: [] },
    });

    assert.equal(result.recordsWritten, 0);
    assert.equal(result.newSignals, 0);
    assert.equal(result.csvName, null);
    assert.equal(result.signalCsvName, null);
    assert.match(result.jsonlName, /^cluster-scans-v2-/);
    const jsonl = await readFile(join(dataDir, result.jsonlName), "utf8");
    const event = JSON.parse(jsonl.trim());
    assert.equal(event.records, 0);
    assert.equal(event.schema_version, 2);
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
});

test("best-effort forwards normalized cards to the local shadow executor", async () => {
  const dataDir = await mkdtemp(join(tmpdir(), "cipher-ingest-forward-"));
  const calls = [];
  try {
    const handler = createScannerIngestHandler({
      dataDir,
      ingestToken: "secret-token",
      forwardUrl: "http://127.0.0.1:8787/api/scanner-ingest",
      forwarder: async (url, options) => {
        calls.push({ url, options, body: JSON.parse(options.body) });
        return { ok: true, status: 202 };
      },
      logger: { info() {}, error() {} },
    });
    const response = mockResponse();
    await handler(mockRequest({
      token: "secret-token",
      body: {
        scan_type: "Flash BETA",
        captured_at: "2026-08-13T14:00:00Z",
        cards: [{ ticker: "NVDA", direction: "bullish", setup_type: "floor bounce",
          spot: 180, target: 183, invalidation: 178 }],
      },
    }), response);
    assert.equal(response.status, 202);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].body.scan_type, "flash");
    assert.equal(calls[0].body.cards[0].scanner_type, "flash");
    assert.equal(calls[0].body.cards[0].ticker, "NVDA");
    assert.equal(JSON.parse(response.body).shadow_forwarded, true);
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
});
