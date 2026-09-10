"use client";

import { useEffect, useState } from "react";
import { fetchEarningsRadar, type EarningsRadarResponse } from "@/lib/api";
import { LoadingStatus } from "@/components/ui/skeleton";

const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(0)}%`;
const number = (value: number | null | undefined, digits = 2) => value == null ? "—" : Number(value).toFixed(digits);

export function EarningsRadar() {
  const [data, setData] = useState<EarningsRadarResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const ctrl = new AbortController();
    const load = () => fetchEarningsRadar(ctrl.signal).then((next) => {
      if (!ctrl.signal.aborted) { setData(next); setError(""); }
    }).catch((err) => {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Earnings radar unavailable");
    });
    void load();
    const timer = window.setInterval(() => void load(), 5 * 60_000);
    return () => { window.clearInterval(timer); ctrl.abort(); };
  }, []);
  if (error && !data) return <div style={{ color: "var(--negative)" }}>{error} — retrying automatically.</div>;
  if (!data) return <LoadingStatus style={{ color: "var(--text-mute)" }}>Loading earnings radar…</LoadingStatus>;
  return (
    <div className="space-y-4" style={{ fontFamily: "var(--font-mono)" }}>
      {error && <p role="status" style={{ color: "var(--gold)" }}>Refresh failed: {error}. Showing the last successful radar with its original timestamp; retrying automatically.</p>}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-semibold">Earnings Radar</h1>
          <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>Upcoming reports, expected underlying gap, and evidence-gated paper decisions. Refreshed daily at 08:15 ET.</p>
        </div>
        <div className="text-right text-xs">
          <span className="rounded-full border px-2.5 py-1 text-[9px] font-bold uppercase" style={{ borderColor: "var(--line)", color: data.status === "current" ? "var(--positive)" : data.status === "stale" ? "var(--gold)" : "var(--negative)" }}>
            {data.status === "current" ? `current · ${data.count} names` : data.status === "partial" ? `partial data · ${data.count} names` : data.status === "stale" ? `stale · ${data.age_hours?.toFixed(1)}h old` : "unavailable"}
          </span>
          {data.as_of && <div className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>radar as of {data.as_of}</div>}
        </div>
      </div>
      {(data.status === "unavailable" || data.status === "partial") && <p className="border px-3 py-2 text-[11px]" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>{data.reason ?? "No radar artifact available yet."} Missing data is shown as unavailable, never zero.</p>}
      {data.validation && <p className="border px-3 py-2 text-[10px]" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>
        {data.validation.strategy_gate ?? "UNVALIDATED_FOR_LIVE_OPTIONS_PNL"} · day-5 {number(data.validation.day5_direction_accuracy_pct)}% vs baseline {number(data.validation.day5_baseline_accuracy_pct)}% · gated {number(data.validation.day5_gated_accuracy_pct)}% (N={data.validation.day5_gated_samples ?? "—"}) · gap MAE {number(data.validation.expected_gap_mae_pct)}% vs {number(data.validation.expected_gap_baseline_mae_pct)}% baseline · holdout N={data.validation.test_samples ?? "—"}
      </p>}
      {data.paper_scorecard && <section className="border p-3" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div className="grid grid-cols-2 gap-3 text-[10px] sm:grid-cols-5">
          <span><small className="block" style={{ color: "var(--text-mute)" }}>Open</small>{data.paper_scorecard.open}</span>
          <span><small className="block" style={{ color: "var(--text-mute)" }}>Settled</small>{data.paper_scorecard.settled}</span>
          <span><small className="block" style={{ color: "var(--text-mute)" }}>Estimated wins</small>{data.paper_scorecard.wins}</span>
          <span><small className="block" style={{ color: "var(--text-mute)" }}>Estimated win rate</small>{number(data.paper_scorecard.win_rate_pct)}%</span>
          <span><small className="block" style={{ color: "var(--text-mute)" }}>Estimated P&amp;L</small>${number(data.paper_scorecard.realized_pnl)}</span>
        </div>
        <p className="mt-2 text-[9px]" style={{ color: "var(--text-mute)" }}>
          {data.paper_scorecard.cohorts.map((row) => `${row.model_version}: ${row.settled} settled`).join(" · ") || "No model cohorts yet"}. Legacy entries use heuristic premiums; new entries require the independent holdout gate. UNVALIDATED_FOR_LIVE_OPTIONS_PNL.
        </p>
      </section>}
      {data.cards.length === 0 && data.status === "current" && <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>No earnings scheduled inside the scan window.</p>}
      {data.cards.length > 0 && <div className="overflow-x-auto overflow-y-auto border" role="region" aria-label="Upcoming earnings radar scrollport" style={{ borderColor: "var(--line)" }}>
        <table aria-label="Upcoming earnings radar" className="w-full text-left text-[10px]">
          <thead><tr className="border-b" style={{ borderColor: "var(--line)" }}>
            {["symbol", "reports / in", "est eps", "hist beat", "forecast status", "raw 5D up probability", "exp gap", "reversal risk", "paper decision", "rationale"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}
          </tr></thead>
          <tbody>{data.cards.map((card) => (
            <tr key={`${card.symbol}-${card.scheduled_date}`} className="border-b align-top" style={{ borderColor: "var(--line)" }}>
              <td className="px-3 py-2 font-semibold">{card.symbol}</td>
              <td className="px-3 py-2">{card.scheduled_date} · {card.days_until}d<br /><small style={{ color: "var(--text-mute)" }}>{card.earnings_date_confirmation === "single_source_unconfirmed" ? "Yahoo only · unconfirmed" : "cross-checked"}</small></td>
              <td className="px-3 py-2">{number(card.eps_estimate_avg)}</td>
              <td className="px-3 py-2">{percent(card.hist_beat_rate)} ({card.total_hist_reports})</td>
              <td className="px-3 py-2">{card.forecast_status ?? "UNVALIDATED"}</td>
              <td className="px-3 py-2">{percent(card.prob_day5_up)}</td>
              <td className="px-3 py-2">±{number(card.expected_gap_pct)}%</td>
              <td className="px-3 py-2">{percent(card.reversal_risk_pct)}</td>
              <td className="px-3 py-2">{card.recommended_strategy}</td>
              <td className="px-3 py-2 max-w-[280px]" style={{ color: "var(--text-mute)" }}>{card.rationale}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>}
      <p className="border px-3 py-2 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>{data.caveat} UNVALIDATED_FOR_LIVE_OPTIONS_PNL. Paper-only recommendations — no order authority.</p>
    </div>
  );
}
