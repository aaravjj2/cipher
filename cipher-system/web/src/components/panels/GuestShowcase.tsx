"use client";

import { useEffect, useState } from "react";

import { guestPanelMode } from "@/lib/guestCatalog";

type Demo = {
  title: string;
  summary: string;
  metrics: [string, string][];
  rows: string[];
  next: string;
};

const GUEST_LOCKED_BOUNDARY =
  "This surface stays locked for guests: no writes, no saved private state, and no broker or LLM connection.";

const DEMOS: Record<string, Demo> = {
  Autopilot: { title: "An AI agent you can audit before you trust", summary: "Premarket research becomes a bounded paper decision only after fresh evidence, closed-bar confirmation, contract liquidity, and deterministic risk gates agree. Zero paper trades is a healthy outcome when no setup qualifies.", metrics: [["Phase", "Waiting for confirmation"], ["Authority", "Alpaca paper only"], ["Zero-trade", "Healthy"]], rows: ["08:20 ET · Research agents produced thesis and counter-thesis", "09:42 ET · MU confirmation closed above the planned level", "A day with no paper trade can be the correct outcome", "09:43 ET · Paper intent persisted before broker submission only after gates passed", "Exit, reconciliation, and every rejection remain in the audit ledger"], next: "Follow the evidence snapshot into Night Vision, then inspect the paper-order trace. This recorded run is illustrative; no real-money order is possible. Zero paper trades is not a stalled agent." },
  "Morning Brief": { title: "A two-minute plan before the bell", summary: "Regime, catalysts, exposure, and only the setups worth reviewing.", metrics: [["Regime", "Risk-on, selective"], ["MAG7 breadth", "5 of 7"], ["Event risk", "Elevated"]], rows: ["NVDA and MSFT lead relative strength", "META is nearest a high-gamma decision zone", "MU is the volatility focus; confirmation required"], next: "Inspect the highest-ranked setup and its invalidation." },
  "Earnings Radar": { title: "Catalysts ranked before the reaction", summary: "Expected move, revisions, historical reaction, and liquidity stay separate.", metrics: [["Liquid events", "6"], ["Universe", "MAG7 + leaders"], ["Validation", "Chronological"]], rows: ["MSFT · durable revisions, moderate implied move", "AMZN · wider tail risk, strong liquidity", "UNVALIDATED_FOR_LIVE_OPTIONS_PNL"], next: "Reject structures whose spread consumes the measured edge." },
  "Research Desk": { title: "One evidence trail from question to conclusion", summary: "Coordinates structure, options, history, catalysts, and adversarial review.", metrics: [["Agents", "5 bounded roles"], ["Sources", "Point-in-time"], ["Gate", "Human review"]], rows: ["Thesis and counter-thesis remain side by side", "Missing evidence stays unknown—not zero", "Every conclusion carries provenance and freshness"], next: "Begin with a falsifiable question, then inspect the evidence ledger." },
  "Ticker Workbench": { title: "One ticker, one evidence context", summary: "The live quote is temporarily unavailable, so Cipher is showing the safe workflow instead of an empty or stale price.", metrics: [["Source", "Demo fallback"], ["Scope", "Price · options · context"], ["Orders", "None"]], rows: ["Chart and options share the selected ticker", "Unavailable provider data stays unavailable", "Private research remains locked"], next: "Retry the live panel when provider access recovers." },
  "Night Vision": { title: "Price structure with honest exposure context", summary: "This fallback preserves the chart workflow without presenting synthetic levels as live market data.", metrics: [["Source", "Demo fallback"], ["GEX", "Public-OI heuristic"], ["Missing", "Unknown"]], rows: ["Premarket and regular sessions remain distinct", "Exposure is contextual—not verified dealer positioning", "Replay identity freezes what was known at decision time"], next: "Retry for a live Alpaca chart and current exposure snapshot." },
  "Strike Matrix": { title: "Options exposure without invented cells", summary: "This fallback explains the matrix while the bounded live Alpaca chain is unavailable.", metrics: [["Source", "Demo fallback"], ["Inputs", "Gamma · OI"], ["Missing", "Unknown"]], rows: ["Calls and puts retain signed exposure conventions", "Gamma or OI gaps never become zero", "GEX is a public-OI heuristic, not verified dealer positioning"], next: "Retry for the live Alpaca-backed matrix." },
  "Setup Scanner": { title: "Rank opportunities, not noise", summary: "Liquid names where trend, levels, volatility, and options structure agree.", metrics: [["Scanned", "42"], ["Qualified", "4"], ["Rejected", "38 explained"]], rows: ["NVDA · continuation watch · evidence 84/100", "MU · PM rejection watch · evidence 79/100", "TSLA · breakout only after confirmation"], next: "Open a candidate to inspect its rejection funnel and snapshot ID." },
  "My Watchlists": { title: "A focused desk, not a ticker dump", summary: "Quotes, catalysts, and the latest qualifying setup in one list.", metrics: [["Demo list", "12"], ["Catalysts", "4"], ["Actionable", "3"]], rows: ["MAG7 core", "Semis · NVDA, AMD, MU, AVGO", "Indices · SPY and QQQ"], next: "Sign in to persist custom lists and tags." },
  News: { title: "News attached to the thesis", summary: "Company, sector, macro, and earnings headlines grouped by decision impact.", metrics: [["High impact", "3"], ["Duplicates removed", "18"], ["Scope", "12 symbols"]], rows: ["AI capex read-through for semiconductors", "Rates sensitivity for long-duration technology", "Revisions separated from price commentary"], next: "Use headlines as catalyst evidence, never standalone direction." },
  "Options Terminal": { title: "Structure before prediction", summary: "This nav item is an illustrative showcase, not the live option chain. Expiry, strikes, Greeks, liquidity, expected move, and bounded payoff are shown as a recorded example.", metrics: [["Source", "Demo showcase"], ["Example", "NVDA call spread"], ["Orders", "None"]], rows: ["Front-week IV compared with next expiry", "Bid/ask width is a first-class filter", "Breakeven and scenario payoff shown together"], next: "Open Ticker Workbench → Options for the live chain. This Options Terminal nav item stays a demo showcase." },
  "Skew Map": { title: "See relative option demand without calling it a signal", summary: "Mirrored 25-delta put and call IV against aligned one-month return.", metrics: [["Formula", "25Δ put IV − call IV"], ["Use", "Research priority"], ["Orders", "None"]], rows: ["Raw volatility points across sectors", "A name stays PROVISIONAL until 20 stored sessions", "Earnings and thin chains stay flagged"], next: "Open the live stored-observation map, then verify the chain and event calendar." },
  Spyglass: { title: "Options flow with context", summary: "Clusters premium while refusing to equate flow with informed direction.", metrics: [["Focus", "Liquid prints"], ["Side", "Inferred"], ["OI", "Public snapshot"]], rows: ["Repeated NVDA call-side activity", "MU puts near a known level", "Index hedges separated from single-name bets"], next: "Confirm flow against price, volatility, and OI context." },
  "Company Context": { title: "Know what the ticker owns", summary: "Drivers, segments, revisions, valuation, catalysts, and thesis-break risks.", metrics: [["Example", "NVDA"], ["Drivers", "3 mapped"], ["Risk", "Expectations"]], rows: ["Secular demand separated from quarterly timing", "Suppliers and customers mapped", "Falsification evidence stated up front"], next: "Move from setup to business context without losing the question." },
  "Ask Cipher": { title: "An analyst that shows its work", summary: "Bounded questions, read-only tools, short context, and explicit uncertainty.", metrics: [["Tools", "Read-only"], ["Context", "Budgeted"], ["Output", "Evidence-linked"]], rows: ["Compare NVDA and AMD option structure", "Explain why MU failed a scanner gate", "Summarize catalyst concentration"], next: "Sign in to run tool-backed questions." },
  "Chart Workbench": { title: "Chart the decision, not decoration", summary: "Price, volume, VWAP, EMAs, sessions, and evidence snapshots.", metrics: [["Frames", "1m to daily"], ["Levels", "PM · PD · options"], ["State", "Read-only"]], rows: ["Premarket and RTH remain distinct", "Signals appear after confirmation", "Annotations retain source timestamps"], next: "Validate entry location and invalidation before the thesis." },
  "Portfolio Risk": { title: "Risk beyond ticker weights", summary: "Equity and option exposure normalized by delta, catalyst, and factor.", metrics: [["Demo NAV", "$100,000"], ["Net delta", "+0.31"], ["Top factor", "AI infra 38%"]], rows: ["NVDA and AVGO share factor risk", "Weekly options concentrate theta", "Earnings collisions flagged before sizing"], next: "Reduce correlated exposure before adding another idea." },
  Holdings: { title: "Stocks and options in one exposure view", summary: "Value, delta, catalysts, and scenario risk across instrument types.", metrics: [["Stocks", "4 demo"], ["Options", "2 defined-risk"], ["Broker", "Disconnected"]], rows: ["Options auto-included in totals", "Unknown Greeks remain unavailable", "Positions link to originating thesis"], next: "Connect private data after sign-in." },
  Alerts: { title: "Alerts that encode the whole setup", summary: "Trigger, confirmation, invalidation, freshness, and source snapshot.", metrics: [["Demo alerts", "4"], ["Price-only", "0"], ["Orders", "None"]], rows: ["MU PMH reclaim after confirmed close", "NVDA gamma-wall break with liquidity", "META earnings-risk reminder"], next: "Sign in to create and deliver alerts." },
  "Paper Portfolios": { title: "Prospective evidence, never backfilled wins", summary: "Isolated paper ledgers for decisions, rejections, fills, exits, and equity.", metrics: [["Portfolios", "6 demo"], ["Authority", "Simulation"], ["Backfill", "Never"]], rows: ["No-setup differs from data failure", "Every rejection retains its reason", "Option P&L requires a real or labelled mark"], next: "Judge strategies from prospective samples and ledger truth." },
  "Trader Journal": { title: "A journal tied to evidence", summary: "Thesis, setup, plan, execution notes, outcome, and decision-time snapshot.", metrics: [["Demo entries", "8"], ["Mistakes tagged", "3"], ["Review", "Weekly"]], rows: ["Process errors separated from losses", "Screenshots retain levels and time", "Repeated mistakes become measurable"], next: "Sign in to keep a private journal." },
  Standing: { title: "Know what has earned trust", summary: "Maturity from hypothesis through backtest, paper, and mandatory review.", metrics: [["Research", "14"], ["Paper eligible", "6"], ["Live authorized", "0"]], rows: ["Sample size gates promotion", "Failures remain in lineage", "No path crosses LIVE_REVIEW_REQUIRED"], next: "Promote evidence quality, not attractive historical P&L." },
  Strategies: { title: "A catalog with assumptions attached", summary: "Universe, timeframe, signal, fills, risk, and validation status.", metrics: [["Protocols", "18"], ["Paper cohorts", "6"], ["Hidden rules", "0"]], rows: ["Premarket liquidity break and sweep", "Structural Fib continuation", "Close-to-next-open research"], next: "Compare only compatible samples and execution assumptions." },
  Backtest: { title: "Underlying strategy laboratory", summary: "Reproducible tests with costs, next-bar rules, and chronological holdouts.", metrics: [["Universe", "39 stocks/ETFs"], ["Costs", "Explicit"], ["Splits", "Chronological"]], rows: ["Downloadable trade ledger", "Every parameter trial retained", "Holdout never merged into training"], next: "Inspect stability across tickers and years." },
  "Options Backtest": { title: "Captured option-bar research", summary: "Point-in-time contract bars with historical NBBO absence preserved.", metrics: [["Archives", "37"], ["Fill truth", "Trade bars"], ["NBBO", "Unavailable"]], rows: ["Entry-time contract selection", "Missing bars never become zero returns", "Gross and friction stress kept separate"], next: "Use results to prioritize prospective validation." },
  "GEX Replay": { title: "Replay what the matrix knew then", summary: "Frozen public-OI gamma estimates without future positioning leakage.", metrics: [["Formula", "Signed GEX"], ["Direction", "Heuristic"], ["Missing", "Unknown"]], rows: ["Snapshot checksums protect lineage", "Spot and expiry context retained", "Dealer intent is never asserted"], next: "Compare frozen exposure with the price path that followed." },
  "Trident": { title: "Index exposure at a glance", summary: "SPY, QQQ, and IWM gamma structure without a forced common narrative.", metrics: [["Matrices", "3"], ["Levels", "Walls · flips"], ["Use", "Context"]], rows: ["SPY · broad-market hedges", "QQQ · technology concentration", "GEX is a public-OI heuristic, not verified dealer positioning"], next: "Treat disagreement as a risk clue, not a guaranteed trade." },
  "Chart Saves": { title: "Freeze the chart you actually saw", summary: "Ticker, timeframe, levels, notes, and evidence timestamp.", metrics: [["Demo saves", "5"], ["Ownership", "Private"], ["Replay", "Deterministic"]], rows: ["Morning plan", "Entry review", "Post-close lesson"], next: "Sign in to persist chart state." },
  Beliefs: { title: "Claims designed to be disproved", summary: "Hypotheses with evidence, blockers, and promotion criteria.", metrics: [["Active", "9"], ["Rejected", "7"], ["To paper", "3"]], rows: ["Fresh impulse may improve continuation", "Public OI is context, not dealer truth", "Overnight edge varies by ticker and cost"], next: "Advance a belief only when its predefined gate passes." },
};

