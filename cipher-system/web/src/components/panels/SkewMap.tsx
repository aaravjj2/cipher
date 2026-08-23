"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchSkewMap, type SkewMapPoint, type SkewMapResponse } from "@/lib/api";

const QUADRANTS: Record<string, { label: string; note: string }> = {
  contrarian_bid: { label: "Contrarian bid", note: "Down tape · calls relatively bid" },
  chase: { label: "Chase", note: "Up tape · calls relatively bid" },
  hedged_rally: { label: "Hedged rally", note: "Up tape · puts relatively bid" },
  fear: { label: "Fear", note: "Down tape · puts relatively bid" },
  insufficient_data: { label: "Incomplete", note: "One or more inputs unavailable" },
};

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const signed = (value: number | null, digits = 1) => value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;

function Plot({ points, selected, onSelect }: { points: SkewMapPoint[]; selected: string; onSelect: (ticker: string) => void }) {
  const usable = points.filter((point) => point.raw_skew != null && point.return_1m_pct != null);
  const maxX = Math.max(8, ...usable.map((point) => Math.abs(point.return_1m_pct ?? 0))) * 1.15;
  const maxY = Math.max(.08, ...usable.map((point) => Math.abs(point.raw_skew ?? 0))) * 1.2;
  return <div className="relative h-[390px] overflow-hidden border border-[var(--line)] bg-[var(--panel)] sm:h-[470px]" role="img" aria-label="One-month return by raw 25-delta put-call implied-volatility skew">
    <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--line)]" />
    <div className="absolute inset-x-0 top-1/2 h-px bg-[var(--line)]" />
    <div className="absolute left-3 top-3 text-[10px] text-[var(--text-mute)]">FEAR</div>
    <div className="absolute right-3 top-3 text-[10px] text-[var(--text-mute)]">HEDGED RALLY</div>
    <div className="absolute bottom-3 left-3 text-[10px] text-[var(--text-mute)]">CONTRARIAN BID</div>
    <div className="absolute bottom-3 right-3 text-[10px] text-[var(--text-mute)]">CHASE</div>
    <div className="absolute bottom-2 left-1/2 -translate-x-1/2 bg-[var(--panel)] px-2 text-[9px] text-[var(--text-mute)]">1M UNDERLYING RETURN →</div>
    <div className="absolute left-1 top-1/2 -translate-y-1/2 -rotate-90 text-[9px] text-[var(--text-mute)]">PUT − CALL IV →</div>
    {usable.map((point) => {
      const left = 50 + clamp((point.return_1m_pct ?? 0) / maxX, -1, 1) * 44;
      const top = 50 - clamp((point.raw_skew ?? 0) / maxY, -1, 1) * 43;
      const active = point.ticker === selected;
      return <button key={point.ticker} type="button" onClick={() => onSelect(point.ticker)} className="absolute -translate-x-1/2 -translate-y-1/2 rounded-sm border px-1.5 py-1 text-[9px] font-bold shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ left: `${left}%`, top: `${top}%`, borderColor: active ? "var(--gold)" : "var(--line-strong)", background: active ? "var(--gold)" : "var(--panel-2)", color: active ? "#16120a" : "var(--text)" }} aria-label={`${point.ticker}, one-month return ${signed(point.return_1m_pct)} percent, raw skew ${signed((point.raw_skew ?? 0) * 100)} volatility points`}>{point.ticker}</button>;
    })}
  </div>;
}

