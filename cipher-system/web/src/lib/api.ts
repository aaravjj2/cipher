// Client for cipher-system's real backend, proxied same-origin by app/server.mjs
// (see routes table in cipher-system/app/server.mjs — /api/* is forwarded to the
// Python core service). All types here mirror the ACTUAL JSON shapes returned by
// that service, confirmed via live curl against /api/quote and /api/matrix —
// distinct from the mock types in src/types/cipher.ts used by not-yet-wired panels.

import { coordinatedGet, invalidateRequests } from "@/lib/requestCache";

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
  call_gex: number | null;
  put_gex: number | null;
  net_gex: number | null;
  call_vex: number | null;
  put_vex: number | null;
  net_vex: number | null;
  call_oi: number | null;
  put_oi: number | null;
  volume: number | null;
  call_mid: number | null;
  put_mid: number | null;
  listed: boolean;
  oi_assumed_zero: boolean;
  // Provenance: gamma reconstructed via the implied-vol solver rather than taken from
  // the feed, and/or open interest substituted by session volume. See core/exposure.py.
  gamma_modeled?: boolean;
  /** Gamma solved from a mid at the minimum tick — unbiased but individually noisy. */
  iv_min_tick?: boolean;
  oi_from_volume?: boolean;
  available: boolean;
  gex_available?: boolean;
  vex_available?: boolean;
  call_listed?: boolean;
  put_listed?: boolean;
  call_gex_available?: boolean;
  put_gex_available?: boolean;
  call_vex_available?: boolean;
  put_vex_available?: boolean;
  call_oi_available?: boolean;
  put_oi_available?: boolean;
  oi_available?: boolean;
  volume_available?: boolean;
  call_mid_available?: boolean;
  put_mid_available?: boolean;
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
  /** Where open interest came from, and the session it is dated to. GEX is gamma x OI,
   *  so an OI date older than today means the exposure surface is that stale. */
  open_interest_source?: string;
  open_interest_as_of?: string | null;
};

export type EvidenceSnapshot = {
  schema_version: 1;
  snapshot_id: string;
  ticker: string;
  view: "setup_scanner" | "night_vision" | string;
  event_at: string | null;
  captured_at: string;
  provider: string;
  feed: string;
  spot: number | null;
  session: { timezone: "America/New_York"; market_date: string | null; phase: string };
  freshness: { status: "current" | "stale" | "unknown"; age_seconds: number | null };
  coverage: {
    status: "sufficient" | "limited" | "unknown";
    calculated_cells: number | null;
    listed_cells: number | null;
    contracts: number | null;
    open_interest_as_of?: string | null;
    open_interest_source?: string | null;
  };
  levels: {
    exposure: Array<{ kind: string; label: string; price: number; origin: string }>;
    session: Array<{ kind: string; label: string; price: number; origin: string }>;
  };
  missing_reasons: string[];
  caveats: string[];
  replay_available?: boolean;
  read_only: true;
  execution_capability: false;
};

export type RealMatrixResponse = {
  ticker: string;
  as_of: string;
  feed: string;
  quote: RealQuote;
  depth_points: number | null;
  depth_mode: string;
  expirations: string[];
  total_expirations_available: number;
  rows: RealMatrixRow[];
  coverage: RealMatrixCoverage;
  summary: {
    global_max_strike: number | null;
    call_wall_strike: number | null;
    put_wall_strike: number | null;
    gamma_flip_level: number | null;
    gamma_flip_candidates?: number[];
    gamma_flip_reference?: string;
  };
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
  return coordinatedGet(path, async () => {
    const res = await fetch(path, { cache: "no-store" });
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
  }, signal);
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    cache: "no-store",
    signal,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    let readOnly = false;
    try {
      const errBody = await res.json();
      detail = errBody?.error || "";
      readOnly = Boolean(errBody?.read_only);
    } catch {
      /* body wasn't JSON — fall back to status text below */
    }
    throw new ApiError(detail || `Request failed (${res.status})`, res.status, readOnly);
  }
  const result = await res.json() as T;
  invalidateRequests();
  return result;
}

export function fetchQuote(ticker: string, signal?: AbortSignal): Promise<RealQuote> {
  return getJson<RealQuote>(`/api/quote?symbol=${encodeURIComponent(ticker)}`, signal);
}

export type FreshnessItem = {
  name: string;
  observed_at: string | null;
  age_seconds: number | null;
  state: "current" | "last_session" | "stale" | "unavailable";
  source: string;
  detail?: string | null;
};

export type ProductStatus = {
  generated_at: string;
  ticker: string;
  session: {
    phase: "premarket" | "regular" | "postmarket" | "closed";
    is_regular: boolean;
    market_date: string;
    exchange_time: string;
    timezone: string;
  };
  items: FreshnessItem[];
  exceptions: FreshnessItem[];
  healthy: boolean;
  read_only: true;
};

export function fetchProductStatus(ticker: string, signal?: AbortSignal): Promise<ProductStatus> {
  return getJson<ProductStatus>(`/api/product-status?symbol=${encodeURIComponent(ticker)}`, signal);
}

export type PaperPortfolioSummary = {
  portfolio_id: string;
  strategy: string;
  starting_cash: number;
  realized_equity: number;
  realized_pnl: number;
  marked_equity: number;
  liquidation_equity: number;
  unrealized_pnl_mid: number;
  liquidation_pnl: number;
  daily_realized_pnl: number;
  daily_closed_trades: number;
  daily_wins: number;
  daily_losses: number;
  daily_entries: number;
  closed_trades: number;
  wins: number;
  open_positions: number;
  config: Record<string, unknown>;
  positions: Array<Record<string, unknown>>;
  signals: Array<Record<string, unknown>>;
  equity_curve: Array<{ at: string; equity: number }>;
  opportunity_summary: OpportunitySummary;
  risk_state: {
    daily_loss_locked: boolean;
    entry_window_open: boolean;
    stale_open_marks: number;
    unavailable_open_marks: number;
    new_entries_allowed: boolean;
  };
};

export type OpportunitySummary = {
  signals: number;
  resolved: number;
  tracking: number;
  targets: number;
  invalidations: number;
  session_expired: number;
  skipped_targets: number;
  skipped_invalidations: number;
  scope: "underlying_path_counterfactual";
};

export type PaperPortfoliosResponse = {
  as_of: string | null;
  paper_only: true;
  read_only: true;
  execution_capability: false;
  portfolio_count: number;
  combined_starting_cash: number;
  combined_equity: number;
  combined_realized_pnl: number;
  combined_marked_equity: number;
  combined_liquidation_equity: number;
  combined_unrealized_pnl_mid: number;
  combined_liquidation_pnl: number;
  daily_realized_pnl: number;
  opportunity_summary: OpportunitySummary;
  normalized_comparison: {
    method: string;
    minimum_sample: number;
    ranked: false;
    rows: Array<{
      portfolio_id: string; strategy: string; closed_sample: number; minimum_sample: number;
      sample_status: "TINY" | "EARLY" | "USABLE"; win_rate: number | null;
      average_option_return_pct: number | null; profit_factor_on_return_units: number | null;
      rank_eligible: boolean;
    }>;
    caveat: string;
  };
  portfolios: PaperPortfolioSummary[];
  runs: Array<Record<string, unknown>>;
  caveat: string;
};

