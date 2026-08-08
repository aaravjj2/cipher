// Client for cipher-system's real backend, proxied same-origin by app/server.mjs
// (see routes table in cipher-system/app/server.mjs — /api/* is forwarded to the
// Python core service). All types here mirror the ACTUAL JSON shapes returned by
// that service, confirmed via live curl against /api/quote and /api/matrix —
// distinct from the mock types in src/types/cipher.ts used by not-yet-wired panels.

export type RealQuote = {
  ticker: string;
  bid: number;
  ask: number;
  mid: number;
  last: number;
  price_context: number;
  price_context_kind: string;
  as_of: string;
  feed: string;
  day_change_pct: number;
  prior_close: number;
};

export type RealMatrixCell = {
  expiration: string; // ISO date, e.g. "2026-08-07"
  call_gex: number;
  put_gex: number;
  net_gex: number;
  call_vex: number;
  put_vex: number;
  net_vex: number;
  call_oi: number;
  put_oi: number;
  volume: number;
  call_mid: number;
  put_mid: number;
  listed: boolean;
  oi_assumed_zero: boolean;
  // Provenance: gamma reconstructed via the implied-vol solver rather than taken from
  // the feed, and/or open interest substituted by session volume. See core/exposure.py.
  gamma_modeled?: boolean;
  /** Gamma solved from a mid at the minimum tick — unbiased but individually noisy. */
  iv_min_tick?: boolean;
  oi_from_volume?: boolean;
  available: boolean;
};

export type RealMatrixRow = {
  strike: number;
  is_spot_band: boolean;
  cells: RealMatrixCell[];
};

export type RealMatrixCoverage = {
  contracts: number;
  contracts_missing_gamma: number;
  contracts_missing_gamma_or_oi: number;
  contracts_oi_assumed_zero: number;
  contracts_gamma_modeled?: number;
  contracts_oi_from_volume?: number;
  calculated_cells: number;
  listed_cells: number;
};

export type RealMatrixResponse = {
  ticker: string;
  as_of: string;
  feed: string;
  quote: RealQuote;
  depth_points: number;
  depth_mode: string;
  expirations: string[];
  total_expirations_available: number;
  rows: RealMatrixRow[];
  coverage: RealMatrixCoverage;
};

export class ApiError extends Error {
  status?: number;
  readOnly?: boolean;
  constructor(message: string, status?: number, readOnly?: boolean) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.readOnly = readOnly;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { cache: "no-store", signal });
  if (!res.ok) {
    let detail = "";
    let readOnly = false;
    try {
      const body = await res.json();
      detail = body?.error || "";
      readOnly = Boolean(body?.read_only);
    } catch {
      /* body wasn't JSON — fall back to status text below */
    }
    throw new ApiError(detail || `Request failed (${res.status})`, res.status, readOnly);
  }
  return res.json() as Promise<T>;
}

export function fetchQuote(ticker: string, signal?: AbortSignal): Promise<RealQuote> {
  return getJson<RealQuote>(`/api/quote?symbol=${encodeURIComponent(ticker)}`, signal);
}

// core/exposure.py: DEFAULT_MATRIX_EXPIRATIONS=12, MAX_MATRIX_EXPIRATIONS=36. Confirmed
// against the real site that "Compact"/"Full" actually controls how many expiration
// columns are fetched (not a client-side strike-count filter as first assumed).
export function fetchMatrix(
  ticker: string,
  expirationCount?: number,
  signal?: AbortSignal,
  depth?: string
): Promise<RealMatrixResponse> {
  const params = new URLSearchParams({ symbol: ticker });
  if (expirationCount != null) params.set("expirations", String(expirationCount));
  if (depth != null) params.set("depth", depth);
  return getJson<RealMatrixResponse>(`/api/matrix?${params.toString()}`, signal);
}

export type RealBar = {
  time: string; // ISO timestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type RealBarsResponse = {
  ticker: string;
  timeframe: string;
  feed: string;
  bars: RealBar[];
};

