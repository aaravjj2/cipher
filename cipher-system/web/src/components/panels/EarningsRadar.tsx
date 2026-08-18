"use client";

import { useEffect, useState } from "react";
import { fetchEarningsRadar, type EarningsRadarResponse } from "@/lib/api";

const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(0)}%`;
const number = (value: number | null | undefined, digits = 2) => value == null ? "—" : Number(value).toFixed(digits);

export function EarningsRadar() {
  const [data, setData] = useState<EarningsRadarResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const ctrl = new AbortController();
    const load = () => fetchEarningsRadar(ctrl.signal).then(setData).catch((err) => {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Earnings radar unavailable");
    });
    void load();
    const timer = window.setInterval(() => void load(), 5 * 60_000);
    return () => { window.clearInterval(timer); ctrl.abort(); };
  }, []);
  if (error) return <div style={{ color: "var(--negative)" }}>{error}</div>;
  if (!data) return <div style={{ color: "var(--text-mute)" }}>Loading earnings radar…</div>;
  return (
    <div className="space-y-4" style={{ fontFamily: "var(--font-mono)" }}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-semibold">Earnings Radar</h1>
          <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>Upcoming earnings in the universe with model direction, expected gap, and recommended defined-risk structure. Refreshed by the daily 08:15 ET digest.</p>
        </div>
        <div className="text-right text-xs">
          <span className="rounded-full border px-2.5 py-1 text-[9px] font-bold uppercase" style={{ borderColor: "var(--line)", color: data.status === "current" ? "var(--positive)" : data.status === "stale" ? "var(--gold)" : "var(--negative)" }}>
            {data.status === "current" ? `current · ${data.count} names` : data.status === "stale" ? `stale · ${data.age_hours?.toFixed(1)}h old` : "unavailable"}
          </span>
          {data.as_of && <div className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>radar as of {data.as_of}</div>}
        </div>
      </div>
      {data.status === "unavailable" && <p className="rounded-lg border px-3 py-2 text-[11px]" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>No radar artifact yet — it is written after the first 08:15 ET digest run. Missing data is shown as unavailable, never zero.</p>}
      {data.cards.length === 0 && data.status !== "unavailable" && <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>No earnings scheduled inside the scan window.</p>}
      {data.cards.length > 0 && <div className="overflow-x-auto rounded-xl border" style={{ borderColor: "var(--line)" }}>
        <table aria-label="Upcoming earnings radar" className="w-full text-left text-[10px]">
          <thead><tr className="border-b" style={{ borderColor: "var(--line)" }}>
            {["symbol", "reports", "in", "est eps", "hist beat", "bias", "confidence", "exp gap", "reversal risk", "recommended", "rationale"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}
          </tr></thead>
          <tbody>{data.cards.map((card) => (
            <tr key={`${card.symbol}-${card.scheduled_date}`} className="border-b align-top" style={{ borderColor: "var(--line)" }}>
              <td className="px-3 py-2 font-semibold">{card.symbol}</td>
              <td className="px-3 py-2">{card.scheduled_date} · {card.days_until}d</td>
              <td className="px-3 py-2">{number(card.eps_estimate_avg)}</td>
              <td className="px-3 py-2">{percent(card.hist_beat_rate)} ({card.total_hist_reports})</td>
              <td className="px-3 py-2">{card.direction_bias}</td>
              <td className="px-3 py-2">{percent(card.confidence)}</td>
              <td className="px-3 py-2">±{number(card.expected_gap_pct)}%</td>
              <td className="px-3 py-2">{percent(card.reversal_risk_pct)}</td>
              <td className="px-3 py-2">{card.recommended_strategy}</td>
              <td className="px-3 py-2 max-w-[280px]" style={{ color: "var(--text-mute)" }}>{card.rationale}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>}
      <p className="rounded-lg border px-3 py-2 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>{data.caveat} Paper-only recommendations — no order authority.</p>
    </div>
  );
}