export function fetchPaperPortfolios(signal?: AbortSignal): Promise<PaperPortfoliosResponse> {
  return getJson<PaperPortfoliosResponse>("/api/paper-portfolios", signal);
}

export type ProspectiveProgram = {
  program_id: string;
  name: string;
  kind: string;
  configuration_sha256: string;
  effective_status: "REGISTERED" | "MONITORING" | "COLLECTING" | "COMPLETED";
  starts_at: string;
  ends_at: string | null;
  minimum_sample: number;
  signals: number;
  eligible_signals: number;
  open_signals: number;
  closed_signals: number;
  void_signals: number;
  wins: number;
  average_underlying_return_pct: number | null;
  option_legs: number;
  open_option_legs: number;
  void_option_legs: number;
  closed_option_pnl: number;
  open_option_mark_pnl: number;
  open_option_liquidation_pnl: number;
  sample_progress: number;
  execution_authority: false;
};

export type ProspectiveFronttestsResponse = {
  as_of: string | null;
  paper_only: true;
  read_only: true;
  execution_capability: false;
  programs: ProspectiveProgram[];
  signals: Array<Record<string, unknown>>;
  option_legs: Array<Record<string, unknown>>;
  observations: Array<Record<string, unknown>>;
  latest_coverage: {
    run_id: number | null;
    observed: number;
    fresh: number;
    partial: number;
    stale: number;
    missing: number;
    signals_opened: number;
  };
  open_option_mark_pnl: number;
  open_option_liquidation_pnl: number;
  option_liquidity_policy: { maximum_entry_spread_pct: number; missing_quote_is_unavailable: boolean };
  runs: Array<Record<string, unknown>>;
  caveat: string;
};

export function fetchProspectiveFronttests(signal?: AbortSignal): Promise<ProspectiveFronttestsResponse> {
  return getJson<ProspectiveFronttestsResponse>("/api/prospective-fronttests", signal);
}

export type MorningBriefResponse = {
  generated_at: string;
  ticker: string;
  session: ProductStatus["session"];
  freshness: ProductStatus;
  market: Array<{
    ticker: string; price: number | null; day_change_pct: number | null;
    as_of: string | null; feed: string; availability?: RealFlowResponse["availability"];
  }>;
  recent_scans: Array<{ id: string; as_of: string; strategy: string; qualified: number; top_ticker?: string | null }>;
  significant_flow: {
    ticker: string;
    as_of?: string | null;
    source?: string;
    session_date?: string | null;
    prints: RealFlowPrint[];
    caveat?: string;
    freshness?: RealFlowResponse["freshness"];
    availability?: RealFlowResponse["availability"];
    coverage?: RealFlowResponse["coverage"];
  };
  alerts: { rules: Array<Record<string, unknown>> };
  gex_change?: {
    ticker: string; available: boolean; change?: number | null;
    current?: { captured_at: string; net_gex: number | null } | null;
    prior?: { captured_at: string; net_gex: number | null } | null;
    caveat?: string;
  };
  holdings?: {
    positions?: Array<Record<string, unknown>>;
    open?: Array<Record<string, unknown>>;
    summary?: Record<string, number | null>;
    unresolved?: string[];
  };
  paper_portfolios: {
    as_of?: string | null;
    combined_equity?: number | null;
    combined_marked_equity?: number | null;
    combined_liquidation_equity?: number | null;
    combined_unrealized_pnl_mid?: number | null;
    daily_realized_pnl?: number | null;
    combined_realized_pnl?: number | null;
    portfolios: Array<Pick<PaperPortfolioSummary, "portfolio_id" | "strategy" | "realized_equity" | "realized_pnl" | "marked_equity" | "liquidation_equity" | "unrealized_pnl_mid" | "closed_trades" | "wins" | "open_positions" | "risk_state">>;
  };
  prospective_fronttests: {
    as_of?: string | null;
    latest_coverage: ProspectiveFronttestsResponse["latest_coverage"];
    programs: Array<Pick<ProspectiveProgram, "program_id" | "name" | "kind" | "effective_status" | "minimum_sample" | "eligible_signals" | "open_signals" | "closed_signals" | "void_signals" | "wins" | "sample_progress" | "closed_option_pnl">>;
    open_signals: Array<{
      signal_id: string; program_id: string; ticker: string; setup_id: string;
      direction: string; signal_bar_at: string; underlying_entry: number;
      target?: number | null; deadline_at?: string | null; option_selection_status?: string | null;
    }>;
    latest_observations: Array<{
      program_id: string; ticker: string; observed_at: string; latest_bar_at?: string | null;
      coverage_status: string; decision: string; reason: string;
    }>;
    paper_only: true;
    execution_capability: false;
  };
  attention: Array<{
    severity: "warning" | "error"; kind: string; title: string;
    detail: string; ticker?: string | null;
  }>;
  exceptions: Array<FreshnessItem | { section: string; ticker?: string; error: string }>;
  read_only: true;
};

export function fetchMorningBrief(ticker: string, signal?: AbortSignal): Promise<MorningBriefResponse> {
  return getJson<MorningBriefResponse>(`/api/morning-brief?symbol=${encodeURIComponent(ticker)}`, signal);
}

export type ResearchCandidate = {
  rank: number;
  ticker: string;
  horizon: "intraday" | "weekly";
  observed: {
    spot: number | null; day_change_pct: number | null; feed?: string | null;
    scanner_as_of?: string | null; supports: number[]; resistances: number[];
    target: number | null; invalidation: number | null;
    coverage: { status: "sufficient" | "limited" | "unknown"; calculated_cells: number | null; contracts: number | null };
  };
  derived: {
    ranking_score: number | null; direction: "BULLISH" | "BEARISH" | "NEUTRAL";
    setup_type?: string | null; reward_risk: number | null;
    confidence: "higher" | "developing" | "insufficient";
    eligible_for_deeper_review: boolean; research_template: string;
    thesis?: string | null; blockers: string[];
  };
  disclaimer: string;
};

export type ResearchDeskResponse = {
  available: boolean;
  schema_version?: number;
  generated_at: string | null;
  session_timezone?: string;
  universe?: string[];
  discovery?: { status?: string; symbols?: string[]; generated_at?: string; caveat?: string };
  source_timestamps?: string[];
  candidates: { intraday: ResearchCandidate[]; weekly: ResearchCandidate[] };
  scan_summary?: Record<string, Record<string, number | null>>;
  errors: Array<{ scope?: string; ticker?: string; error: string }>;
  method?: string;
  execution_boundary?: { read_only: boolean; paper_only: boolean; live_order_authority: false; allowed_output: string };
  message?: string;
};

export function fetchResearchDesk(signal?: AbortSignal): Promise<ResearchDeskResponse> {
  return getJson<ResearchDeskResponse>("/api/research-desk", signal);
}

