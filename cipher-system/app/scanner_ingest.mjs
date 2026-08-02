import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import {
  appendFile,
  mkdir,
  readFile,
  rename,
  writeFile,
} from "node:fs/promises";
import { join } from "node:path";

const SCHEMA_VERSION = 2;
const SIGNAL_EPISODE_GAP_MS = 10 * 60 * 1000;
const LEDGER_RETENTION_MS = 30 * 24 * 60 * 60 * 1000;
const MAX_DIRECTIONAL_DISTANCE_FRACTION = 0.12;
const LEDGER_NAME = "scanner-signal-ledger-v2.json";

const CSV_HEADER = [
  "received_at",
  "request_id",
  "source",
  "scan_type",
  "client_timestamp",
  "card_timestamp",
  "card_index",
  "signal_id",
  "signal_signature",
  "is_new_signal",
  "first_seen_at",
  "last_seen_at",
  "seen_count",
  "state",
  "ticker",
  "direction",
  "setup_type",
  "setup_family",
  "score",
  "strength",
  "spot",
  "pivot",
  "target",
  "invalidation",
  "geometry_valid",
  "actionable",
  "validation_errors",
  "validation_warnings",
  "raw_json",
].join(",") + "\n";

const DEFAULT_ALLOWED_ORIGINS = [
  "https://www.accessobsidian.com",
  "https://accessobsidian.com",
];

const ledgerQueues = new Map();

function firstDefined(object, keys) {
  if (!object || typeof object !== "object") return "";
  for (const key of keys) {
    const value = object[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "";
}

function textValue(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).trim();
}

function numericValue(value) {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "number") return Number.isFinite(value) ? value : "";
  const match = String(value).replaceAll(",", "").match(/[-+]?\d*\.?\d+/);
  if (!match) return "";
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : "";
}

function csvCell(value) {
  const text = textValue(value);
  return `"${text.replaceAll('"', '""')}"`;
}

function sha256(value) {
  return createHash("sha256").update(String(value)).digest("hex");
}

function canonicalScanType(value) {
  const normalized = textValue(value).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const aliases = new Map([
    ["flash", "flash"],
    ["flash_beta", "flash"],
    ["flash_index", "flash_index"],
    ["flash_index_beta", "flash_index"],
    ["flash_agentic", "flash_agentic"],
    ["flash_agentic_beta", "flash_agentic"],
    ["agentic", "flash_agentic"],
    ["cluster", "cluster"],
    ["cluster_scan", "cluster"],
    ["liq", "liq"],
    ["liq_scan", "liq"],
    ["cipher_model", "cipher_model"],
    ["cipher_model_scan", "cipher_model"],
  ]);
  return aliases.get(normalized) || normalized || "unknown";
}

function canonicalDirection(value) {
  const normalized = textValue(value).toUpperCase();
  if (["BULL", "LONG", "UPSIDE"].includes(normalized)) return "BULLISH";
  if (["BEAR", "SHORT", "DOWNSIDE"].includes(normalized)) return "BEARISH";
  if (["BULLISH", "BEARISH", "NEUTRAL"].includes(normalized)) return normalized;
  return normalized;
}

function canonicalState(value) {
  const normalized = textValue(value).toUpperCase();
  const aliases = new Map([
    ["ACTIVE", "ACTIVE"],
    ["SURFACED", "SURFACED"],
    ["ARMING", "ARMING"],
    ["TRIGGERED", "TRIGGERED"],
    ["COMPLETED", "COMPLETED"],
    ["INVALIDATED", "INVALIDATED"],
  ]);
  return aliases.get(normalized) || normalized;
}

function canonicalSetup(value) {
  return textValue(value).replace(/\s+/g, " ").trim().toUpperCase();
}