export function fetchBars(
  ticker: string,
  timeframe: string,
  signal?: AbortSignal
): Promise<RealBarsResponse> {
  return getJson<RealBarsResponse>(
    `/api/bars?symbol=${encodeURIComponent(ticker)}&timeframe=${encodeURIComponent(timeframe)}`,
    signal
  );
}

export type RealLevelKind = "above_spot" | "below_spot" | "global";

export type RealLevel = {
  price: number;
  net_gex: number;
  abs_gex: number;
  net_vex: number;
  abs_vex: number;
  kind: RealLevelKind;
};

export type RealNightVisionSummary = {
  global_max_strike: number;
  call_wall_strike: number;
  put_wall_strike: number;
  gamma_flip_level: number;
};

/** One rung of the X-Ray strike ladder (core/app.py night_vision -> "xray"). */
export type RealXrayRung = {
  strike: number;
  net_gex: number;
  net_vex: number;
  abs_gex: number;
  abs_vex: number;
  kind: RealLevelKind;
};

/** Prior-session / extended-hours reaction level (core/session_levels.py). */
export type RealSessionLevel = {
  kind: string;
  /** Short chart label: PDH, PDL, PWH, PWL, PMH, PML, PostH, PostL. */
  label: string;
  price: number;
};

export type RealSessionLevels = {
  levels: RealSessionLevel[];
  session_dates?: { current?: string; previous_day?: string | null; postmarket_from?: string | null };
  warnings?: string[];
  note?: string;
};

export type RealNightVisionResponse = RealMatrixResponse & {
  summary: RealNightVisionSummary;
  levels: RealLevel[];
  peak: RealLevel;
  /** Full per-strike ladder the real product renders beside the chart. */
  xray?: RealXrayRung[];
  /** Heuristic short-horizon price path from the hedging surface. */
  ghost?: { step: number; price: number }[];
  ghost_note?: string;
  /** Previous day/week and pre/post-market extremes drawn alongside the exposure levels. */
  session_levels?: RealSessionLevels;
  /** Today's pre-market range as a percent of the pre-market low. */
  premarket_range_pct?: number | null;
};

export function fetchNightVision(
  ticker: string,
  signal?: AbortSignal,
  expirationCount?: number
): Promise<RealNightVisionResponse> {
  const params = new URLSearchParams({ symbol: ticker });
  // core/app.py's /api/night-vision accepts `expirations` and always has; Night
  // Vision never sent it, so its 1 Exp / Compact / Full / Leap pills only changed
  // a label while the levels underneath stayed on the server default.
  if (expirationCount != null) params.set("expirations", String(expirationCount));
  return getJson<RealNightVisionResponse>(`/api/night-vision?${params.toString()}`, signal);
}

// Real query params confirmed from core/app.py's /api/flow handler: min (or premium),
// pmax, type (all|call|put), side (all|buy|sell), money (or moneyness) (all|otm|itm).
export type RealFlowPrint = {
  ticker: string;
  contract: string;
  time: string; // ISO
  premium: number;
  size: number;
  price: number;
  strike: number;
  expiration: string; // ISO date
  type: "call" | "put";
  bid: number;
  ask: number;
  side: "buy" | "sell";
  tier: string;
  otm_pct: number;
  exchange: string;
  feed: string;
};

export type RealFlowResponse = {
  ticker: string;
  as_of: string;
  feed: string;
  quote: RealQuote;
  min_premium: number;
  count: number;
  prints: RealFlowPrint[];
};

export type FlowFilters = {
  minPremium?: number;
  maxPrice?: number;
  optionType?: "all" | "call" | "put";
  side?: "all" | "buy" | "sell";
  moneyness?: "all" | "otm" | "itm";
};

export function fetchFlow(
  ticker: string,
  filters: FlowFilters = {},
  signal?: AbortSignal
): Promise<RealFlowResponse> {
  const params = new URLSearchParams({ symbol: ticker });
  if (filters.minPremium != null) params.set("min", String(filters.minPremium));
  if (filters.maxPrice != null) params.set("pmax", String(filters.maxPrice));
  if (filters.optionType) params.set("type", filters.optionType);
  if (filters.side) params.set("side", filters.side);
  if (filters.moneyness) params.set("money", filters.moneyness);
  return getJson<RealFlowResponse>(`/api/spyglass?${params.toString()}`, signal);
}