const FALLBACK: Demo = { title: "Explore Cipher's research workflow", summary: "A deterministic representation of this private feature.", metrics: [["Mode", "Read-only demo"], ["Universe", "MAG7 + leaders"], ["Orders", "Disabled"]], rows: ["Point-in-time inputs", "Explicit uncertainty", "Human decision gate"], next: "Sign in for private data and interactive tools." };

type AgentShowcase = {
  decision_log?: { available?: boolean; rows?: { event?: string; decision_id?: string; reason?: string; ticker?: string }[] };
  gex_regime?: { available?: boolean; regime?: string; gamma_flip_level?: number | null; spot?: number | null; net_gex_b?: number | null };
  decision_quality?: {
    available?: boolean; trade_count?: number;
    expectancy?: { sample_size?: number; win_rate_pct?: number; expectancy_per_trade_pct?: number; note?: string | null };
    dead_on_arrival?: { count?: number; share_pct?: number };
  };
};

/**
 * Live paper record for the Autopilot panel: real decisions and quality
 * statistics from the public read-only feed. Renders nothing when the feed is
 * empty or unreachable, so the illustrative content below stays the fallback
 * rather than a broken state.
 */
function LiveAgentRecord() {
  const [data, setData] = useState<AgentShowcase | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/agent-showcase", { headers: { accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => { if (!cancelled && payload) setData(payload); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);
  if (!data) return null;
  const quality = data.decision_quality || {};
  const expectancy = quality.expectancy || {};
  const doa = quality.dead_on_arrival || {};
  const events = (data.decision_log?.rows || []).slice(-4).reverse();
  if (!quality.trade_count && !events.length) return null;
  return <section className="border-t border-[var(--gold)] bg-[color-mix(in_srgb,var(--gold)_6%,transparent)]" data-testid="guest-live-agent">
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--line)] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--gold)]">Live · this machine&apos;s actual paper record</p><span className="border border-[var(--line)] px-2 py-1 text-[9px] uppercase text-[var(--text-mute)]">Not demo</span></header>
    <dl className="grid grid-cols-2 border-b border-[var(--line)] lg:grid-cols-3">
      <div className="border-b border-r border-[var(--line)] p-4"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">Closed trades</dt><dd className="mt-1 font-mono text-base font-semibold">{quality.trade_count ?? 0}</dd></div>
      <div className="border-b border-[var(--line)] p-4 sm:border-b-0 sm:border-r"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">Win rate · expectancy/trade</dt><dd className="mt-1 font-mono text-base font-semibold">{expectancy.win_rate_pct ?? "–"}% · {expectancy.expectancy_per_trade_pct ?? "–"}%</dd></div>
      <div className="p-4"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">Dead-on-arrival losses</dt><dd className="mt-1 font-mono text-base font-semibold">{doa.count ?? 0} ({doa.share_pct ?? 0}%)</dd></div>
      <div className="border-t border-[var(--line)] p-4 sm:border-t-0 sm:border-r"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">SPY gamma regime</dt><dd className="mt-1 font-mono text-base font-semibold">{data.gex_regime?.regime ?? "–"}{data.gex_regime?.net_gex_b != null ? ` · ${data.gex_regime.net_gex_b}B` : ""}</dd></div>
      <div className="border-t border-[var(--line)] p-4 sm:border-t-0 sm:border-r"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">Gamma flip level</dt><dd className="mt-1 font-mono text-base font-semibold">{data.gex_regime?.gamma_flip_level ?? "–"}</dd></div>
      <div className="border-t border-[var(--line)] p-4 sm:border-t-0"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">Decision log</dt><dd className="mt-1 font-mono text-base font-semibold">{events.length} recent</dd></div>
    </dl>
    {events.length > 0 && <ol className="divide-y divide-[var(--line-soft)] p-4 text-xs text-[var(--text-dim)]">
      {events.map((event, index) => <li key={`${event.decision_id}-${index}`} className="py-1.5 font-mono">
        <span className="text-[var(--gold)]">{event.event}</span> · {event.decision_id}{event.reason ? ` · ${event.reason}` : ""}
      </li>)}
    </ol>}
    {expectancy.note && <p className="border-t border-[var(--line)] px-4 py-3 text-[10px] leading-5 text-[var(--text-mute)]">{expectancy.note}</p>}
  </section>;
}