function setupFamily(value) {
  return canonicalSetup(value)
    .replace(/#\d+.*$/g, "")
    .replace(/\b(?:ACTIVE|SURFACED|ARMING|TRIGGERED|COMPLETED|INVALIDATED)\b/g, "")
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
}

function identityNumber(value) {
  return value === "" ? "" : Number(value).toFixed(4);
}

function parseTimestamp(value, fallback) {
  const parsed = Date.parse(textValue(value));
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : fallback;
}

function geometryValidation({ scanType, direction, spot, target, invalidation }) {
  const errors = [];
  const warnings = [];
  const directional = direction === "BULLISH" || direction === "BEARISH";
  const intradayMode = ["flash", "flash_index", "flash_agentic"].includes(scanType);

  if (!directional) {
    if (direction && direction !== "NEUTRAL") warnings.push("unrecognized_direction");
    return { errors, warnings, geometryValid: true };
  }
  if (spot === "") {
    warnings.push("missing_spot");
    return { errors, warnings, geometryValid: true };
  }

  if (target !== "") {
    if (direction === "BULLISH" && target <= spot) errors.push("bullish_target_not_above_spot");
    if (direction === "BEARISH" && target >= spot) errors.push("bearish_target_not_below_spot");
    if (Math.abs(target - spot) / spot > MAX_DIRECTIONAL_DISTANCE_FRACTION) {
      errors.push("target_more_than_12pct_from_spot");
    }
  } else if (intradayMode) {
    warnings.push("missing_target");
  }

  if (invalidation !== "") {
    if (direction === "BULLISH" && invalidation >= spot) errors.push("bullish_invalidation_not_below_spot");
    if (direction === "BEARISH" && invalidation <= spot) errors.push("bearish_invalidation_not_above_spot");
    if (Math.abs(invalidation - spot) / spot > MAX_DIRECTIONAL_DISTANCE_FRACTION) {
      errors.push("invalidation_more_than_12pct_from_spot");
    }
  } else if (intradayMode) {
    warnings.push("missing_invalidation");
  }

  return { errors, warnings, geometryValid: errors.length === 0 };
}

function normalizeCard(card, envelope, cardIndex) {
  const rawCard = card && typeof card === "object" && !Array.isArray(card)
    ? card
    : { value: card };
  const scanType = envelope.scanType;
  const source = envelope.source;
  const ticker = textValue(firstDefined(rawCard, ["ticker", "symbol", "underlying"])).toUpperCase();
  const direction = canonicalDirection(firstDefined(rawCard, ["direction", "bias", "side"]));
  const setupType = canonicalSetup(firstDefined(rawCard, ["setup_type", "setupType", "setup", "type", "label"]));
  const family = setupFamily(setupType);
  const state = canonicalState(firstDefined(rawCard, ["state", "status", "lifecycle_state", "lifecycleState"]));
  const spot = numericValue(firstDefined(rawCard, ["spot", "spot_price", "spotPrice", "price"]));
  const pivot = numericValue(firstDefined(rawCard, ["pivot", "entry", "trigger"]));
  const target = numericValue(firstDefined(rawCard, ["target", "cluster_target", "clusterTarget", "first_target", "firstTarget"]));
  const invalidation = numericValue(firstDefined(rawCard, ["invalidation", "invalid", "stop", "stop_loss", "stopLoss"]));
  let score = numericValue(firstDefined(rawCard, ["score", "rating"]));
  let strength = numericValue(firstDefined(rawCard, ["strength", "cluster_strength", "clusterStrength", "abs_score", "absScore"]));
  const validationErrors = [];
  const validationWarnings = [];

  if (score !== "" && (score < 0 || score > 100)) {
    if (scanType === "cluster" && score > 100 && strength === "") {
      strength = score;
      score = "";
      validationWarnings.push("out_of_range_cluster_score_moved_to_strength");
    } else {
      validationErrors.push("score_out_of_range_0_100");
    }
  }

  if (!ticker) validationErrors.push("missing_ticker");
  if (scanType === "unknown") validationWarnings.push("unknown_scan_type");

  const geometry = geometryValidation({ scanType, direction, spot, target, invalidation });
  validationErrors.push(...geometry.errors);
  validationWarnings.push(...geometry.warnings);

  const structuralLevel = pivot !== "" ? pivot : target;
  const signalKeyMaterial = [
    source,
    scanType,
    ticker,
    direction,
    family,
    identityNumber(structuralLevel),
  ].join("|");
  const signalSignature = sha256(signalKeyMaterial).slice(0, 24);
  const actionable = (
    validationErrors.length === 0
    && (direction === "BULLISH" || direction === "BEARISH")
    && spot !== ""
    && target !== ""
    && invalidation !== ""
  );

  return {
    cardIndex,
    source,
    scanType,
    clientTimestamp: envelope.clientTimestamp,
    cardTimestamp: textValue(firstDefined(rawCard, ["captured_at", "capturedAt", "timestamp"])) || envelope.clientTimestamp,
    ticker,
    direction,
    setupType,
    setupFamily: family,
    state,
    score,
    strength,
    spot,
    pivot,
    target,
    invalidation,
    signalSignature,
    geometryValid: geometry.geometryValid,
    actionable,
    validationErrors,
    validationWarnings,
    rawCard,
  };
}

function parseOrigins(value) {
  if (!value) return new Set(DEFAULT_ALLOWED_ORIGINS);
  return new Set(
    String(value)
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

function extractToken(req) {
  const direct = req.headers["x-cipher-ingest-token"];
  if (typeof direct === "string" && direct) return direct;
  const authorization = req.headers.authorization;
  if (typeof authorization === "string" && authorization.startsWith("Bearer ")) {
    return authorization.slice(7).trim();
  }
  return "";
}

function tokenMatches(expected, supplied) {
  if (!expected) return true;
  const expectedBuffer = Buffer.from(expected);
  const suppliedBuffer = Buffer.from(supplied || "");
  return expectedBuffer.length === suppliedBuffer.length
    && timingSafeEqual(expectedBuffer, suppliedBuffer);
}

function corsHeaders(origin, allowedOrigins) {
  if (!origin || !allowedOrigins.has(origin)) return {};
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "Content-Type, Accept, Authorization, X-Cipher-Ingest-Token",
    "access-control-max-age": "600",
    vary: "Origin",
  };
}

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    ...headers,
  });
  res.end(JSON.stringify(body));
}