export function SkewMap({ ticker, onNavigate }: { ticker: string; onNavigate?: (panel: string, ticker?: string) => void }) {
  const [payload, setPayload] = useState<SkewMapResponse | null>(null);
  const [selected, setSelected] = useState(ticker);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetchSkewMap(controller.signal).then(setPayload).catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Skew map unavailable"); });
    return () => controller.abort();
  }, []);
  const points = useMemo(() => payload?.points ?? [], [payload]);
  const point = points.find((item) => item.ticker === selected) ?? points[0];
  if (!payload && !error) return <section data-guest-panel="Skew Map" data-guest-source="loading"><p className="text-sm text-[var(--text-mute)]">Aligning stored option surfaces with underlying returns…</p></section>;
  if (!payload) return <section data-guest-panel="Skew Map" data-guest-source="error" className="border border-[var(--line)] bg-[var(--panel)] p-5"><h1 className="text-lg font-semibold">Skew Map</h1><p className="mt-2 text-sm text-[var(--text-dim)]">{error}</p><p className="mt-3 text-xs text-[var(--text-mute)]">No values are synthesized when stored IV or aligned price bars are unavailable.</p></section>;
  return <section className="space-y-3 font-mono" data-testid="skew-map" data-guest-panel="Skew Map" data-guest-source="live">
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-[var(--line)] pb-3">
      <div><p className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--gold)]">Options positioning · research only</p><h1 className="mt-1 text-xl font-semibold">Skew Map</h1><p className="mt-1 max-w-3xl text-[11px] leading-5 text-[var(--text-mute)]">25-delta put IV minus mirrored 25-delta call IV against the underlying&apos;s aligned one-month return. This narrows where to research; it is not a forecast or entry signal.</p></div>
      <div className="text-right text-[10px] text-[var(--text-mute)]">Surface {payload.as_of?.slice(0, 10) ?? "—"}<br />{points.length} names · stored observations</div>
    </header>
    <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Plot points={points} selected={point?.ticker ?? selected} onSelect={setSelected} />
      <aside className="border border-[var(--line)] bg-[var(--panel)] p-4">
        {point ? <><div className="flex items-start justify-between gap-2"><div><h2 className="text-lg font-semibold">{point.ticker}</h2><p className="text-[10px] text-[var(--text-mute)]">{point.sector} · {point.front_expiry ?? "expiry unavailable"}</p></div><span className="border border-[var(--line)] px-2 py-1 text-[9px] uppercase text-[var(--gold)]">{point.quality}</span></div>
        <dl className="mt-5 grid grid-cols-2 gap-px bg-[var(--line)] border border-[var(--line)] text-[10px]">{[["1M return", `${signed(point.return_1m_pct)}%`], ["Raw skew", `${signed(point.raw_skew == null ? null : point.raw_skew * 100)} vol pts`], ["Normalized", signed(point.normalized_skew, 2)], ["ATM IV", point.atm_iv == null ? "—" : `${(point.atm_iv * 100).toFixed(1)}%`], ["IV coverage", `${(point.iv_coverage * 100).toFixed(0)}%`], ["Quote coverage", `${(point.quote_coverage * 100).toFixed(0)}%`]].map(([label, value]) => <div key={label} className="bg-[var(--panel-2)] p-2"><dt className="text-[var(--text-mute)]">{label}</dt><dd className="mt-1 text-[var(--text)]">{value}</dd></div>)}</dl>
        <div className="mt-4"><p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--text-mute)]">Reading</p><p className="mt-2 text-sm text-[var(--text)]">{QUADRANTS[point.quadrant]?.label ?? point.quadrant}</p><p className="mt-1 text-[10px] leading-5 text-[var(--text-dim)]">{QUADRANTS[point.quadrant]?.note}</p></div>
        {point.quality_reasons.length > 0 && <div className="mt-4 border-l-2 border-[var(--gold)] pl-3 text-[10px] leading-5 text-[var(--text-dim)]">{point.quality_reasons.join(" · ")}</div>}
        <button type="button" onClick={() => onNavigate?.("Options Terminal", point.ticker)} className="mt-5 w-full bg-[var(--gold)] px-3 py-2 text-xs font-bold text-[#16120a] hover:bg-[var(--gold-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white">Inspect chain</button></> : <p className="text-xs text-[var(--text-mute)]">No stored observations.</p>}
      </aside>
    </div>
    <div className="grid gap-3 border border-[var(--line)] bg-[var(--panel)] p-3 text-[10px] leading-5 text-[var(--text-dim)] md:grid-cols-3"><p><strong className="text-[var(--text)]">Raw first.</strong> Compare volatility-point differences across unlike names.</p><p><strong className="text-[var(--text)]">Normalize carefully.</strong> Use skew / ATM IV mainly within similar names or sectors.</p><p><strong className="text-[var(--text)]">Event check required.</strong> Earnings can dominate the expiry&apos;s premium.</p></div>
    <p className="text-[10px] leading-5 text-[var(--text-mute)]">{payload.caveat} Missing values remain unavailable. No order capability.</p>
  </section>;
}
