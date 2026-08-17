"use client";

import { useEffect, useState } from "react";
import { fetchAutopilotStatus, fetchMorningBrief, fetchQuote, type AutopilotStatus, type MorningBriefResponse } from "@/lib/api";
import { loadWatchlistTickers } from "@/lib/watchlist";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

const money = (value?: number | null) => value == null ? "—" : value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const pct = (value?: number | null) => value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
const when = (value?: string | null) => value ? new Date(value).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
      <h2 className="mb-3 text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>{title}</h2>
      {children}
    </section>
  );
}

export function MorningBrief({ ticker, onNavigate }: { ticker: string; onNavigate?: (panel: string, ticker?: string) => void }) {
  const [data, setData] = useState<MorningBriefResponse | null>(null);
  const [error, setError] = useState("");
  const [watchlist, setWatchlist] = useState<Array<{ ticker: string; price: number; change: number }>>([]);
  const [autopilot, setAutopilot] = useState<AutopilotStatus | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    fetchMorningBrief(ticker, ctrl.signal).then(setData).catch((err) => {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Morning Brief unavailable");
    });
    fetchAutopilotStatus(ctrl.signal).then(setAutopilot).catch(() => setAutopilot(null));
    Promise.allSettled(loadWatchlistTickers().slice(0, 12).map(async (symbol) => {
      const quote = await fetchQuote(symbol, ctrl.signal);
      return { ticker: symbol, price: quote.price_context, change: quote.day_change_pct };
    })).then((results) => {
      const rows = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
      setWatchlist(rows.sort((a, b) => Math.abs(b.change) - Math.abs(a.change)));
    });
    return () => ctrl.abort();
  }, [ticker]);

  if (error) return <div className="rounded-xl border p-4 text-sm" style={{ borderColor: "var(--line)", color: "var(--negative)" }}>{error}</div>;
  if (!data) return <SkeletonRegion label="Building Morning Brief…"><Skeleton className="h-28 w-full" /><Skeleton className="h-48 w-full" /></SkeletonRegion>;

  const flow = data.significant_flow.prints ?? [];
  const flowAvailability = data.significant_flow.availability?.status ?? "available";
  const prospective = data.prospective_fronttests;
  const coverage = prospective.latest_coverage;
  const coverageHealthy = coverage.observed > 0 && coverage.fresh === coverage.observed;
  return (
    <div className="flex flex-col gap-4" style={{ fontFamily: "var(--font-mono)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold" style={{ color: "var(--text)" }}>Morning Brief</h1>
          <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>{data.session.phase.toUpperCase()} · {data.session.market_date} ET · generated {when(data.generated_at)}</p>
        </div>
        <span className="rounded-full border px-3 py-1 text-[10px]" style={{ borderColor: "var(--line)", color: data.freshness.healthy ? "var(--positive)" : "var(--gold)" }}>
          {data.freshness.healthy ? "Inputs healthy" : `${data.freshness.exceptions.length} data exceptions`}
        </span>
      </div>

      <nav aria-label="Daily research workflow" className="flex flex-wrap items-center gap-1 rounded-xl border p-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <span className="px-2 text-[9px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>Daily flow</span>
        {[
          ["1", "Research", "Research Desk"], ["2", "Scan", "Setup Scanner"], ["3", "Validate chart", "Night Vision"],
          ["4", "Structure", "Options Terminal"], ["5", "Record", "Trader Journal"], ["6", "Review paper", "Paper Portfolios"],
        ].map(([step, label, panel]) => <button key={panel} type="button" onClick={() => onNavigate?.(panel, ticker)} className="rounded-lg px-2.5 py-1.5 text-[10px] hover:bg-white/[0.04]" style={{ color: "var(--text-dim)" }}><b style={{ color: "var(--accent)" }}>{step}</b> {label}</button>)}
      </nav>

      <section className="rounded-xl border p-4" style={{ borderColor: data.attention.length ? "var(--gold)" : "var(--line)", background: "var(--panel)" }}>
        <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>1 · Needs attention</h2><span className="text-[10px]" style={{ color: data.attention.length ? "var(--gold)" : "var(--positive)" }}>{data.attention.length ? `${data.attention.length} review item${data.attention.length === 1 ? "" : "s"}` : "No integrity exceptions"}</span></div>
        {data.attention.length ? <div className="grid gap-2 lg:grid-cols-2">{data.attention.map((item, index) => <div key={`${item.kind}-${index}`} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="text-xs font-semibold" style={{ color: item.severity === "error" ? "var(--negative)" : "var(--gold)" }}>{item.title}</div><p className="mt-1 text-[10px]" style={{ color: "var(--text-dim)" }}>{item.detail}</p></div>)}</div> : <p className="text-[11px]" style={{ color: "var(--text-dim)" }}>Required inputs and the latest prospective observation pass are currently within their declared freshness rules.</p>}
      </section>

      <section className="rounded-xl border p-4" style={{ borderColor: autopilot?.executor.reachable ? "var(--line)" : "var(--gold)", background: "var(--panel)" }}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h2 className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>Paper autopilot</h2><p className="mt-1 text-[10px]" style={{ color: "var(--text-dim)" }}>Premarket discovery → 09:35 ET confirmation → simulated option entry and managed exit. No broker-order capability.</p></div>
          <span className="rounded-full border px-3 py-1 text-[10px]" style={{ borderColor: "var(--line)", color: autopilot?.executor.reachable ? "var(--positive)" : "var(--gold)" }}>{autopilot ? `${autopilot.phase.replaceAll("_", " ")} · ${autopilot.executor.reachable ? autopilot.executor.mode : "executor offline"}` : "status unavailable"}</span>
        </div>
        {autopilot && <><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">{[
          ["Watch candidates", autopilot.plan.candidate_count], ["Open shadow", autopilot.executor.open_shadow_positions],
          ["Training samples", autopilot.learning.samples], ["Market dates", autopilot.learning.market_dates],
          ["Last action", autopilot.scheduler.action.replaceAll("_", " ")],
        ].map(([label, value]) => <div key={String(label)} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>{label}</div><div className="mt-1 text-xs font-semibold">{value}</div></div>)}</div>
        {autopilot.plan.candidates.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{autopilot.plan.candidates.map((row) => <button key={row.ticker} type="button" onClick={() => onNavigate?.("Ticker Workbench", row.ticker)} className="rounded-lg border px-3 py-2 text-left text-[10px]" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><b>{row.ticker}</b> · <span style={{ color: row.direction === "BULLISH" ? "var(--positive)" : "var(--negative)" }}>{row.direction}</span> · score {row.score} · R/R {row.reward_risk}</button>)}</div>}
        <p className="mt-3 text-[9px]" style={{ color: "var(--text-mute)" }}>FinBERT: {autopilot.models.finbert.replaceAll("_", " ")} · FinGPT: {autopilot.models.fingpt.replaceAll("_", " ")} · learning: {autopilot.learning.training_status.replaceAll("_", " ")}</p></>}
      </section>

      <section className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>2 · Active paper observations</h2><p className="mt-1 text-[10px]" style={{ color: "var(--text-dim)" }}>Registered prospective cohorts only. No backfill, broker connection, or execution authority.</p></div><button type="button" onClick={() => onNavigate?.("Paper Portfolios")} className="rounded-lg border px-3 py-1.5 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--accent)" }}>Open full audit ledger</button></div>
        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-6">
          {[["Observed", coverage.observed], ["Fresh", coverage.fresh], ["Partial", coverage.partial], ["Stale", coverage.stale], ["Missing", coverage.missing], ["Opened now", coverage.signals_opened]].map(([label, value]) => <div key={String(label)} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>{label}</div><div className="mt-1 text-sm font-semibold" style={{ color: label === "Fresh" && coverageHealthy ? "var(--positive)" : (label === "Stale" || label === "Missing") && Number(value) > 0 ? "var(--negative)" : "var(--text)" }}>{value}</div></div>)}
        </div>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">{prospective.programs.map((program) => <div key={program.program_id} className="rounded-lg border p-3" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="flex justify-between gap-3"><div><div className="text-xs font-semibold">{program.name}</div><div className="mt-1 text-[9px]" style={{ color: "var(--text-mute)" }}>{program.kind.replaceAll("_", " ")}</div></div><span className="text-[9px] font-bold" style={{ color: program.effective_status === "COLLECTING" ? "var(--positive)" : "var(--text-dim)" }}>{program.effective_status}</span></div><div className="mt-3 grid grid-cols-4 gap-2 text-[10px]"><span>{program.open_signals}<small className="block" style={{ color: "var(--text-mute)" }}>open</small></span><span>{program.closed_signals}/{program.minimum_sample}<small className="block" style={{ color: "var(--text-mute)" }}>closed/min</small></span><span>{program.wins}<small className="block" style={{ color: "var(--text-mute)" }}>wins</small></span><span style={{ color: program.void_signals ? "var(--gold)" : "var(--text)" }}>{program.void_signals}<small className="block" style={{ color: "var(--text-mute)" }}>excluded</small></span></div></div>)}</div>
        <div className="mt-4"><h3 className="mb-2 text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Open eligible observations</h3>{prospective.open_signals.length ? <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">{prospective.open_signals.map((signal) => <button key={signal.signal_id} type="button" onClick={() => onNavigate?.("Ticker Workbench", signal.ticker)} className="rounded-lg border p-3 text-left" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="flex justify-between text-xs"><b>{signal.ticker}</b><span style={{ color: signal.direction === "long" ? "var(--positive)" : "var(--negative)" }}>{signal.direction.toUpperCase()}</span></div><div className="mt-1 text-[10px]" style={{ color: "var(--text-dim)" }}>{signal.setup_id.replaceAll("_", " ")} · entry {signal.underlying_entry.toFixed(2)} · target {signal.target == null ? "open" : signal.target.toFixed(2)}</div><div className="mt-1 text-[9px]" style={{ color: "var(--text-mute)" }}>observed {when(signal.signal_bar_at)} · option {signal.option_selection_status ?? "unavailable"}</div></button>)}</div> : <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>No eligible prospective signals are open.</p>}</div>
        {prospective.latest_observations.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Latest no-signal and deduplication reasons</summary><div className="mt-2 grid gap-1.5 md:grid-cols-2">{prospective.latest_observations.map((row) => <div key={`${row.program_id}-${row.ticker}`} className="flex justify-between gap-3 rounded border px-2 py-1.5 text-[10px]" style={{ borderColor: "var(--line)" }}><span>{row.ticker} · {row.coverage_status}</span><span className="text-right" style={{ color: "var(--text-mute)" }}>{row.reason.replaceAll("_", " ").toLowerCase()}</span></div>)}</div></details>}
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="3 · Review-worthy setups"><div className="space-y-2 text-[11px]">{data.recent_scans.map((scan) => <button key={scan.id} type="button" onClick={() => onNavigate?.("Setup Scanner", scan.top_ticker ?? undefined)} className="flex w-full justify-between gap-3 text-left"><span>{scan.strategy} · {scan.top_ticker ?? "no leader"}</span><span className="text-right" style={{ color: "var(--text-mute)" }}>{scan.qualified ?? 0} qualified · {when(scan.as_of)}</span></button>)}{!data.recent_scans.length && <p style={{ color: "var(--text-mute)" }}>No saved scan runs yet.</p>}</div></Card>
        <Card title={`Significant ${ticker} flow`}><p className="mb-2 text-[10px]" style={{ color: "var(--text-mute)" }}>{data.significant_flow.source ?? "unavailable"} · {data.significant_flow.session_date ?? "no session"} · newest {when(data.significant_flow.as_of)}</p><div className="space-y-1.5">{flow.slice(0, 5).map((row) => <button key={`${row.contract}-${row.time}`} type="button" onClick={() => onNavigate?.("Spyglass", ticker)} className="flex w-full justify-between text-left text-[11px]"><span>{row.type.toUpperCase()} {row.strike} · {row.side === "unknown" ? "side unknown" : row.side}</span><span>{money(row.premium)}</span></button>)}{!flow.length && <p className="text-[11px]" style={{ color: flowAvailability === "unavailable" ? "var(--negative)" : flowAvailability === "refreshing" ? "var(--gold)" : "var(--text-mute)" }}>{flowAvailability === "refreshing" ? "Provider refresh pending; flow is unknown, not zero." : flowAvailability === "unavailable" ? "Flow unavailable; no observations are being represented." : "No captured prints above $100K for the available session."}</p>}{data.significant_flow.caveat && <p className="text-[9px]" style={{ color: "var(--text-mute)" }}>{data.significant_flow.caveat}</p>}</div></Card>
        <Card title={`${ticker} public-OI GEX`}><div className="flex justify-between text-xs"><span>Net GEX</span><span>{data.gex_change?.current?.net_gex == null ? "—" : money(data.gex_change.current.net_gex)}</span></div><div className="mt-2 flex justify-between text-[11px]"><span>Since prior capture</span><span style={{ color: (data.gex_change?.change ?? 0) >= 0 ? "var(--positive)" : "var(--negative)" }}>{data.gex_change?.change == null ? "—" : money(data.gex_change.change)}</span></div><p className="mt-3 text-[10px]" style={{ color: "var(--text-mute)" }}>{data.gex_change?.caveat}</p></Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-4">
        <Card title="4 · Broad market"><div className="space-y-2">{data.market.map((row) => <button key={row.ticker} type="button" onClick={() => onNavigate?.("Ticker Workbench", row.ticker)} className="flex w-full justify-between text-left text-xs"><span>{row.ticker} · {row.price?.toFixed(2) ?? (row.availability?.status === "refreshing" ? "refreshing" : "—")}</span><span style={{ color: row.day_change_pct == null ? "var(--text-mute)" : row.day_change_pct >= 0 ? "var(--positive)" : "var(--negative)" }}>{pct(row.day_change_pct)}</span></button>)}</div></Card>
        <Card title="Watchlist movers"><div className="space-y-2">{watchlist.slice(0, 6).map((row) => <button key={row.ticker} type="button" onClick={() => onNavigate?.("Ticker Workbench", row.ticker)} className="flex w-full justify-between text-left text-xs"><span>{row.ticker} · {row.price.toFixed(2)}</span><span style={{ color: row.change >= 0 ? "var(--positive)" : "var(--negative)" }}>{pct(row.change)}</span></button>)}{!watchlist.length && <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>No watchlist quotes available.</p>}</div></Card>
        <Card title="Alerts & declared risk"><div className="space-y-2 text-[11px]"><div className="flex justify-between"><span>Declared positions</span><span>{data.holdings?.positions?.length ?? data.holdings?.open?.length ?? 0}</span></div><div className="flex justify-between"><span>Configured alerts</span><span>{data.alerts.rules.length}</span></div><div className="flex justify-between"><span>Triggered state</span><span>{data.alerts.rules.filter((rule) => (rule.evaluation as { status?: string } | undefined)?.status === "triggered").length}</span></div></div></Card>
        <Card title="Input freshness"><div className="space-y-1.5 text-[11px]">{data.freshness.items.map((item) => <div key={item.name} className="flex justify-between gap-3"><span>{item.name.replaceAll("_", " ")}</span><span style={{ color: item.state === "current" ? "var(--positive)" : item.state === "last_session" ? "var(--text-mute)" : "var(--gold)" }}>{item.state.replaceAll("_", " ")}</span></div>)}</div></Card>
      </div>

      <Card title="5 · Six shadow portfolios"><div className="mb-3 flex justify-between text-xs"><span>Combined marked equity</span><span>{money(data.paper_portfolios.combined_marked_equity)}</span></div><div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">{data.paper_portfolios.portfolios.map((row) => <button type="button" key={row.portfolio_id} onClick={() => onNavigate?.("Paper Portfolios")} className="flex justify-between rounded-lg border px-3 py-2 text-left text-[11px]" style={{ borderColor: row.risk_state.daily_loss_locked ? "var(--negative)" : "var(--line)", background: "var(--panel-2)" }}><span>{row.strategy}{row.risk_state.daily_loss_locked ? " · LOCKED" : ""}</span><span style={{ color: row.realized_pnl + row.unrealized_pnl_mid >= 0 ? "var(--positive)" : "var(--negative)" }}>{money(row.realized_pnl + row.unrealized_pnl_mid)} · {row.wins}/{row.closed_trades}</span></button>)}</div></Card>
      <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>Research-only decision support. Shadow portfolios cannot submit broker orders.</p>
    </div>
  );
}