// Bio: a real bulk-flow scan across a curated pharma/biotech/medtech ticker list
// (core/app.py's BIO_UNIVERSE + flow_bulk()) — no sector data is wired into the service,
// so this is a hand-picked list, not an exhaustive real sector universe. Measured at
// ~90s for 54 tickers, well past a reasonable blocking request, hence the async job here
// mirroring the Setup Scanner's start_scan_job/get_scan_job pattern.
export type RealBioFlowResult = {
  as_of: string;
  feed: string;
  universe_size: number;
  names_with_prints: number;
  min_premium: number;
  count: number;
  prints: RealFlowPrint[];
};

export type RealBioJob = {
  id: string;
  status: "queued" | "running" | "done" | "error";
  done: number;
  total: number;
  pct: number;
  message: string;
  result: RealBioFlowResult | null;
  error: string | null;
  started_at: string;
};

export function startBioFlowJob(
  filters: FlowFilters = {}
): Promise<{ job_id: string; status: string; universe_size: number }> {
  const params = new URLSearchParams({ sector: "bio", async: "1" });
  if (filters.minPremium != null) params.set("min", String(filters.minPremium));
  if (filters.maxPrice != null) params.set("pmax", String(filters.maxPrice));
  if (filters.optionType) params.set("type", filters.optionType);
  if (filters.side) params.set("side", filters.side);
  if (filters.moneyness) params.set("money", filters.moneyness);
  return getJson(`/api/flow?${params.toString()}`);
}

export function fetchBioFlowJob(jobId: string, signal?: AbortSignal): Promise<RealBioJob> {
  return getJson<RealBioJob>(`/api/flow/job?id=${encodeURIComponent(jobId)}`, signal);
}

// Real query params + async-job shape confirmed from core/app.py's /api/scan handler and
// scanner.py's run_scan()/start_scan_job(). Strategies flash/flash_index/flash_agentic
// resolve their own universe server-side (no `tickers` needed); cluster_exp accepts
// "nearest" or a 0-based expiration index as a string.
export type ScanStrategy = "cipher" | "cluster" | "liquidity" | "flash" | "flash_index" | "flash_agentic";
export type ScanMode = "short" | "long" | "leap";

export type RealClusterSetup = {
  kind: "quad" | "triple" | "battle" | "golden" | "call_wall" | "put_floor" | string;
  label: string;
  strikes: number[];
  low: number;
  high: number;
  center: number;
  net_gex: number;
  strength: number;
  oi: number;
  side: "above" | "below" | string;
  peak_count: number;
  persistence?: number;
  persistence_ratio?: number;
  /** Per-strike weights, normalized so the heaviest strike in the cluster is 100.
   *  The real product's own capture exposes exactly this, and its displayed
   *  Strength is the sum of these weights. */
  levels?: { strike: number; weight: number }[];
  /** Highest-weighted strike — what the real product labels CLUSTER TARGET. */
  target_strike?: number;
  /** Sum of `levels` weights (the displayed Strength). */
  strength_norm?: number;
};

export type RealFlashComponents = {
  gex_fc: number;
  vex_fc: number;
  session: number;
  vp: number;
  vwap: number;
  momentum: number;
  touch: number;
  flow: number;
  atr: number;
};

export type RealFlashInfo = {
  score: number;
  targets: number[];
  first_target: number;
  push_target: number;
  stretch: number;
  invalidation: number;
  agent_state: "dormant" | "arming" | "triggered" | "target_1_hit" | "target_2_hit" | "completed" | string;
  trigger: number;
  trigger_kind?: "floor" | "ceiling" | null;
  trigger_proximity?: "at" | "nearing" | null;
  stretch_kind?: "floor" | "ceiling" | null;
  regime?: "pin" | "trend" | "mixed" | null;
  vwap?: number | null;
  vwap_side?: "above" | "below" | null;
  dte?: number | null;
  geometry_valid: boolean;
  actionable: boolean;
  reward_risk: number;
  components: RealFlashComponents;
  score_source: string;
  // Momentum-structure layer from the Obsidian EOD detector (core/obsidian_eod.py).
  setup?: string;
  setup_family?: string;
  event_timeline?: { age: string; event: string }[];
  latest_event?: string;
  coiling?: boolean;
  /** Local conviction composite — NOT the real product's Edge. See _edge_local(). */
  edge_local?: number | null;
  momentum_bias?: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
  collapse_pct?: number;
  eod_in_window?: boolean;
  eod_hot?: boolean;
};