export function GuestShowcase({ panel, ticker, titleTag: Title = "h1" }: { panel: string; ticker: string; titleTag?: "h1" | "h2" }) {
  const content = DEMOS[panel] || FALLBACK;
  const locked = guestPanelMode(panel) === "locked" ? GUEST_LOCKED_BOUNDARY : null;
  return <section data-testid="guest-showcase" data-guest-panel={panel} data-guest-source={panel === "Autopilot" ? "demo+live" : "demo"} className="mx-auto w-full max-w-6xl border border-[var(--line)] bg-[var(--panel)]">
    <header className="border-b border-[var(--line)] p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--gold)]">Illustrative · {ticker} · read only</p><span className="border border-[var(--line)] px-2 py-1 text-[9px] uppercase text-[var(--text-mute)]">Demo data</span></div>
      <Title className="mt-3 text-xl font-semibold sm:text-2xl">{panel}</Title>
      <p className="mt-1 text-sm font-medium text-[var(--text-dim)]">{content.title}</p>
      <p className="mt-2 max-w-3xl text-xs leading-5 text-[var(--text-mute)]">{content.summary}</p>
    </header>
    {panel === "Autopilot" && <LiveAgentRecord />}
    <dl className="grid border-b border-[var(--line)] sm:grid-cols-3">{content.metrics.map(([label, value]) => <div key={label} className="border-b border-[var(--line)] p-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">{label}</dt><dd className="mt-1 font-mono text-base font-semibold text-[var(--text)]">{value}</dd></div>)}</dl>
    <div className="grid lg:grid-cols-[1.4fr_1fr]">
      <div className="border-b border-[var(--line)] p-4 lg:border-b-0 lg:border-r"><h2 className="text-xs font-semibold">What Cipher surfaces</h2><ol className="mt-3 divide-y divide-[var(--line-soft)] text-xs text-[var(--text-dim)]">{content.rows.map((row, index) => <li key={row} className="grid grid-cols-[24px_1fr] gap-2 py-2"><span className="font-mono text-[var(--gold)]">{String(index + 1).padStart(2, "0")}</span><span>{row}</span></li>)}</ol></div>
      <div className="p-4"><h2 className="text-xs font-semibold">Next decision</h2><p className="mt-3 text-xs leading-5 text-[var(--text-dim)]">{content.next}</p>{locked && <div className="mt-4 border-l-2 border-[var(--gold)] pl-3 text-[10px] leading-5 text-[var(--text-dim)]"><strong className="text-[var(--gold)]">Guest boundary:</strong> {locked}</div>}</div>
    </div>
    <p className="border-t border-[var(--line)] px-4 py-3 text-[10px] leading-5 text-[var(--text-mute)]">Static values are labelled—not current quotes, recommendations, or performance claims. Live panels are marked separately. Cipher has no browser order authority.</p>
  </section>;
}