export type FinvizDiscovery = { generated_at: string; symbols: string[]; read_only: true; requires_alpaca_validation: true; caveat: string; screens: Array<{ preset: string; status: string; observed_at: string; delayed: true; rows: Array<{ ticker: string }> }> };
export function fetchFinvizDiscovery(signal?: AbortSignal): Promise<FinvizDiscovery> {
  return getJson<FinvizDiscovery>("/api/finviz-discovery", signal);
}

export type OptionTerminalContract = {
  symbol: string; type: "call" | "put"; strike: number; expiry: string;
  bid: number | null; ask: number | null; mid: number | null;
  buy_price: number | null; sell_price: number | null;
  spread_pct: number | null; quote_time: string | null; quote_age_seconds: number | null;
  last: number | null; volume: number | null; open_interest: number | null;
  open_interest_date: string | null; iv: number | null; delta: number | null;
  gamma: number | null; theta: number | null; vega: number | null; rho: number | null;
  moneyness: "ITM" | "ATM" | "OTM"; distance_pct: number | null;
  liquidity_flags: string[]; liquid: boolean | null; feed: string;
};

export type OptionsChainResponse = {
  ticker: string; spot: number; spot_as_of: string; generated_at: string; as_of: string | null;
  feed: string; iv_rank: number | null; iv_percentile: number | null; iv_history_status: string;
  sessions?: number; minimum_sessions?: number; current_atm_iv?: number | null; current_skew_25d?: number | null; current_term_slope?: number | null;
  readiness?: "COLLECTING" | "PROVISIONAL" | "USABLE" | "ESTABLISHED" | "MATURE"; metric?: string; methodology_version?: string | null; iv_30d_quality?: string | null;
  coverage?: { iv: number; oi: number; quotes: number } | null; caveat?: string;
  expirations: Array<{ expiration: string; dte: number; expected_move: number | null; expected_move_pct: number | null; put_call_25d_skew: number | null; rows: Array<{ strike: number; call: OptionTerminalContract | null; put: OptionTerminalContract | null }> }>;
  term_structure: Array<{ expiration: string; dte: number; atm_strike: number | null; atm_iv: number | null; expected_move: number | null }>;
  open_interest_caveat: string; read_only: true;
};

export function fetchOptionsChain(ticker: string, expirationCount = 6, signal?: AbortSignal): Promise<OptionsChainResponse> {
  const params = new URLSearchParams({ symbol: ticker, expirations: String(expirationCount) });
  return getJson<OptionsChainResponse>(`/api/options-chain?${params.toString()}`, signal);
}

export type BuilderLeg = Omit<Partial<OptionTerminalContract>, "type"> & {
  contract?: string; type: "call" | "put" | "stock"; side: "buy" | "sell";
  quantity: number; strike?: number; expiration?: string; entry_price?: number;
};

export type OptionsBuilderResponse = {
  ticker: string; spot: number; legs: BuilderLeg[]; net_debit: number; net_credit: number;
  max_profit: number | null; max_loss: number | null; max_profit_unbounded: boolean;
  max_loss_unbounded: boolean; breakevens: number[]; aggregate_greeks: Record<string, number | null>;
  risk_per_structure: number | null; liquidity_warnings: string[]; ex_dividend_warning: boolean;
  payoff: Array<{ underlying: number; pnl: number }>;
  same_expiration: boolean; calendar_caveat: string | null;
  assignment_warning: boolean; research_only: true; execution_capability: false;
};

