"use client";

import { useEffect, useState } from "react";
import {
  fetchAutopilotStatus,
  fetchPaperPortfolios,
  fetchProspectiveFronttests,
  type AutopilotStatus,
  type PaperPortfoliosResponse,
  type ProspectiveFronttestsResponse,
} from "@/lib/api";

const money = (value: number) => value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const text = (value: unknown) => value == null ? "—" : String(value);
const percent = (value: unknown) => typeof value === "number" ? `${value.toFixed(2)}%` : "—";

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return <div className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}>
    <div className="text-[9px] font-bold uppercase tracking-[.12em]" style={{ color: "var(--text-mute)" }}>{label}</div>
    <div className="mt-1 text-sm font-semibold" style={{ color: tone }}>{value}</div>
  </div>;
}

export function PaperPortfolios() {
  const [data, setData] = useState<PaperPortfoliosResponse | null>(null);
  const [prospective, setProspective] = useState<ProspectiveFronttestsResponse | null>(null);
  const [autopilot, setAutopilot] = useState<AutopilotStatus | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const ctrl = new AbortController();
    const load = () => Promise.all([fetchPaperPortfolios(ctrl.signal), fetchProspectiveFronttests(ctrl.signal), fetchAutopilotStatus(ctrl.signal)]).then(([portfolios, fronttests, autopilotStatus]) => {
      setData(portfolios);
      setProspective(fronttests);
      setAutopilot(autopilotStatus);
    }).catch((err) => {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Paper portfolios unavailable");
    });
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => { window.clearInterval(timer); ctrl.abort(); };
  }, []);
  if (error) return <div style={{ color: "var(--negative)" }}>{error}</div>;
  if (!data) return <div style={{ color: "var(--text-mute)" }}>Loading shadow portfolios…</div>;
  return (
    <div className="space-y-4" style={{ fontFamily: "var(--font-mono)" }}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-semibold">Paper Portfolios</h1><p className="text-[11px]" style={{ color: "var(--text-mute)" }}>{data.caveat}</p></div>
        <div className="text-right text-xs"><div>{money(data.combined_marked_equity)} marked</div><div style={{ color: data.combined_realized_pnl >= 0 ? "var(--positive)" : "var(--negative)" }}>{money(data.combined_realized_pnl)} realized</div></div>
      </div>
      <section className="rounded-xl border p-4" style={{ borderColor: autopilot?.executor.reachable ? "var(--line)" : "var(--gold)", background: "var(--panel)" }}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h2 className="text-sm font-semibold">Paper autopilot control plane</h2><p className="text-[10px]" style={{ color: "var(--text-mute)" }}>Premarket plan → closed-bar confirmation → simulated fill → bounded exit. No broker-order capability.</p></div>
          <span className="rounded-full border px-2.5 py-1 text-[9px] font-bold uppercase" style={{ borderColor: "var(--line)", color: autopilot?.executor.reachable ? "var(--positive)" : "var(--gold)" }}>{autopilot ? `${autopilot.phase.replaceAll("_", " ")} · ${autopilot.executor.reachable ? "healthy" : "offline"}` : "status unavailable"}</span>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Marked equity" value={money(data.combined_marked_equity)} />
          <Stat label="Liquidation equity" value={money(data.combined_liquidation_equity)} />
          <Stat label="Today realized" value={money(data.daily_realized_pnl)} tone={data.daily_realized_pnl >= 0 ? "var(--positive)" : "var(--negative)"} />
          <Stat label="Open mid P&L" value={money(data.combined_unrealized_pnl_mid)} tone={data.combined_unrealized_pnl_mid >= 0 ? "var(--positive)" : "var(--negative)"} />
          <Stat label="Plan candidates" value={autopilot?.plan.candidate_count ?? "—"} />
          <Stat label="Plan state" value={autopilot?.plan.state ?? "—"} />
          <Stat label="Last action" value={autopilot?.scheduler.action.replaceAll("_", " ") ?? "—"} />
          <Stat label="Learning" value={autopilot ? `${autopilot.learning.samples} / 100` : "—"} />
        </div>
        {autopilot?.scheduler.reason && <p className="mt-2 rounded-lg border px-3 py-2 text-[10px]" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>Blocked: {autopilot.scheduler.reason.replaceAll("_", " ")}</p>}
        {autopilot && <p className="mt-2 text-[9px]" style={{ color: "var(--text-mute)" }}>Decision trace: {autopilot.daily_trace.cycles} cycles · premarket plan {autopilot.daily_trace.premarket_plan_observed ? "captured" : "missing"} · confirmation {autopilot.daily_trace.confirmation_cycle_observed ? "captured" : "missing"} · paper submissions {autopilot.daily_trace.paper_submissions}</p>}
      </section>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Signals" value={data.opportunity_summary.signals} />
        <Stat label="Resolved" value={data.opportunity_summary.resolved} />
        <Stat label="Tracking" value={data.opportunity_summary.tracking} />
        <Stat label="Targets" value={data.opportunity_summary.targets} tone="var(--positive)" />
        <Stat label="Invalidated" value={data.opportunity_summary.invalidations} tone="var(--negative)" />
        <Stat label="Expired" value={data.opportunity_summary.session_expired} />
        <Stat label="Skipped → target" value={data.opportunity_summary.skipped_targets} tone="var(--positive)" />
        <Stat label="Skipped → invalid" value={data.opportunity_summary.skipped_invalidations} tone="var(--negative)" />
      </div>
      <p className="rounded-lg border px-3 py-2 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>
        Opportunity outcomes measure the underlying path after every signal—including blocked signals. They are not hypothetical option fills or P&amp;L.
      </p>
      <section className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold">Comparable strategy cohorts</h2><p className="text-[10px]" style={{ color: "var(--text-mute)" }}>{data.normalized_comparison.caveat}</p></div><span className="rounded-full border px-2.5 py-1 text-[9px] font-bold" style={{ borderColor: "var(--line)", color: "var(--gold)" }}>NO RANKING YET</span></div>
        <div className="mt-3 overflow-x-auto"><table aria-label="Normalized strategy comparison" className="w-full text-left text-[10px]"><thead><tr>{["strategy","closed sample","sample status","win rate","average option return","profit factor","rank eligible"].map((h) => <th key={h} className="pr-3 py-1">{h}</th>)}</tr></thead><tbody>{data.normalized_comparison.rows.map((row) => <tr key={row.portfolio_id}><td className="pr-3 py-1 font-semibold">{row.strategy}</td><td className="pr-3 py-1">{row.closed_sample}/{row.minimum_sample}</td><td className="pr-3 py-1" style={{ color: row.sample_status === "USABLE" ? "var(--positive)" : "var(--gold)" }}>{row.sample_status}</td><td className="pr-3 py-1">{percent(row.win_rate)}</td><td className="pr-3 py-1">{percent(row.average_option_return_pct)}</td><td className="pr-3 py-1">{row.profit_factor_on_return_units?.toFixed(2) ?? "—"}</td><td className="pr-3 py-1">{row.rank_eligible ? "yes" : "no"}</td></tr>)}</tbody></table></div>
      </section>
      {prospective && <section className="space-y-3 rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div>
          <h2 className="text-sm font-semibold">Prospective programs</h2>
          <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>{prospective.caveat}</p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
          <Stat label="Observed" value={prospective.latest_coverage.observed} />
          <Stat label="Fresh" value={prospective.latest_coverage.fresh} tone="var(--positive)" />
          <Stat label="Partial" value={prospective.latest_coverage.partial} />
          <Stat label="Stale" value={prospective.latest_coverage.stale} tone="var(--negative)" />
          <Stat label="Missing" value={prospective.latest_coverage.missing} tone="var(--negative)" />
          <Stat label="Opened" value={prospective.latest_coverage.signals_opened} />
          <Stat label="Open option mark" value={money(prospective.open_option_mark_pnl)} tone={prospective.open_option_mark_pnl >= 0 ? "var(--positive)" : "var(--negative)"} />
          <Stat label="Spread ceiling" value={`${prospective.option_liquidity_policy.maximum_entry_spread_pct}%`} />
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          {prospective.programs.map((program) => <div key={program.program_id} className="rounded-lg border p-3" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}>
            <div className="flex items-start justify-between gap-3"><div><div className="text-xs font-semibold">{program.name}</div><div className="text-[9px]" style={{ color: "var(--text-mute)" }}>{program.program_id} · rule {program.configuration_sha256.slice(0, 10)}</div></div><span className="rounded-full border px-2 py-1 text-[9px] font-bold" style={{ borderColor: "var(--line)", color: program.effective_status === "COLLECTING" ? "var(--positive)" : "var(--text-dim)" }}>{program.effective_status}</span></div>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5"><Stat label="Eligible" value={program.eligible_signals} /><Stat label="Void / audit" value={program.void_signals} tone={program.void_signals ? "var(--negative)" : undefined} /><Stat label="Closed / min" value={`${program.closed_signals}/${program.minimum_sample}`} /><Stat label="Realized option" value={money(program.closed_option_pnl)} tone={program.closed_option_pnl >= 0 ? "var(--positive)" : "var(--negative)"} /><Stat label="Open option mark" value={money(program.open_option_mark_pnl)} tone={program.open_option_mark_pnl >= 0 ? "var(--positive)" : "var(--negative)"} /></div>
            <div className="mt-2 h-1 overflow-hidden rounded-full" style={{ background: "var(--line)" }}><div className="h-full" style={{ width: `${program.sample_progress * 100}%`, background: "var(--accent)" }} /></div>
          </div>)}
        </div>
        {prospective.signals.length > 0 && <div className="overflow-x-auto"><table aria-label="Recent prospective signals" className="w-full text-left text-[10px]"><thead><tr>{["signal_bar_at", "ticker", "setup_id", "direction", "status", "outcome", "gross_underlying_return_pct", "option_selection_status"].map((h) => <th key={h} className="pr-3 py-1">{h.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{prospective.signals.map((row, i) => <tr key={text(row.signal_id) + i}>{["signal_bar_at", "ticker", "setup_id", "direction", "status", "outcome"].map((h) => <td key={h} className="pr-3 py-1">{text(row[h])}</td>)}<td className="pr-3 py-1">{percent(row.gross_underlying_return_pct)}</td><td className="pr-3 py-1">{text(row.option_selection_status)}</td></tr>)}</tbody></table></div>}
        {prospective.option_legs.length > 0 && <details open><summary className="cursor-pointer text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Observed option legs and current marks</summary><div className="mt-2 overflow-x-auto"><table aria-label="Prospective option marks" className="w-full text-left text-[10px]"><thead><tr>{["contract","status","entry_fill","mark_mid","unrealized_pnl_mid","liquidation_pnl","entry_spread_pct","mark_status"].map((h) => <th key={h} className="pr-3 py-1">{h.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{prospective.option_legs.map((row, i) => <tr key={text(row.leg_id) + i}>{["contract","status","entry_fill","mark_mid","unrealized_pnl_mid","liquidation_pnl","entry_spread_pct","mark_status"].map((h) => <td key={h} className="pr-3 py-1">{text(row[h])}</td>)}</tr>)}</tbody></table></div></details>}
        {prospective.observations.length > 0 && <details><summary className="cursor-pointer text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Observation coverage and no-signal reasons</summary><div className="mt-2 overflow-x-auto"><table aria-label="Prospective observation coverage" className="w-full text-left text-[10px]"><thead><tr>{["observed_at", "program_id", "ticker", "coverage_status", "decision", "reason", "latest_bar_at"].map((h) => <th key={h} className="pr-3 py-1">{h.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{prospective.observations.map((row, i) => <tr key={text(row.observation_id) + i}>{["observed_at", "program_id", "ticker", "coverage_status", "decision", "reason", "latest_bar_at"].map((h) => <td key={h} className="pr-3 py-1">{text(row[h])}</td>)}</tr>)}</tbody></table></div></details>}
      </section>}
      <div className="grid gap-4 xl:grid-cols-2">
        {data.portfolios.map((portfolio) => (
          <details key={portfolio.portfolio_id} className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
            <summary className="cursor-pointer list-none">
              <div className="flex items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{portfolio.strategy}</span>{portfolio.enabled === false && <span className="rounded-full border px-2 py-0.5 text-[8px] font-bold" style={{ borderColor: "var(--text-dim)", color: "var(--text-dim)" }}>DISABLED</span>}{portfolio.risk_state.daily_loss_locked && <span className="rounded-full border px-2 py-0.5 text-[8px] font-bold" style={{ borderColor: "var(--negative)", color: "var(--negative)" }}>DAILY LOSS LOCK</span>}{portfolio.risk_state.stale_open_marks > 0 && <span className="rounded-full border px-2 py-0.5 text-[8px] font-bold" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>STALE MARK</span>}</div><div className="text-[10px]" style={{ color: "var(--text-mute)" }}>{portfolio.portfolio_id} · {text(portfolio.config.symbol)} · {text(portfolio.config.timeframe_minutes)}m{portfolio.enabled === false ? " · turned off, not receiving signals" : ""}</div></div><div className="text-right text-xs"><div>{money(portfolio.marked_equity)} marked</div><div style={{ color: portfolio.realized_pnl + portfolio.unrealized_pnl_mid >= 0 ? "var(--positive)" : "var(--negative)" }}>{money(portfolio.realized_pnl + portfolio.unrealized_pnl_mid)} total</div></div></div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-6" style={{ color: "var(--text-mute)" }}><span>{portfolio.wins}/{portfolio.closed_trades} wins</span><span>{portfolio.open_positions} open</span><span>{money(portfolio.daily_realized_pnl)} today</span><span>{money(portfolio.unrealized_pnl_mid)} open mid</span><span>{portfolio.opportunity_summary.targets} targets</span><span>{portfolio.opportunity_summary.skipped_targets} blocked winners</span></div>
            </summary>
            <div className="mt-4 space-y-4 border-t pt-3" style={{ borderColor: "var(--line)" }}>
              <div><h3 className="mb-2 text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Rules and current risk state</h3><div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4 xl:grid-cols-8"><Stat label="Risk / entry" value={percent((portfolio.config.risk_fraction as number | undefined) != null ? Number(portfolio.config.risk_fraction) * 100 : null)} /><Stat label="Timeframe" value={`${text(portfolio.config.timeframe_minutes)}m`} /><Stat label="DTE" value={`${text(portfolio.config.min_dte)}–${text(portfolio.config.max_dte)}`} /><Stat label="Max spread" value={`${text(portfolio.config.max_spread_pct)}%`} /><Stat label="Entry window" value={`${text(portfolio.config.entry_start_et)}–${text(portfolio.config.entry_cutoff_et)}`} /><Stat label="Loss lock" value={`${portfolio.daily_losses}/${text(portfolio.config.stop_after_daily_losses)}`} tone={portfolio.risk_state.daily_loss_locked ? "var(--negative)" : undefined} /><Stat label="Entries today" value={`${portfolio.daily_entries}/${text(portfolio.config.maximum_new_positions_per_day)}`} /><Stat label="Flip cooldown" value={`${text(portfolio.config.direction_flip_cooldown_minutes)}m`} /></div></div>
              <div><h3 className="mb-2 text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Recent signals and subsequent path</h3>{portfolio.signals.length ? <div className="overflow-x-auto"><table aria-label={`${portfolio.strategy} recent signals`} className="w-full text-left text-[10px]"><thead><tr>{["signal_at","setup_id","direction","disposition","skip_reason","outcome","canonical_signal_id","evidence_snapshot_ids","mfe_pct","mae_pct"].map((h) => <th key={h} scope="col" className="pr-3 py-1">{h.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{portfolio.signals.map((row, i) => <tr key={text(row.signal_id) + i}>{["signal_at","setup_id","direction","disposition","skip_reason","outcome","canonical_signal_id","evidence_snapshot_ids"].map((h) => <td key={h} className="pr-3 py-1">{text(row[h])}</td>)}<td className="pr-3 py-1">{percent(row.mfe_pct)}</td><td className="pr-3 py-1">{percent(row.mae_pct)}</td></tr>)}</tbody></table></div> : <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>No signals recorded yet.</p>}</div>
              <div><h3 className="mb-2 text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>Positions / fills / marked P&amp;L</h3>{portfolio.positions.length ? <div className="overflow-x-auto"><table className="w-full text-left text-[10px]"><thead><tr>{["status","structure","contract","short_contract","quantity","entry_fill","mark_mid","unrealized_pnl_mid","liquidation_pnl","mark_status","exit_reason","pnl"].map((h) => <th key={h} className="pr-3 py-1">{h.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{portfolio.positions.map((row, i) => <tr key={text(row.position_id) + i}>{["status","structure","contract","short_contract","quantity","entry_fill","mark_mid","unrealized_pnl_mid","liquidation_pnl","mark_status","exit_reason","pnl"].map((h) => <td key={h} className="pr-3 py-1">{text(row[h])}</td>)}</tr>)}</tbody></table></div> : <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>No simulated positions yet.</p>}</div>
            </div>
          </details>
        ))}
      </div>
      <div className="rounded-lg border px-3 py-2 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>PAPER ONLY · READ ONLY · EXECUTION CAPABILITY: FALSE · last pass {data.as_of ?? "unavailable"}</div>
    </div>
  );
}