export type RealScanCard = {
  ticker: string;
  spot: number;
  day_change_pct: number;
  score: number;
  /** For cluster strategy this is score_cluster_setup()'s hard-tier-weighted abs_score, not a dollar value. */
  strength: number;
  direction: "BULLISH" | "BEARISH";
  setup_type: string;
  state?: string;
  target: number;
  invalidation: number;
  reward_risk: number;
  reason: string;
  read: string;
  supports: number[];
  resistances: number[];
  pull_target: number;
  vacuum_targets: number[];
  vacuum_count?: number;
  call_wall?: number;
  put_wall?: number;
  /** Only present when strategy === "cluster" — the winning cluster + all candidate setups. */
  cluster?: RealClusterSetup;
  setups?: RealClusterSetup[];
  /** Only present when strategy is flash/flash_index/flash_agentic. */
  flash?: RealFlashInfo;
};

export type RealScanResult = {
  as_of: string;
  mode: string;
  strategy: string;
  universe_size: number;
  scanned: number;
  qualified: number;
  actionable: number;
  elapsed_ms: number;
  top: RealScanCard[];
};

export type RealScanJob = {
  id: string;
  status: "queued" | "running" | "done" | "error";
  done: number;
  total: number;
  pct: number;
  message: string;
  result: RealScanResult | null;
  /** Running leaderboard published mid-scan (core/scanner.py PARTIAL_EMIT_EVERY). */
  partial_top?: RealScanCard[];
  error: string | null;
  started_at: string;
};

export type ScanParams = {
  mode: ScanMode;
  strategy?: ScanStrategy;
  limit?: number;
  clusterExp?: string;
  tickers?: string[];
};

export function startScanJob(
  params: ScanParams,
  signal?: AbortSignal
): Promise<{ job_id: string; status: string; universe_size: number }> {
  const qp = new URLSearchParams({ mode: params.mode, async: "1" });
  if (params.strategy) qp.set("strategy", params.strategy);
  if (params.limit != null) qp.set("limit", String(params.limit));
  if (params.clusterExp) qp.set("cluster_exp", params.clusterExp);
  if (params.tickers?.length) qp.set("tickers", params.tickers.join(","));
  return getJson(`/api/scan?${qp.toString()}`, signal);
}

export function fetchScanJob(jobId: string, signal?: AbortSignal): Promise<RealScanJob> {
  return getJson<RealScanJob>(`/api/scan/job?id=${encodeURIComponent(jobId)}`, signal);
}

export type SavedScanEntry = {
  id: string;
  as_of: string;
  mode: string;
  strategy: string;
  universe_size: number;
  qualified: number;
  top_ticker: string | null;
  elapsed_ms: number;
};

// core/scan_history.py — every completed run_scan() is auto-saved locally (the real
// product does not persist scans server-side; this is our own addition).
export function listScanHistory(
  opts: { strategy?: ScanStrategy; limit?: number } = {},
  signal?: AbortSignal
): Promise<{ scans: SavedScanEntry[] }> {
  const qp = new URLSearchParams();
  if (opts.strategy) qp.set("strategy", opts.strategy);
  if (opts.limit != null) qp.set("limit", String(opts.limit));
  return getJson(`/api/scan/history?${qp.toString()}`, signal);
}

export function loadSavedScan(scanId: string, signal?: AbortSignal): Promise<RealScanResult> {
  return getJson<RealScanResult>(`/api/scan/history?id=${encodeURIComponent(scanId)}`, signal);
}

export type FlashAgenticEvent = { age: string; event: string };