async function readJsonBody(req, maxBytes) {
  const declaredLength = Number(req.headers["content-length"] || 0);
  if (declaredLength > maxBytes) {
    const error = new Error("request body too large");
    error.statusCode = 413;
    throw error;
  }

  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > maxBytes) {
      const error = new Error("request body too large");
      error.statusCode = 413;
      throw error;
    }
    chunks.push(chunk);
  }

  if (!chunks.length) {
    const error = new Error("JSON request body is required");
    error.statusCode = 400;
    throw error;
  }

  let payload;
  try {
    payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    const error = new Error("invalid JSON request body");
    error.statusCode = 400;
    throw error;
  }

  if (payload === null || (typeof payload !== "object" && !Array.isArray(payload))) {
    const error = new Error("payload must be a JSON object or array");
    error.statusCode = 422;
    throw error;
  }
  return payload;
}

export function normalizeScannerPayload(payload) {
  const envelope = Array.isArray(payload) ? {} : payload;
  let cards;

  if (Array.isArray(payload)) {
    cards = payload;
  } else {
    const collection = ["setups", "cards", "results", "data"]
      .map((key) => payload[key])
      .find(Array.isArray);
    if (collection) {
      cards = collection;
    } else if (
      firstDefined(payload, ["ticker", "symbol", "underlying", "setup_type", "setupType", "strength", "score"]) !== ""
    ) {
      cards = [payload];
    } else {
      cards = [];
    }
  }

  if (cards.length > 1000) {
    const error = new Error("payload contains more than 1000 setup cards");
    error.statusCode = 422;
    throw error;
  }

  const metadata = {
    source: textValue(firstDefined(envelope, ["source", "provider"]) || "accessobsidian"),
    scanType: canonicalScanType(firstDefined(envelope, ["scan_type", "scanType", "mode", "tab", "scanner"])),
    clientTimestamp: textValue(firstDefined(envelope, ["captured_at", "capturedAt", "timestamp", "sent_at", "sentAt"])),
  };

  return {
    ...metadata,
    cards: cards.map((card, index) => normalizeCard(card, metadata, index)),
  };
}

async function loadLedger(path) {
  try {
    const parsed = JSON.parse(await readFile(path, "utf8"));
    return parsed && typeof parsed === "object" && parsed.entries
      ? parsed
      : { schema_version: 1, entries: {} };
  } catch (error) {
    if (error?.code === "ENOENT" || error instanceof SyntaxError) {
      return { schema_version: 1, entries: {} };
    }
    throw error;
  }
}