export function analyzeOptionsStructure(ticker: string, spot: number, legs: BuilderLeg[], signal?: AbortSignal): Promise<OptionsBuilderResponse> {
  return postJson<OptionsBuilderResponse>("/api/options-builder", { ticker, spot, legs }, signal);
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
  signal?: AbortSignal,
  opts?: { start?: string; limit?: number }
): Promise<RealBarsResponse> {
  const params = new URLSearchParams({ symbol: ticker, timeframe });
  if (opts?.start) params.set("start", opts.start);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  return getJson<RealBarsResponse>(`/api/bars?${params.toString()}`, signal);
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

/** All four levels are null when nothing supports them: an empty or fully unavailable
 *  profile, a chain with no positive call gamma, or a net profile that never crosses zero.
 *  They were typed as `number`, which asserted a value the API does not promise. */
export type RealNightVisionSummary = {
  global_max_strike: number | null;
  call_wall_strike: number | null;
  put_wall_strike: number | null;
  gamma_flip_level: number | null;
  /** Every zero crossing in the net profile, ascending. `gamma_flip_level` is the one
   *  nearest spot; a long list means the profile oscillates and the single level is weak
   *  evidence rather than a clean regime boundary. */
  gamma_flip_candidates?: number[];
  /** Which rule chose the flip: "nearest_spot", or "nearest_dominant_strike" when the
   *  caller supplied no spot. */
  gamma_flip_reference?: string | null;
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
  /** Live provider state; stale_cache is explicit and never treated as current. */
  data_status?: "live" | "stale_cache";
  provider_error?: string;
  cache_note?: string;
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
  evidence_snapshot: EvidenceSnapshot;
  replay?: {
    mode: "frozen";
    snapshot_id: string;
    event_at: string | null;
    captured_at: string;
    exposure_frozen: true;
    session_levels_captured: boolean;
    integrity?: {
      snapshot_identity: "verified";
      matrix_checksum: "verified" | "legacy_unavailable";
      matrix_sha256: string;
    };
    read_only: true;
    execution_capability: false;
  };
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

export function fetchNightVisionReplay(
  ticker: string,
  snapshotId: string,
  signal?: AbortSignal
): Promise<RealNightVisionResponse> {
  const params = new URLSearchParams({ symbol: ticker, id: snapshotId });
  return getJson<RealNightVisionResponse>(`/api/night-vision-replay?${params.toString()}`, signal);
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
  side: "buy" | "sell" | "unknown";
  side_basis?: "event_time_bid_ask" | "snapshot_bid_ask" | "unclassified";
  tier: string;
  otm_pct: number;
  exchange: string;
  feed: string;
  captured_at?: string;
  session_date?: string | null;
};

export type RealFlowResponse = {
  ticker: string;
  as_of: string | null;
  generated_at?: string;
  oldest_event_at?: string | null;
  newest_event_at?: string | null;
  event_age_seconds?: number | null;
  freshness?: { status: "current" | "stale" | "unknown"; age_seconds: number | null };
  session_date?: string | null;
  source?: "tradier_stream" | "alpaca_chain_snapshot" | string;
  capture_mode?: "event_timesales" | "latest_trade_per_contract" | string;
  caveat?: string;
  coverage?: {
    status?: "complete" | "partial" | "stale" | "missing" | "unknown";
    scope?: string;
    captured_events?: number;
    captured_contracts?: number;
    contracts_with_matching_session?: number;
  };
  feed: string;
  quote: RealQuote | null;
  min_premium: number;
  count: number;
  prints: RealFlowPrint[];
  availability?: {
    status: "refreshing" | "unavailable" | "available";
    reason?: "refresh_pending" | "provider_error" | string;
    response_budget_seconds?: number;
    retry_after_seconds?: number;
    detail?: string | null;
  };
  read_only?: boolean;
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
  rank_eligible?: boolean;
  confidence?: "higher" | "developing" | "insufficient";
  coverage_status?: "sufficient" | "limited" | "unknown";
  quality_reasons?: string[];
  confidence_caveat?: string;
  coverage_cells?: number | null;
  contracts?: number | null;
  feed?: string | null;
  evidence_snapshot?: EvidenceSnapshot;
};

export type RealScanResult = {
  as_of: string;
  evidence_schema_version?: number;
  evidence_snapshot_ids?: string[];
  mode: string;
  strategy: string;
  universe_size: number;
  scanned: number;
  qualified: number;
  rejected?: number;
  failed?: number;
  rejection_counts?: Record<string, number>;
  rejected_examples?: Array<{ ticker: string; reasons: string[]; evidence_snapshot?: EvidenceSnapshot | null }>;
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
  uncertainty?: BacktestUncertainty;
  serial_uncertainty?: BacktestUncertainty;
  portfolio?: BacktestPortfolio;
  trade_ledger?: Array<Record<string, unknown>>;
  experiment_id?: string;
  run_id?: string;
  artifacts?: { input_snapshot: string; report: string };
  manifest?: BacktestManifest;
  validation?: {
    status: "eligible" | "insufficient_holdout";
    blocker?: string;
    train?: BacktestEvaluation;
    holdout?: BacktestEvaluation;
  };
};

export type BacktestUncertainty = {
  method: string;
  n: number;
  repeats?: number;
  seed?: number;
  block_length?: number;
  interval: [number, number] | null;
  contains_zero?: boolean;
  blocker?: string | null;
};

export type BacktestEvaluation = {
  stats?: BacktestStats;
  base?: BacktestStats;
  partitions?: Record<string, BacktestPartition>;
  control?: BacktestResultPayload["control"];
  uncertainty?: BacktestUncertainty;
  serial_uncertainty?: BacktestUncertainty;
  portfolio?: BacktestPortfolio;
  trade_ledger?: Array<Record<string, unknown>>;
  error?: string;
};

export type BacktestPortfolio = {
  starting_equity: number;
  ending_equity: number;
  profit_loss: number;
  return_pct: number;
  max_drawdown_pct: number;
  trades_taken: number;
  trades_skipped_at_capacity: number;
  max_concurrent_positions: number;
  position_fraction: number;
  note: string;
};

export type BacktestManifest = {
  schema_version: string;
  experiment_id: string;
  run_id: string;
  data_fingerprint: string;
  generated_at: string;
  validation_status: "eligible" | "insufficient_holdout";
  validation_eligible_symbols: string[];
  blocked_symbols: string[];
  live_order_authority: false;
  promotion_authority: "RESEARCH_ONLY";
  artifacts?: { input_snapshot: string; report: string };
  spec: {
    cost_model: { slippage_bps_per_side: number; commission_bps_per_side: number; total_bps_per_side: number; round_trip_bps: number; source: string };
    validation: { holdout_fraction: number; embargo_bars: number; seed: number; method: string };
    engine_rules: Record<string, string | boolean>;
    parameters: Record<string, number>;
  };
  data_coverage: Record<string, {
    all: { bars: number; first: string | null; last: string | null };
    train: { bars: number; first: string | null; last: string | null };
    holdout: { bars: number; first: string | null; last: string | null };
    eligible: boolean;
    blocker?: string | null;
  }>;
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
  costBps?: number;
  commissionBps?: number;
  holdout?: number;
  seed?: number;
}, signal?: AbortSignal): Promise<{ job_id: string; status: string }> {
  const q = new URLSearchParams({ action: "start", mode: params.mode });
  if (params.symbols) q.set("symbols", params.symbols);
  if (params.timeframe) q.set("timeframe", params.timeframe);
  if (params.years != null) q.set("years", String(params.years));
  if (params.detector) q.set("detector", params.detector);
  if (params.lookback != null) q.set("lookback", String(params.lookback));
  if (params.costBps != null) q.set("slippage_bps", String(params.costBps));
  if (params.commissionBps != null) q.set("commission_bps", String(params.commissionBps));
  if (params.holdout != null) q.set("holdout", String(params.holdout));
  if (params.seed != null) q.set("seed", String(params.seed));
  return getJson(`/api/signal-backtest?${q.toString()}`, signal);
}

export function fetchBacktestJob(jobId: string, signal?: AbortSignal): Promise<BacktestJob> {
  return getJson<BacktestJob>(`/api/signal-backtest?action=status&id=${encodeURIComponent(jobId)}`, signal);
}

/**
 * The strategy catalog. One route replaced five that each ranked strategies with
 * their own scoring half — those halves charged no transaction cost, truncated
 * chronologically, or read today's open interest while trading past bars, and none
 * of them was proxied to the browser. What reaches the UI now is a verdict against
 * a matched random-entry control, or an explicit statement that a strategy cannot
 * be measured and why.
 */
export type CatalogStrategy = {
  strategy_id: string;
  name: string;
  family: string;
  source: string;
  data_requirement: string;
  bar_timeframe: string;
  evaluable: boolean;
  blocked_reason: string | null;
};

export type StrategyCatalog = {
  summary: {
    total: number;
    evaluable: number;
    blocked: number;
    families: Record<string, { total: number; evaluable: number }>;
  };
  standard: string;
  strategies: CatalogStrategy[];
};

export type StrategyVerdict = {
  strategy_id: string;
  name: string;
  family: string;
  source: string;
  data_requirement: string;
  verdict: string;
  reason: string;
  metrics: Record<string, number> | null;
  beats_control_range?: boolean;
  walk_forward_passed?: boolean;
  accrual?: string;
  bar_timeframe?: string;
};

export type StrategyJob = {
  id: string;
  status: "queued" | "running" | "done" | "error";
  pct: number;
  message: string;
  error: string | null;
  result: {
    verdicts: Record<string, number>;
    results: StrategyVerdict[];
    standard: string;
    cost_source?: string;
    timeframe?: string | null;
    elapsed_ms?: number;
  } | null;
};

export function fetchStrategyCatalog(signal?: AbortSignal): Promise<StrategyCatalog> {
  return getJson<StrategyCatalog>("/api/strategies?action=list", signal);
}

export function startStrategyEvaluation(params: {
  family?: string;
  strategyIds?: string;
  symbols?: string;
  timeframe?: string;
  years?: number;
  repeats?: number;
}, signal?: AbortSignal): Promise<{ job_id: string; status: string }> {
  const q = new URLSearchParams({ action: "evaluate" });
  if (params.family) q.set("family", params.family);
  if (params.strategyIds) q.set("strategy_ids", params.strategyIds);
  if (params.symbols) q.set("symbols", params.symbols);
  if (params.timeframe) q.set("timeframe", params.timeframe);
  if (params.years != null) q.set("years", String(params.years));
  if (params.repeats != null) q.set("repeats", String(params.repeats));
  return getJson(`/api/strategies?${q.toString()}`, signal);
}

export function fetchStrategyJob(jobId: string, signal?: AbortSignal): Promise<StrategyJob> {
  return getJson<StrategyJob>(`/api/strategies?action=status&id=${encodeURIComponent(jobId)}`, signal);
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

export type StandingProspectiveRegistration = {
  prospective_test_id: string;
  strategy_id: string;
  name: string;
  status: string;
  minimum_sample: number;
  scored_count: number;
  progress_pct: number | null;
  created_at: string;
  updated_at: string;
};

export type StandingShadowPosition = {
  id: string;
  ticker: string;
  direction: string;
  symbol: string;
  quantity: number;
  entry_price: number;
  opened_at: string;
  status: string;
};

export type StandingStatus = {
  as_of: string;
  read_only: boolean;
  prospective_registrations: StandingProspectiveRegistration[];
  shadow_positions: StandingShadowPosition[];
  clocks: EvidenceClock[];
};

/** Open commitments (prospective registrations, shadow positions) and accrual clocks. */
export function fetchStanding(signal?: AbortSignal): Promise<StandingStatus> {
  return getJson<StandingStatus>("/api/standing", signal);
}

// Holdings — manually-entered positions (core/holdings.py), never connected to a real
// brokerage or exchange account. Marked to market with the same quote()/bars() every
// other panel already uses; the position facts (shares, entry price/date) are the
// user's own ground truth, not fabricated.

export type HoldingPosition = {
  id: string;
  ticker: string;
  shares: number;
  entry_price: number;
  entry_date: string;
  status: "OPEN";
  notes: string | null;
  exit_price: null;
  exit_date: null;
  closed_from_id: string | null;
  created_at: string;
  updated_at: string;
  cost_basis: number;
  current_price: number | null;
  price_as_of: string | null;
  market_value: number | null;
  unrealized_pnl_dollars: number | null;
  unrealized_pnl_pct: number | null;
  day_change_dollars: number | null;
  quote_error: string | null;
};

export type ClosedHoldingPosition = {
  id: string;
  ticker: string;
  shares: number;
  entry_price: number;
  entry_date: string;
  status: "CLOSED";
  notes: string | null;
  exit_price: number;
  exit_date: string;
  closed_from_id: string | null;
  created_at: string;
  updated_at: string;
  cost_basis: number;
  proceeds: number;
  realized_pnl_dollars: number;
  realized_pnl_pct: number | null;
};

export type HoldingsAllocationRow = { ticker: string; market_value: number; weight_pct: number };

export type HoldingsBenchmarkComparison = {
  ticker: string;
  hypothetical_value: number;
  hypothetical_pnl_dollars: number;
  hypothetical_pnl_pct: number | null;
};

export type HoldingsStatus = {
  as_of: string;
  read_only: boolean;
  caveat: string;
  open_positions: HoldingPosition[];
  closed_positions: ClosedHoldingPosition[];
  summary: {
    open_position_count: number;
    closed_position_count: number;
    total_cost_basis_open: number;
    total_market_value_open: number;
    unresolved_tickers: string[];
    total_unrealized_pnl_dollars: number;
    total_unrealized_pnl_pct: number | null;
    total_day_change_dollars: number;
    total_realized_pnl_dollars: number;
  };
  allocation: HoldingsAllocationRow[];
  benchmark: {
    since: string;
    actual_market_value: number;
    comparisons: HoldingsBenchmarkComparison[];
  } | null;
};

export function fetchHoldings(opts?: { benchmark?: boolean }, signal?: AbortSignal): Promise<HoldingsStatus> {
  const q = opts?.benchmark ? "?benchmark=1" : "";
  return getJson<HoldingsStatus>(`/api/holdings${q}`, signal);
}

export function addHolding(
  input: { ticker: string; shares: number; entry_price: number; entry_date: string; notes?: string },
  signal?: AbortSignal
): Promise<HoldingPosition> {
  return postJson<HoldingPosition>("/api/holdings?action=add", input, signal);
}

export function closeHolding(
  input: { id: string; exit_price: number; exit_date: string; shares?: number },
  signal?: AbortSignal
): Promise<HoldingPosition | ClosedHoldingPosition> {
  return postJson<HoldingPosition | ClosedHoldingPosition>("/api/holdings?action=close", input, signal);
}

export function deleteHolding(id: string, signal?: AbortSignal): Promise<{ deleted: true; id: string }> {
  return postJson(`/api/holdings?action=delete`, { id }, signal);
}

export type PortfolioRiskPosition = {
  id: string; strategy: string; asset_type: "stock" | "option"; ticker: string;
  contract_symbol: string | null; option_type: "call" | "put" | null; strike: number | null;
  expiration: string | null; quantity: number; entry_price: number; fees: number;
  current_mark: number | null; mark_as_of: string | null; mark_source: string | null;
  market_value: number | null; unrealized_pnl: number | null;
  greeks: Record<"delta" | "gamma" | "theta" | "vega" | "rho", number | null>;
};
export type PortfolioRiskStatus = {
  as_of: string; cash: number; positions: PortfolioRiskPosition[];
  summary: { position_count: number; signed_cost_basis: number; signed_market_value: number;
    marked_gross_value: number; unrealized_pnl: number; net_liquidating_value: number;
    aggregate_greeks: Record<"delta" | "gamma" | "theta" | "vega" | "rho", number | null> };
  concentration: Array<{ ticker: string; delta_dollars: number; weight_pct: number | null }>;
  expiration_calendar: Array<{ expiration: string; positions: number; short_contracts: number; tickers: string[] }>;
  strategy_groups: Array<{ name: string; position_ids: string[] }>;
  exceptions: Array<{ position_id?: string; ticker: string; kind: string; detail: string }>;
  caveat: string; execution_capability: false;
};
export type PortfolioRiskInput = { strategy?: string; asset_type: "stock" | "option"; ticker: string;
  contract_symbol?: string; option_type?: "call" | "put"; strike?: number; expiration?: string;
  quantity: number; entry_price: number; fees?: number; opened_at?: string; notes?: string };
export const fetchPortfolioRisk = (signal?: AbortSignal) => getJson<PortfolioRiskStatus>("/api/portfolio-risk", signal);
export const addPortfolioRiskPosition = (position: PortfolioRiskInput, signal?: AbortSignal) => postJson<PortfolioRiskPosition>("/api/portfolio-risk?action=add", position, signal);
export const deletePortfolioRiskPosition = (id: string, signal?: AbortSignal) => postJson<{ deleted: string }>("/api/portfolio-risk?action=delete", { id }, signal);
export const setPortfolioRiskCash = (cash: number, signal?: AbortSignal) => postJson<{ cash: number }>("/api/portfolio-risk?action=cash", { cash }, signal);
export const exportPortfolioRiskCsv = (signal?: AbortSignal) => getJson<{ filename: string; csv: string }>("/api/portfolio-risk?action=export", signal);
export const importPortfolioRiskCsv = (csv: string, replace = false, signal?: AbortSignal) => postJson<{ imported: number; replaced: boolean }>("/api/portfolio-risk?action=import", { csv, replace }, signal);

export type ServerWatchlist = { id: string; name: string; tickers: string[]; created_at: string; updated_at: string };
export type SavedScreen = { id: string; name: string; watchlist_id: string | null; criteria: Record<string, number | boolean>; created_at: string; updated_at: string };
export type WatchlistsPayload = { watchlists: ServerWatchlist[]; screens: SavedScreen[]; server_side: true; execution_capability: false };
export const fetchWatchlists = (signal?: AbortSignal) => getJson<WatchlistsPayload>("/api/watchlists", signal);
export const createWatchlist = (name: string, signal?: AbortSignal) => postJson<ServerWatchlist>("/api/watchlists?action=create", { name }, signal);
export const addWatchlistMember = (watchlist_id: string, ticker: string, signal?: AbortSignal) => postJson("/api/watchlists?action=add", { watchlist_id, ticker }, signal);
export const removeWatchlistMember = (watchlist_id: string, ticker: string, signal?: AbortSignal) => postJson("/api/watchlists?action=remove", { watchlist_id, ticker }, signal);
export const deleteServerWatchlist = (id: string, signal?: AbortSignal) => postJson("/api/watchlists?action=delete", { id }, signal);
export const saveScreen = (input: { name: string; watchlist_id?: string; criteria: Record<string, number | boolean> }, signal?: AbortSignal) => postJson<SavedScreen>("/api/watchlists?action=save-screen", input, signal);
export const deleteSavedScreen = (id: string, signal?: AbortSignal) => postJson("/api/watchlists?action=delete-screen", { id }, signal);
export type ScreenResult = { id: string; name: string; criteria: Record<string, number | boolean>; evaluated: number; matches: Array<{ ticker: string; price: number; day_change_pct: number; scanner_score: number | null; as_of: string }>; errors: Array<{ ticker: string; error: string }>; generated_at: string; reproducible_inputs: { watchlist_id: string | null; tickers: string[] } };
export const runSavedScreen = (id: string, signal?: AbortSignal) => getJson<ScreenResult>(`/api/screens?id=${encodeURIComponent(id)}`, signal);

export type JournalOptionLeg = { contract_symbol: string; side: "buy" | "sell"; quantity: number; multiplier: number; entry_mark: number | null; entry_mark_type: string };
export type JournalEntry = { id: string; ticker: string; title: string; status: "planned" | "open" | "closed" | "cancelled"; direction: "long" | "short" | "neutral"; setup: string | null; thesis: string | null; invalidation: number | null; targets: number[]; tags: string[]; entry_at: string | null; entry_price: number | null; exit_at: string | null; exit_price: number | null; exit_reason: string | null; position_id: string | null; signal_id: string | null; chart_state: Record<string, unknown> | null; chart_snapshot_svg: string | null; legs: JournalOptionLeg[]; option_excursion: { status: string; caveat: string; coverage?: { requested_legs: number; calculated_legs: number }; legs: Array<{ contract_symbol: string; status: string; side?: string; quantity?: number; entry_mark?: number; entry_mark_source?: string; events: number; excursions?: Record<string, { observations: number; mfe_pct: number | null; mae_pct: number | null; mfe_dollars: number | null; mae_dollars: number | null }> }> }; notes: string | null; created_at: string; updated_at: string; excursion: { mfe_pct: number | null; mae_pct: number | null; bars: number; status: string } };
export type JournalPayload = { entries: JournalEntry[]; as_of: string; caveat: string; execution_capability: false };
export type JournalInput = Partial<JournalEntry> & { ticker: string; title: string };
export const fetchJournal = (ticker?: string, signal?: AbortSignal) => getJson<JournalPayload>(`/api/journal${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`, signal);
export const createJournalEntry = (input: JournalInput, signal?: AbortSignal) => postJson<JournalEntry>("/api/journal?action=create", input, signal);
export const updateJournalEntry = (id: string, input: Partial<JournalEntry>, signal?: AbortSignal) => postJson<JournalEntry>("/api/journal?action=update", { id, ...input }, signal);
export const deleteJournalEntry = (id: string, signal?: AbortSignal) => postJson<{ deleted: string }>("/api/journal?action=delete", { id }, signal);
export type ChartTemplate = { id: string; name: string; state: Record<string, unknown>; created_at: string; updated_at: string };
export const fetchChartTemplates = (signal?: AbortSignal) => getJson<{ templates: ChartTemplate[] }>("/api/journal?action=templates", signal);
export const saveChartTemplate = (name: string, state: Record<string, unknown>, signal?: AbortSignal) => postJson<ChartTemplate>("/api/journal?action=save-template", { name, state }, signal);

export type CompanyContext = { ticker: string; generated_at: string; profile: null | { name: string; cik: string; sic: string; sic_description: string; state: string; fiscal_year_end: string; exchanges: string[]; website: string; investor_website: string; sec_company_url: string }; fundamentals: Array<{ name: string; tag: string; label: string; unit: string; val: number; filed: string; end: string; form: string; accn: string }>; filings: Array<{ form: string; filed: string; report_date: string; accession: string; url: string }>; macro: { events: Array<{ date: string; time: string | null; title: string; source: string; source_url: string }>; errors: Array<{ source: string; error: string }> }; earnings: { status: string; events: Array<{ symbol: string; scheduled_date: string; timing: string; status: string; provider: string; conflict?: boolean }>; detail: string }; corporate_actions: { status: string; detail: string }; sources: Array<{ name: string; url: string | null }>; errors: Array<{ source: string; error: string }>; execution_capability: false };
export const fetchCompanyContext = (ticker: string, signal?: AbortSignal) => getJson<CompanyContext>(`/api/company-context?ticker=${encodeURIComponent(ticker)}`, signal);

// Ask Cipher — core/ask_cipher.py. Grounded strictly in tool calls made this
// turn against Cipher's own data; no live orders, no fresh backtests (those
// take minutes and live in the async job UIs, not a chat turn).

export type AskChatRole = "user" | "assistant";
export type AskChatMessage = { role: AskChatRole; content: string };

export function startAskJob(
  message: string,
  history: AskChatMessage[],
  activeTicker?: string,
  signal?: AbortSignal
): Promise<{ job_id: string; status: string }> {
  return postJson("/api/ask", { message, history, active_ticker: activeTicker }, signal);
}

// News — /api/news, a pass-through of Yahoo Finance's public RSS feed for one symbol.
// Headlines only: there is no score, rank, summary or derived signal in this payload,
// and the backend's `caveat` string says so in the UI's own words.

export type RealNewsHeadline = {
  title: string;
  link: string;
  /** RFC-822 date exactly as the feed reported it, e.g. "Mon, 10 Aug 2026 18:25:25 +0000". */
  published: string;
};

export type RealNews = {
  as_of: string;
  read_only: boolean;
  ticker: string;
  source: string;
  caveat: string;
  headlines: RealNewsHeadline[];
};

export function fetchNews(
  ticker: string,
  limit = 15,
  signal?: AbortSignal
): Promise<RealNews> {
  return getJson<RealNews>(
    `/api/news?ticker=${encodeURIComponent(ticker)}&limit=${limit}`,
    signal
  );
}

// Historical options research catalog — stored manifests and completed report
// artifacts only. The API never starts a lab run from a browser request.

export type OptionsBacktestDataset = {
  id: string;
  relative_path: string;
  manifest_type: "download_manifest" | "eod_archive_manifest";
  provider_dataset_id: string | null;
  status: string | null;
  database_present: boolean;
  database_size_bytes: number | null;
  database_sha256: string | null;
  coverage: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  caveats: string[];
  research_grade?: boolean;
};

export type OptionsBacktestReport = {
  id: string;
  relative_path: string;
  size_bytes: number;
  modified_at: string;
  dataset_id: string | null;
};

export type OptionsBacktestCatalog = {
  as_of: string;
  read_only: true;
  datasets: OptionsBacktestDataset[];
  reports: OptionsBacktestReport[];
  errors: Array<{ relative_path: string; error: string }>;
  counts: { datasets: number; reports: number; manifest_errors: number };
  caveat: string;
};

export type OptionsBacktestReportPayload = {
  report: OptionsBacktestReport;
  result: Record<string, unknown>;
  read_only: true;
};

export function fetchOptionsBacktestCatalog(
  signal?: AbortSignal,
  refresh = false
): Promise<OptionsBacktestCatalog> {
  return getJson<OptionsBacktestCatalog>(
    `/api/options-backtest?action=list${refresh ? "&refresh=true" : ""}`,
    signal
  );
}

export function fetchOptionsBacktestReport(
  reportId: string,
  signal?: AbortSignal
): Promise<OptionsBacktestReportPayload> {
  return getJson<OptionsBacktestReportPayload>(
    `/api/options-backtest?action=report&id=${encodeURIComponent(reportId)}`,
    signal
  );
}

export type OptionsLabProtocol = { id: string; script: string; research_only: true };
export type OptionsLabJob = {
  id: string; protocol: string; status: "queued" | "running" | "done" | "error";
  pct: number; message: string; result: Record<string, unknown> | null; error: string | null;
  created_at: string; updated_at: string; research_only: true; execution_capability: false;
};
export function fetchOptionsLabJobs(signal?: AbortSignal): Promise<{ jobs: OptionsLabJob[]; protocols: OptionsLabProtocol[] }> {
  return getJson("/api/options-backtest?action=jobs", signal);
}
export function fetchOptionsLabJob(id: string, signal?: AbortSignal): Promise<OptionsLabJob> {
  return getJson(`/api/options-backtest?action=job&id=${encodeURIComponent(id)}`, signal);
}
export function startOptionsLab(protocol: string, signal?: AbortSignal): Promise<{ job_id: string; status: string; research_only: true }> {
  return postJson("/api/options-backtest?action=start", { protocol }, signal);
}

export type GexReplaySnapshotMeta = {
  id: number;
  ticker: string;
  captured_at: string;
  feed: string | null;
  spot: number | null;
  day_change_pct: number | null;
  contracts: number | null;
  calculated_cells: number | null;
  listed_cells: number | null;
  global_max_strike: number | null;
  call_wall_strike: number | null;
  put_wall_strike: number | null;
  gamma_flip_level: number | null;
  caveat: string;
};

export type GexReplayCatalog = {
  tickers: Array<{ ticker: string; snapshots: number; first_capture: string; last_capture: string }>;
  snapshots: GexReplaySnapshotMeta[];
  counts: { tickers: number; snapshots: number };
  selected_ticker: string | null;
  read_only: true;
  caveat: string;
};

export type GexReplayStrike = {
  strike: number;
  call_gex: number | null;
  put_gex: number | null;
  net_gex: number | null;
  call_oi: number | null;
  put_oi: number | null;
  volume: number | null;
  available_cells: number;
  listed_cells: number;
  available: boolean;
  incomplete: boolean;
};

export type GexReplayPayload = {
  snapshot: GexReplaySnapshotMeta;
  previous: { id: number; captured_at: string; spot: number | null } | null;
  next: { id: number; captured_at: string; spot: number | null } | null;
  strikes: GexReplayStrike[];
  read_only: true;
  aggregation: string;
  caveat: string;
};

export function fetchGexReplayCatalog(ticker: string, signal?: AbortSignal): Promise<GexReplayCatalog> {
  return getJson<GexReplayCatalog>(
    `/api/gex-replay?action=catalog&ticker=${encodeURIComponent(ticker)}&limit=1000`, signal
  );
}

export function fetchGexReplaySnapshot(id: number, signal?: AbortSignal): Promise<GexReplayPayload> {
  return getJson<GexReplayPayload>(`/api/gex-replay?action=snapshot&id=${id}`, signal);
}

export type AlertKind = "price_above" | "price_below" | "day_change_above" | "day_change_below" | "scanner_score_above" | "flow_premium_above" | "net_gex_above" | "net_gex_below" | "atm_iv_above" | "atm_spread_above" | "expiration_days_below" | "portfolio_delta_abs_above" | "data_stale_count_above";
export type AlertRule = { id: string; ticker: string; kind: AlertKind; threshold: number; enabled: boolean; created_at: string };
export type AlertDelivery = { delivery_key: string; rule_id: string; observed_at: string | null; observed: number | null; threshold: number; channel: string; status: string; message: string; created_at: string };
export type AlertsPayload = { rules: AlertRule[]; deliveries: AlertDelivery[]; kinds: AlertKind[]; local_only: true; execution_capability: false };

export function fetchAlerts(signal?: AbortSignal): Promise<AlertsPayload> {
  return getJson<AlertsPayload>("/api/alerts", signal);
}
export function addAlert(rule: { ticker: string; kind: AlertKind; threshold: number }, signal?: AbortSignal): Promise<AlertRule> {
  return postJson<AlertRule>("/api/alerts?action=add", rule, signal);
}
export function deleteAlert(id: string, signal?: AbortSignal): Promise<{ deleted: string }> {
  return postJson("/api/alerts?action=delete", { id }, signal);
}

// Workspace layouts — core/workspace_layouts.py. The grid blob is dockview's own
// serialized state and is stored opaquely: the backend validates the envelope (name,
// size) and never interprets the grid, so the shape below stays deliberately loose.

export type WorkspaceLayoutMeta = {
  name: string;
  created_at: string;
  updated_at: string;
};

export type WorkspaceLayoutsStatus = {
  as_of: string;
  layouts: WorkspaceLayoutMeta[];
  count: number;
  max_layouts: number;
};

/** A single saved layout, including the dockview blob. */
export type WorkspaceLayoutRecord = WorkspaceLayoutMeta & {
  layout: Record<string, unknown>;
};

export function fetchWorkspaceLayouts(signal?: AbortSignal): Promise<WorkspaceLayoutsStatus> {
  return getJson<WorkspaceLayoutsStatus>("/api/workspace-layouts", signal);
}

export function fetchWorkspaceLayout(
  name: string,
  signal?: AbortSignal
): Promise<WorkspaceLayoutRecord> {
  return getJson<WorkspaceLayoutRecord>(
    `/api/workspace-layouts?name=${encodeURIComponent(name)}`,
    signal
  );
}

export function saveWorkspaceLayout(
  name: string,
  layout: Record<string, unknown>,
  signal?: AbortSignal
): Promise<WorkspaceLayoutMeta> {
  return postJson<WorkspaceLayoutMeta>(
    "/api/workspace-layouts?action=save",
    { name, layout },
    signal
  );
}

export function deleteWorkspaceLayout(
  name: string,
  signal?: AbortSignal
): Promise<{ deleted: true; name: string }> {
  return postJson("/api/workspace-layouts?action=delete", { name }, signal);
}

export type RealScanUniverse = {
  count: number;
  /** Optionable tickers, ordered by the backend's own liquidity/size ranking. */
  tickers: string[];
  as_of?: string;
  source?: string;
  validation?: {
    validated_at?: string;
    provider?: string;
    criterion?: string;
    prior_count?: number;
    validated_count?: number;
    removed?: Array<{ ticker: string; prior_tier: string; reason: string }>;
  };
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

export type ProviderCapabilities = {
  as_of: string;
  active_provider: "alpaca";
  mode: "alpaca_opra_sip" | "alpaca_indicative_iex" | "alpaca_custom" | "unconfigured";
  read_only: true;
  live_execution_present: false;
  alpaca: {
    credentials_configured: boolean;
    options_feed: string;
    stock_feed: string;
    options_chain: "full" | "degraded" | "unsupported" | null;
    stock_quotes_bars: "full" | "degraded" | "unsupported" | null;
    caveat: string;
  };
  tradier: {
    credentials_configured: boolean;
    status: "capture_supplement_only";
    capabilities: string[];
    not_a_replacement_for: string[];
  };
  webull: {
    credentials_configured: false;
    status: "unsupported";
    capabilities: string[];
  };
};

export function fetchProviderCapabilities(signal?: AbortSignal): Promise<ProviderCapabilities> {
  return getJson<ProviderCapabilities>("/api/provider-capabilities", signal);
}

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

// Research ranking — what Cipher currently believes, from the last autopilot pass
// (core/autopilot.py via /api/research-ranking). Served from a stored artifact rather than
// recomputed per request, because walking every report under runtime/data is far too slow
// for a page load. That makes staleness possible, so `stale` and `age_seconds` ship with the
// data and the panel must show them rather than imply the numbers are live.

export type ResearchTierCounts = Record<string, number>;

export type ResearchRankedRow = {
  study_id: string;
  engine: string;
  evidence_tier: number;
  verdict: string;
  cost_basis: string;
  observations: number;
  /** Best case across candidates. A study's verdict may be governed by a harsher stress
   *  case, so this must never be labelled as the governing result. */
  best_value: number | null;
  blockers: string[];
};

export type ResearchRankedGroup = {
  metric: string;
  unit: string;
  results: ResearchRankedRow[];
};

export type ResearchAction = {
  action: string;
  detail: string;
  unblocks_studies: number;
  example_studies: string[];
  latency: string;
  limitation: string;
  clears_blocker: string;
};

export type ResearchChange = {
  kind: string;
  study_id?: string;
  detail: string;
};

export type ResearchCoverage = {
  adapted: number;
  unadapted: number;
  adapters_available: number;
  note: string;
};

export type ResearchCaptureHealth = {
  available: boolean;
  verdict: string;
  distinct_days: number | null;
  gap_count: number;
  missing_weekdays: string[];
  sparse_days: string[];
  profile_stale: boolean;
  profile_age_days: number | null;
};

export type ResearchRanking = {
  available: boolean;
  /** Present only when `available` is false. */
  reason?: string;
  as_of?: string | null;
  age_seconds?: number | null;
  stale?: boolean;
  stale_after_seconds?: number;
  commit?: string | null;
  live_order_authority: boolean;
  highest_possible_output?: string | null;
  headline?: string;
  coverage?: ResearchCoverage;
  capture_health?: ResearchCaptureHealth | null;
  tier_counts?: ResearchTierCounts;
  selectable?: string[];
  changes?: ResearchChange[];
  baseline?: boolean;
  noop?: boolean;
  recommended_actions?: ResearchAction[];
  nothing_to_run_because?: string;
  unclassified_blockers?: { blocker: string; study_count: number }[];
  groups?: ResearchRankedGroup[];
};

/** What Cipher believes right now, how strongly, and what would change its mind. */
export function fetchResearchRanking(signal?: AbortSignal): Promise<ResearchRanking> {
  return getJson<ResearchRanking>("/api/research-ranking", signal);
}

export type OperatorStatus = {
  generated_at: string;
  read_only: true;
  execution_capability: false;
  disk: { total_bytes: number; used_bytes: number; free_bytes: number; free_percent: number; runway_status: string; runway_days?: number | null; detail: string };
  databases: Array<{ path: string; status: string; integrity: string; bytes?: number; observed_at?: string; age_seconds?: number; table_count?: number; error?: string }>;
  captures: Record<string, { status: string; path?: string; bytes?: number; observed_at?: string; age_seconds?: number; detail?: string }>;
  caches: Array<{ name: string; entries: number; ttl_seconds: number; oldest_age_s: number | null; newest_age_s: number | null; avg_age_s: number | null; hits: number; misses: number; hit_rate_pct: number }>;
  provider_telemetry: { status: string; window_days?: number; providers: Array<{ provider: string; operation: string; requests: number; avg_latency_ms: number; p95_latency_ms: number; error_count: number; error_rate_pct: number; last_error: string | null; last_observed_at: string }> };
  retention: { mode: "DRY_RUN_ONLY"; candidate_count: number; candidate_bytes: number; destructive_action_enabled: false };
  off_host_archive: { status: string; receipts?: number; verified_and_pruned?: number; last_archived_at?: string; detail?: string; error?: string };
  backup: { status: string; created_at?: string; store_count?: number; path?: string; detail?: string };
  exceptions: string[];
};

export function fetchOperatorStatus(signal?: AbortSignal): Promise<OperatorStatus> {
  return getJson<OperatorStatus>("/api/operator-status", signal);
}

export type AutopilotStatus = {
  generated_at: string;
  phase: string;
  scheduler: { action: string; as_of?: string | null; reason?: string };
  plan: {
    available: boolean; plan_id?: string | null; market_date?: string | null;
    state: string; created_at?: string | null; candidate_count: number;
    candidates: Array<{ ticker: string; direction: string; score: number; reward_risk: number; sentiment_status?: string }>;
  };
  executor: {
    reachable: boolean; mode: string; reconciliation_passed?: boolean;
    quote_feed_degraded?: boolean; open_shadow_positions: number;
    last_mark_at?: string | null; last_worker_exception?: unknown;
  };
  learning: { training_status: string; samples: number; market_dates: number; blockers: string[] };
  models: { finbert: string; fingpt: string; custom_model: string; model_may_authorize_entry: false };
  daily_trace: {
    market_date: string; trace_available: boolean; cycles: number;
    actions: Record<string, number>; rejection_reason_counts: Record<string, number>;
    premarket_plan_observed: boolean; confirmation_cycle_observed: boolean;
    paper_submissions: number;
    recent: Array<Record<string, unknown>>;
  };
  paper_only: true;
  live_execution_capability: false;
};

export function fetchAutopilotStatus(signal?: AbortSignal): Promise<AutopilotStatus> {
  return getJson<AutopilotStatus>("/api/autopilot-status", signal);
}