export type FlashAgenticRow = {
  rank: number;
  state: string;
  ticker: string;
  bias: "BULLISH" | "BEARISH" | string;
  score: number | null;
  edge: number | null;
  setup: string;
  setup_family: string;
  regime: string;
  target_progress: string;
  /** Anchored episode with target extension (core/agentic_episodes.py). */
  episode?: {
    entry_price: number;
    original_target: number;
    target: number;
    extension_count: number;
    extensions: { at: string; from: number; to: number; spot: number }[];
    max_favorable: number;
    move_pct: number;
    state: string;
    close_reason: string | null;
    opened_at: string;
  } | null;
  target_extended?: boolean;
  latest_event: string;
  event_timeline: FlashAgenticEvent[];
  cipher_read: string;
  gamma_regime: string;
  vwap_state: string;
  spot: number | null;
  pivot: number | null;
  first_target: number | null;
  stretch: number | null;
  invalidation: number | null;
};

export type FlashAgenticLive = {
  loop_running: boolean;
  cycle: number | null;
  status_updated_at: string | null;
  captured_at: string | null;
  rows: FlashAgenticRow[];
  caveat: string;
};

// Served from data captured off the real AccessObsidian Flash Agentic panel by
// core/flash_agentic_live_loop.py (browser automation) — not locally computed.
export function fetchFlashAgenticLive(signal?: AbortSignal): Promise<FlashAgenticLive> {
  return getJson<FlashAgenticLive>("/api/flash-agentic/live", signal);
}

export type RealHealth = {
  status: string;
  service: string;
  market_data_configured: boolean;
  default_options_feed: string;
  default_stock_feed: string;
  read_only: boolean;
  as_of: string;
};

export type ContractTrade = {
  time: string;
  price: number;
  size: number;
  premium: number;
  side: "buy" | "sell" | "unknown";
  exchange: string;
};

export type ContractSearchResult = {
  ticker: string;
  found: boolean;
  /** Present only when found=false — listed strikes closest to the one requested. */
  nearest_strikes?: number[];
  message?: string;
  symbol?: string;
  strike?: number;
  type?: "call" | "put";
  expiration?: string;
  trade_date?: string;
  trades?: number;
  volume?: number;
  buy_volume?: number;
  sell_volume?: number;
  unclassified_volume?: number;
  buy_pct?: number | null;
  premium?: number;
  buy_premium?: number;
  sell_premium?: number;
  vwap?: number | null;
  open_interest?: number | null;
  bid?: number | null;
  ask?: number | null;
  last?: number | null;
  largest_trades?: ContractTrade[];
  expirations_available?: string[];
  /** How buy/sell was inferred — surfaced in the UI, not hidden. */
  method?: string;
  caveat?: string;
};

/** One contract's full day of trades, split buy vs sell by the tick rule. */
export function fetchContractSearch(
  params: { ticker: string; strike: string; type: "call" | "put"; date?: string },
  signal?: AbortSignal
): Promise<ContractSearchResult> {
  const q = new URLSearchParams({
    ticker: params.ticker,
    strike: params.strike,
    type: params.type,
  });
  if (params.date) q.set("date", params.date);
  return getJson<ContractSearchResult>(`/api/contract-search?${q.toString()}`, signal);
}

export type BacktestStats = {
  trades: number;
  win_rate: number;
  total_return_pct: number;
  avg_return_pct: number;
  median_return_pct: number;
  profit_factor: number | null;
  max_drawdown_pct: number;
  avg_bars_held: number;
  exit_mix?: Record<string, number>;
  by_setup?: Record<string, { n: number; avg_pct: number; win_rate: number }>;
};

export type BacktestPartition = {
  stats: BacktestStats;
  share_of_base: number;
  lift_vs_base_pp?: number;
  beats_control_range?: boolean;
  control?: { win_rate: number; avg_return_pct: number; profit_factor: number } | null;
  vs_control?: Record<string, number | null>;
  note?: string;
};

export type BacktestResultPayload = {
  mode: "filter" | "standalone";
  symbols: string[];
  timeframe: string;
  years: number;
  detector_mode: string;
  elapsed_ms: number;
  caveat?: string;
  /** filter mode */
  base?: BacktestStats;
  partitions?: Record<string, BacktestPartition>;
  lookback_bars?: number;
  /** standalone mode */
  stats?: BacktestStats;
  control?: {
    control?: { win_rate: number; avg_return_pct: number; profit_factor: number } | null;
    detector_minus_control?: Record<string, number | null>;
    detector_beats_control_range?: boolean;
  };
};