async function saveLedger(path, ledger) {
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(ledger, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  await rename(temporary, path);
}

async function serializeLedger(dataDir, task) {
  const previous = ledgerQueues.get(dataDir) || Promise.resolve();
  const current = previous.catch(() => {}).then(task);
  ledgerQueues.set(dataDir, current);
  try {
    return await current;
  } finally {
    if (ledgerQueues.get(dataDir) === current) ledgerQueues.delete(dataDir);
  }
}

async function assignSignalEpisodes(cards, dataDir, receivedAt) {
  return serializeLedger(dataDir, async () => {
    const ledgerPath = join(dataDir, LEDGER_NAME);
    const ledger = await loadLedger(ledgerPath);
    const nowMs = Date.parse(receivedAt);
    const assigned = [];

    for (const card of cards) {
      const observedAt = parseTimestamp(card.cardTimestamp, receivedAt);
      const observedMs = Date.parse(observedAt);
      const previous = ledger.entries[card.signalSignature];
      const previousMs = previous ? Date.parse(previous.last_seen_at) : Number.NaN;
      const elapsed = observedMs - previousMs;
      const isNewSignal = (
        !previous
        || !Number.isFinite(previousMs)
        || elapsed > SIGNAL_EPISODE_GAP_MS
        || elapsed < -60_000
      );
      const signalId = isNewSignal
        ? sha256(`${card.signalSignature}|${observedAt}`).slice(0, 24)
        : previous.signal_id;
      const firstSeenAt = isNewSignal ? observedAt : previous.first_seen_at;
      const seenCount = isNewSignal ? 1 : Number(previous.seen_count || 0) + 1;

      ledger.entries[card.signalSignature] = {
        signal_id: signalId,
        first_seen_at: firstSeenAt,
        last_seen_at: observedAt,
        seen_count: seenCount,
        source: card.source,
        scan_type: card.scanType,
        ticker: card.ticker,
        direction: card.direction,
        setup_family: card.setupFamily,
      };
      assigned.push({
        ...card,
        signalId,
        isNewSignal,
        firstSeenAt,
        lastSeenAt: observedAt,
        seenCount,
      });
    }

    for (const [key, value] of Object.entries(ledger.entries)) {
      const lastSeenMs = Date.parse(value.last_seen_at);
      if (Number.isFinite(lastSeenMs) && nowMs - lastSeenMs > LEDGER_RETENTION_MS) {
        delete ledger.entries[key];
      }
    }
    ledger.schema_version = 1;
    ledger.updated_at = receivedAt;
    await saveLedger(ledgerPath, ledger);
    return assigned;
  });
}

async function ensureCsvHeader(path) {
  try {
    await writeFile(path, CSV_HEADER, { flag: "wx", mode: 0o600 });
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
}

function csvRowsForCards(cards, normalized, receivedAt, requestId) {
  return cards.map((card) => [
    receivedAt,
    requestId,
    card.source,
    card.scanType,
    normalized.clientTimestamp,
    card.cardTimestamp,
    card.cardIndex,
    card.signalId,
    card.signalSignature,
    card.isNewSignal,
    card.firstSeenAt,
    card.lastSeenAt,
    card.seenCount,
    card.state,
    card.ticker,
    card.direction,
    card.setupType,
    card.setupFamily,
    card.score,
    card.strength,
    card.spot,
    card.pivot,
    card.target,
    card.invalidation,
    card.geometryValid,
    card.actionable,
    JSON.stringify(card.validationErrors),
    JSON.stringify(card.validationWarnings),
    JSON.stringify(card.rawCard),
  ].map(csvCell).join(",")).join("\n") + "\n";
}

async function appendCardsCsv(path, cards, normalized, receivedAt, requestId) {
  if (!cards.length) return;
  await ensureCsvHeader(path);
  await appendFile(
    path,
    csvRowsForCards(cards, normalized, receivedAt, requestId),
    { encoding: "utf8", mode: 0o600 },
  );
}

export async function persistScannerPayload({ payload, dataDir, now = new Date(), requestId = randomUUID() }) {
  const normalized = normalizeScannerPayload(payload);
  const receivedAt = now.toISOString();
  const day = receivedAt.slice(0, 10);
  const mode = normalized.scanType;
  const jsonlName = `${mode}-scans-v2-${day}.jsonl`;
  const csvName = `${mode}-observations-v2-${day}.csv`;
  const signalCsvName = `${mode}-signals-v2-${day}.csv`;
  const jsonlPath = join(dataDir, jsonlName);
  const csvPath = join(dataDir, csvName);
  const signalCsvPath = join(dataDir, signalCsvName);

  await mkdir(dataDir, { recursive: true, mode: 0o700 });
  const cards = await assignSignalEpisodes(normalized.cards, dataDir, receivedAt);
  const event = {
    schema_version: SCHEMA_VERSION,
    request_id: requestId,
    received_at: receivedAt,
    source: normalized.source,
    scan_type: normalized.scanType,
    client_timestamp: normalized.clientTimestamp,
    records: cards.length,
    new_signals: cards.filter((card) => card.isNewSignal).length,
    actionable_records: cards.filter((card) => card.actionable).length,
    invalid_records: cards.filter((card) => card.validationErrors.length > 0).length,
    normalized_cards: cards.map((card) => ({
      card_index: card.cardIndex,
      signal_id: card.signalId,
      signal_signature: card.signalSignature,
      is_new_signal: card.isNewSignal,
      first_seen_at: card.firstSeenAt,
      last_seen_at: card.lastSeenAt,
      seen_count: card.seenCount,
      state: card.state,
      ticker: card.ticker,
      direction: card.direction,
      setup_type: card.setupType,
      setup_family: card.setupFamily,
      score: card.score,
      strength: card.strength,
      spot: card.spot,
      pivot: card.pivot,
      target: card.target,
      invalidation: card.invalidation,
      geometry_valid: card.geometryValid,
      actionable: card.actionable,
      validation_errors: card.validationErrors,
      validation_warnings: card.validationWarnings,
    })),
    payload,
  };
  await appendFile(jsonlPath, `${JSON.stringify(event)}\n`, { encoding: "utf8", mode: 0o600 });

  await appendCardsCsv(csvPath, cards, normalized, receivedAt, requestId);
  const newSignalCards = cards.filter((card) => card.isNewSignal);
  await appendCardsCsv(signalCsvPath, newSignalCards, normalized, receivedAt, requestId);

  return {
    requestId,
    receivedAt,
    recordsWritten: cards.length,
    newSignals: cards.filter((card) => card.isNewSignal).length,
    actionableRecords: cards.filter((card) => card.actionable).length,
    invalidRecords: cards.filter((card) => card.validationErrors.length > 0).length,
    jsonlName,
    csvName: cards.length ? csvName : null,
    signalCsvName: newSignalCards.length ? signalCsvName : null,
    ledgerName: LEDGER_NAME,
    cards,
  };
}

export function createScannerIngestHandler({
  dataDir,
  allowedOrigins = process.env.CIPHER_INGEST_ALLOWED_ORIGINS,
  ingestToken = process.env.CIPHER_SCANNER_INGEST_TOKEN || "",
  maxBytes = Number(process.env.CIPHER_SCANNER_INGEST_MAX_BYTES || 1_048_576),
  logger = console,
} = {}) {
  if (!dataDir) throw new Error("scanner ingest dataDir is required");
  const originSet = parseOrigins(allowedOrigins);

  return async function scannerIngestHandler(req, res) {
    const method = (req.method || "GET").toUpperCase();
    const origin = typeof req.headers.origin === "string" ? req.headers.origin : "";
    const browserOriginAuthorized = Boolean(origin && originSet.has(origin));
    const cors = corsHeaders(origin, originSet);

    if (origin && !originSet.has(origin)) {
      return sendJson(res, 403, { error: "origin not allowed" });
    }

    if (method === "OPTIONS") {
      res.writeHead(204, {
        "cache-control": "no-store",
        ...cors,
      });
      return res.end();
    }

    if (method === "GET") {
      return sendJson(res, 200, {
        status: "ok",
        endpoint: "/api/scanner-ingest",
        schema_version: SCHEMA_VERSION,
        accepts: "POST application/json",
        storage: "versioned daily JSONL/CSV plus a stable signal episode ledger",
        authentication: ingestToken
          ? "allowlisted AccessObsidian browser origin over tailnet; token required otherwise"
          : "tailnet access and origin allowlist",
        max_bytes: maxBytes,
      }, cors);
    }

    if (method !== "POST") {
      return sendJson(res, 405, { error: "method not allowed" }, {
        allow: "GET, POST, OPTIONS",
        ...cors,
      });
    }

    if (!browserOriginAuthorized && !tokenMatches(ingestToken, extractToken(req))) {
      return sendJson(res, 401, { error: "invalid or missing ingest token" }, cors);
    }

    try {
      const payload = await readJsonBody(req, maxBytes);
      const result = await persistScannerPayload({ payload, dataDir });
      logger.info?.(
        `[scanner-ingest] accepted request=${result.requestId} records=${result.recordsWritten} new=${result.newSignals} invalid=${result.invalidRecords}`,
      );
      return sendJson(res, 202, {
        ok: true,
        schema_version: SCHEMA_VERSION,
        request_id: result.requestId,
        received_at: result.receivedAt,
        records_written: result.recordsWritten,
        new_signals: result.newSignals,
        actionable_records: result.actionableRecords,
        invalid_records: result.invalidRecords,
        files: {
          jsonl: result.jsonlName,
          observations_csv: result.csvName,
          signals_csv: result.signalCsvName,
          ledger: result.ledgerName,
        },
        trading_actions: false,
      }, cors);
    } catch (error) {
      const status = Number(error?.statusCode || 500);
      if (status >= 500) logger.error?.("[scanner-ingest] persistence failure", error);
      return sendJson(res, status, {
        error: status >= 500 ? "failed to persist scanner payload" : String(error.message || error),
      }, cors);
    }
  };
}