export type BacktestJob = {
  id: string;
  status: "queued" | "running" | "done" | "error";
  mode: string;
  symbols: string[];
  timeframe: string;
  detector_mode: string;
  pct: number;
  message: string;
  error: string | null;
  started_at: string;
  result: BacktestResultPayload | null;
};

export function startBacktest(params: {
  mode: "filter" | "standalone";
  symbols?: string;
  timeframe?: string;
  years?: number;
  detector?: string;
  lookback?: number;
}, signal?: AbortSignal): Promise<{ job_id: string; status: string }> {
  const q = new URLSearchParams({ action: "start", mode: params.mode });
  if (params.symbols) q.set("symbols", params.symbols);
  if (params.timeframe) q.set("timeframe", params.timeframe);
  if (params.years != null) q.set("years", String(params.years));
  if (params.detector) q.set("detector", params.detector);
  if (params.lookback != null) q.set("lookback", String(params.lookback));
  return getJson(`/api/signal-backtest?${q.toString()}`, signal);
}

export function fetchBacktestJob(jobId: string, signal?: AbortSignal): Promise<BacktestJob> {
  return getJson<BacktestJob>(`/api/signal-backtest?action=status&id=${encodeURIComponent(jobId)}`, signal);
}

export type EvidenceClock = {
  name: string;
  unlocks: string;
  have: number;
  need: number | null;
  unit: string;
  progress_pct: number | null;
  note?: string;
  latest_capture?: string | null;
  snapshots?: number;
  tickers?: number | { have: number; need: number | null };
  days?: { have: number; need: number | null };
  rows?: number;
  fitted_head_active?: boolean | null;
};

export type EvidenceStatus = {
  as_of: string;
  clocks: EvidenceClock[];
  parity: {
    name: string;
    measured?: boolean;
    source?: string;
    median_rel_err_pct?: Record<string, number>;
    tickers?: number | null;
    note?: string;
  };
  caveat: string;
};

/** Accrual progress on the questions that are waiting on data rather than code. */
export function fetchEvidenceStatus(signal?: AbortSignal): Promise<EvidenceStatus> {
  return getJson<EvidenceStatus>("/api/evidence-status", signal);
}

export type RealScanUniverse = {
  count: number;
  /** Optionable tickers, ordered by the backend's own liquidity/size ranking. */
  tickers: string[];
};

/** The scanner's optionable universe — also the source for the header's ticker suggestions. */
export function fetchScanUniverse(signal?: AbortSignal): Promise<RealScanUniverse> {
  return getJson<RealScanUniverse>("/api/scan/universe", signal);
}

export type RealResearchStatus = {
  initialized: boolean;
  read_only: boolean;
  /** True only if a live-execution path was detected. Expected to stay false. */
  live_execution_present: boolean;
  message?: string;
  as_of: string;
};

/** Operator/research status. Backs the Settings disclosure that Cipher has no execution authority. */
export function fetchResearchStatus(signal?: AbortSignal): Promise<RealResearchStatus> {
  return getJson<RealResearchStatus>("/api/research-status", signal);
}

export function fetchHealth(signal?: AbortSignal): Promise<RealHealth> {
  return getJson<RealHealth>(`/api/health`, signal);
}

export type RealWeightFitSummary = {
  as_of: string;
  n: number;
  r_squared: number;
  kendall_tau_rank: number;
  fit_mode: string;
  warnings: string[];
};

export type RealWeightLabStatus = {
  commercial_rows: number;
  flash_rows: number;
  feature_tickers: number;
  active: boolean;
  flash_active: boolean;
  weights_summary: RealWeightFitSummary | null;
  flash_weights_summary: RealWeightFitSummary | null;
};

export function fetchWeightLabStatus(signal?: AbortSignal): Promise<RealWeightLabStatus> {
  return getJson<RealWeightLabStatus>(`/api/weight-lab?action=status`, signal);
}
