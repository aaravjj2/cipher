"use client";

import { useEffect, useMemo, useState } from "react";
import {
  fetchHoldings,
  addHolding,
  closeHolding,
  deleteHolding,
  fetchBars,
  type HoldingsStatus,
  type HoldingPosition,
  type ClosedHoldingPosition,
} from "@/lib/api";

/**
 * Holdings panel — manually-entered positions (core/holdings.py), never connected to
 * a real brokerage or exchange account. The user tells it what they hold; Cipher marks
 * it to market with the same quote()/bars() every other panel already uses. Replaces
 * the "connect your broker" idea a competitor analysis surfaced — that's a step toward
 * account-level integration this app deliberately never takes.
 */

const POLL_MS = 20_000;

function formatSignedDollars(value: number | null): string {
  if (value == null) return "—";
  const sign = value < 0 ? "-" : "+";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatDollars(value: number | null): string {
  if (value == null) return "—";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatPct(value: number | null): string {
  if (value == null) return "—";
  const sign = value < 0 ? "" : "+";
  return `${sign}${value.toFixed(2)}%`;
}

// Cipher convention: purple = profit, red = loss (not the conventional green/red;
// see DayPnlBadge precedent in the Standing/Journal panels).
function pnlColor(value: number | null): string {
  if (value == null) return "var(--text-mute)";
  return value >= 0 ? "var(--accent)" : "var(--neg)";
}

// ---------------------------------------------------------------------------
// Small building blocks
// ---------------------------------------------------------------------------

function Section({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="flex flex-row items-center justify-between gap-3">
        <h2 className="text-[13px] font-bold uppercase" style={{ letterSpacing: "0.06em", color: "var(--text)" }}>
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[12.5px] italic" style={{ color: "var(--text-mute)" }}>
      {children}
    </p>
  );
}

function StatTile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-[10px] px-4 py-3" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>
        {label}
      </span>
      <span className="text-[17px] font-bold" style={{ fontFamily: "var(--font-mono)", color: color || "var(--text)" }}>
        {value}
      </span>
    </div>
  );
}

function textInputStyle(): React.CSSProperties {
  return { background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" };
}

// ---------------------------------------------------------------------------
// Add-position form
// ---------------------------------------------------------------------------

function AddPositionForm({ onAdded }: { onAdded: () => void }) {
  const [ticker, setTicker] = useState("");
  const [shares, setShares] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [entryDate, setEntryDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await addHolding({
        ticker: ticker.trim().toUpperCase(),
        shares: Number(shares),
        entry_price: Number(entryPrice),
        entry_date: entryDate,
        notes: notes.trim() || undefined,
      });
      setTicker("");
      setShares("");
      setEntryPrice("");
      setNotes("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add position");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-row flex-wrap items-end gap-3 rounded-[10px] p-4" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      <label className="flex flex-col gap-1">
        <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Ticker</span>
        <input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="NVDA" required
          className="w-[90px] rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Shares</span>
        <input value={shares} onChange={(e) => setShares(e.target.value)} type="number" step="any" min="0" placeholder="10" required
          className="w-[90px] rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Entry price</span>
        <input value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} type="number" step="any" min="0" placeholder="120.50" required
          className="w-[110px] rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Entry date</span>
        <input value={entryDate} onChange={(e) => setEntryDate(e.target.value)} type="date" required
          className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <label className="flex flex-col gap-1 flex-1 min-w-[160px]">
        <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Notes (optional)</span>
        <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Why this position?"
          className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <button type="submit" disabled={saving}
        className="rounded-[8px] px-[16px] py-[8px] text-[12.5px] font-bold shrink-0 disabled:opacity-60"
        style={{ background: "var(--accent)", color: "#fff" }}>
        {saving ? "Adding…" : "Add position"}
      </button>
      {error && <span className="text-[11.5px] w-full" style={{ color: "var(--neg)" }}>{error}</span>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Close-position inline form
// ---------------------------------------------------------------------------

function ClosePositionForm({ position, onDone, onCancel }: { position: HoldingPosition; onDone: () => void; onCancel: () => void }) {
  const [exitPrice, setExitPrice] = useState("");
  const [exitDate, setExitDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [shares, setShares] = useState(String(position.shares));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const requestedShares = Number(shares);
      await closeHolding({
        id: position.id,
        exit_price: Number(exitPrice),
        exit_date: exitDate,
        shares: requestedShares === position.shares ? undefined : requestedShares,
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to close position");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-row flex-wrap items-end gap-3 rounded-[8px] p-3 mt-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Shares to sell</span>
        <input value={shares} onChange={(e) => setShares(e.target.value)} type="number" step="any" min="0" max={position.shares}
          className="w-[90px] rounded-[8px] px-[8px] py-[5px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Exit price</span>
        <input value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} type="number" step="any" min="0" required
          className="w-[100px] rounded-[8px] px-[8px] py-[5px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Exit date</span>
        <input value={exitDate} onChange={(e) => setExitDate(e.target.value)} type="date" required
          className="rounded-[8px] px-[8px] py-[5px] text-[12px] outline-none" style={textInputStyle()} />
      </label>
      <button type="submit" disabled={saving} className="rounded-[8px] px-[12px] py-[6px] text-[11.5px] font-bold disabled:opacity-60" style={{ background: "var(--accent)", color: "#fff" }}>
        {saving ? "Saving…" : "Confirm sale"}
      </button>
      <button type="button" onClick={onCancel} className="rounded-[8px] px-[12px] py-[6px] text-[11.5px] font-bold" style={{ background: "transparent", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
        Cancel
      </button>
      {error && <span className="text-[11px] w-full" style={{ color: "var(--neg)" }}>{error}</span>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Open positions table
// ---------------------------------------------------------------------------

const OPEN_GRID = "1fr 70px 90px 90px 90px 100px 110px 90px 130px";

function OpenPositionRow({ position, onChanged }: { position: HoldingPosition; onChanged: () => void }) {
  const [closing, setClosing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function remove() {
    setDeleting(true);
    try {
      await deleteHolding(position.id);
      onChanged();
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="flex flex-col">
      <div className="grid items-center gap-2 px-2 py-2 rounded-[8px]" style={{ gridTemplateColumns: OPEN_GRID, background: "var(--panel-2)" }}>
        <span className="text-[13px] font-semibold" style={{ color: "var(--text)" }}>{position.ticker}</span>
        <span className="text-[12px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{position.shares}</span>
        <span className="text-[12px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{formatDollars(position.entry_price)}</span>
        <span className="text-[11px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>{position.entry_date}</span>
        <span className="text-[12px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
          {position.current_price != null ? formatDollars(position.current_price) : "…"}
        </span>
        <span className="text-[12px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
          {position.market_value != null ? formatDollars(position.market_value) : "…"}
        </span>
        <span className="text-[12px] font-semibold" style={{ fontFamily: "var(--font-mono)", color: pnlColor(position.unrealized_pnl_dollars) }}>
          {formatSignedDollars(position.unrealized_pnl_dollars)}
        </span>
        <span className="text-[12px] font-semibold" style={{ fontFamily: "var(--font-mono)", color: pnlColor(position.unrealized_pnl_pct) }}>
          {formatPct(position.unrealized_pnl_pct)}
        </span>
        <div className="flex flex-row gap-2 justify-end">
          <button type="button" onClick={() => setClosing((v) => !v)} className="text-[11px] font-bold rounded-[6px] px-2 py-1" style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
            {closing ? "Close" : "Sell"}
          </button>
          <button type="button" onClick={remove} disabled={deleting} aria-label={`Delete ${position.ticker} entry`}
            className="text-[11px] rounded-[6px] px-2 py-1 disabled:opacity-50" style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--neg)" }}>
            ×
          </button>
        </div>
      </div>
      {position.quote_error && (
        <span className="text-[10.5px] mt-1 px-2" style={{ color: "var(--text-mute)" }}>quote unavailable for {position.ticker}</span>
      )}
      {closing && (
        <ClosePositionForm position={position} onDone={() => { setClosing(false); onChanged(); }} onCancel={() => setClosing(false)} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Allocation breakdown — magnitude ranking, single hue, direct labels
// ---------------------------------------------------------------------------

function AllocationBars({ allocation }: { allocation: HoldingsStatus["allocation"] }) {
  const sorted = useMemo(() => [...allocation].sort((a, b) => b.weight_pct - a.weight_pct), [allocation]);
  return (
    <div className="flex flex-col gap-2">
      {sorted.map((row) => (
        <div key={row.ticker} className="flex flex-row items-center gap-3">
          <span className="text-[12px] font-semibold w-[60px] shrink-0" style={{ color: "var(--text)" }}>{row.ticker}</span>
          <div className="flex-1 h-[10px] rounded-full overflow-hidden" style={{ background: "var(--panel-2)" }}>
            <div className="h-full rounded-full" style={{ width: `${Math.max(row.weight_pct, 1)}%`, background: "var(--accent)" }} />
          </div>
          <span className="text-[11.5px] w-[70px] text-right shrink-0" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
            {row.weight_pct.toFixed(1)}% · {formatDollars(row.market_value)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Closed positions
// ---------------------------------------------------------------------------

function ClosedPositionRow({ position }: { position: ClosedHoldingPosition }) {
  return (
    <div className="grid items-center gap-2 px-2 py-2 rounded-[8px]" style={{ gridTemplateColumns: "1fr 70px 90px 90px 90px 90px 110px 90px", background: "var(--panel-2)" }}>
      <span className="text-[12.5px] font-semibold" style={{ color: "var(--text)" }}>{position.ticker}</span>
      <span className="text-[11.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{position.shares}</span>
      <span className="text-[11.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>{position.entry_date}</span>
      <span className="text-[11.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>{position.exit_date}</span>
      <span className="text-[11.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{formatDollars(position.entry_price)}</span>
      <span className="text-[11.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{formatDollars(position.exit_price)}</span>
      <span className="text-[12px] font-semibold" style={{ fontFamily: "var(--font-mono)", color: pnlColor(position.realized_pnl_dollars) }}>
        {formatSignedDollars(position.realized_pnl_dollars)}
      </span>
      <span className="text-[12px] font-semibold" style={{ fontFamily: "var(--font-mono)", color: pnlColor(position.realized_pnl_pct) }}>
        {formatPct(position.realized_pnl_pct)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Benchmark-since-purchase chart — 3 series, direct end labels (no legend needed)
// ---------------------------------------------------------------------------

const CHART_W = 640;
const CHART_H = 180;

type BenchSeries = { label: string; color: string; points: { t: number; pct: number }[] };

function normalizeSeries(bars: { time: string; close: number }[]): { t: number; pct: number }[] {
  if (!bars.length) return [];
  const base = bars[0].close;
  if (!base) return [];
  return bars.map((b) => ({ t: new Date(b.time).getTime(), pct: (b.close / base - 1) * 100 }));
}

function BenchmarkChart({ series }: { series: BenchSeries[] }) {
  const withPoints = series.filter((s) => s.points.length > 1);
  if (!withPoints.length) return <EmptyRow>Not enough bar history to draw a comparison yet.</EmptyRow>;

  const allT = withPoints.flatMap((s) => s.points.map((p) => p.t));
  const allPct = withPoints.flatMap((s) => s.points.map((p) => p.pct));
  const minT = Math.min(...allT), maxT = Math.max(...allT);
  const minPct = Math.min(0, ...allPct), maxPct = Math.max(0, ...allPct);
  const padPct = (maxPct - minPct) * 0.1 || 1;

  const x = (t: number) => ((t - minT) / (maxT - minT || 1)) * (CHART_W - 60) + 10;
  const y = (pct: number) =>
    CHART_H - 20 - ((pct - (minPct - padPct)) / ((maxPct + padPct) - (minPct - padPct) || 1)) * (CHART_H - 40);

  const zeroY = y(0);

  return (
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%" height={CHART_H} role="img" aria-label="Performance since purchase, position vs. benchmarks">
      <line x1={10} x2={CHART_W - 50} y1={zeroY} y2={zeroY} stroke="var(--line)" strokeWidth={1} strokeDasharray="3,3" />
      {withPoints.map((s) => {
        const d = s.points
          .map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.t).toFixed(1)} ${y(p.pct).toFixed(1)}`)
          .join(" ");
        const last = s.points[s.points.length - 1];
        return (
          <g key={s.label}>
            <path d={d} fill="none" stroke={s.color} strokeWidth={2} />
            <circle cx={x(last.t)} cy={y(last.pct)} r={3} fill={s.color} />
            <text x={x(last.t) + 6} y={y(last.pct) + 3} fontSize={11} fontFamily="var(--font-mono)" fill={s.color}>
              {s.label} {last.pct >= 0 ? "+" : ""}{last.pct.toFixed(1)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function Holdings() {
  const [status, setStatus] = useState<HoldingsStatus | null>(null);
  const [error, setError] = useState(false);
  const [benchSeries, setBenchSeries] = useState<BenchSeries[] | null>(null);
  const [benchLoading, setBenchLoading] = useState(false);

  function refresh() {
    const controller = new AbortController();
    fetchHoldings({}, controller.signal).then(setStatus).catch(() => setError(true));
    return () => controller.abort();
  }

  useEffect(() => {
    const abort = refresh();
    const interval = setInterval(refresh, POLL_MS);
    return () => {
      abort();
      clearInterval(interval);
    };
  }, []);

  const largestOpen = useMemo(() => {
    const withValue = (status?.open_positions ?? []).filter((p) => p.market_value != null);
    if (!withValue.length) return null;
    return withValue.reduce((a, b) => ((a.market_value ?? 0) > (b.market_value ?? 0) ? a : b));
  }, [status]);

  async function loadBenchmark() {
    if (!largestOpen) return;
    setBenchLoading(true);
    try {
      const [posBars, spyBars, qqqBars] = await Promise.all([
        fetchBars(largestOpen.ticker, "1d", undefined, { start: largestOpen.entry_date }),
        fetchBars("SPY", "1d", undefined, { start: largestOpen.entry_date }),
        fetchBars("QQQ", "1d", undefined, { start: largestOpen.entry_date }),
      ]);
      setBenchSeries([
        { label: largestOpen.ticker, color: "var(--accent)", points: normalizeSeries(posBars.bars) },
        { label: "SPY", color: "var(--gold)", points: normalizeSeries(spyBars.bars) },
        { label: "QQQ", color: "#5a9bd4", points: normalizeSeries(qqqBars.bars) },
      ]);
      const withBenchmark = await fetchHoldings({ benchmark: true });
      setStatus(withBenchmark);
    } finally {
      setBenchLoading(false);
    }
  }

  if (!status && !error) {
    return (
      <section className="flex items-center justify-center py-20" style={{ color: "var(--text-mute)" }}>
        Loading holdings…
      </section>
    );
  }

  const openPositions = status?.open_positions ?? [];
  const closedPositions = status?.closed_positions ?? [];
  const summary = status?.summary;

  return (
    <section className="flex flex-col gap-5" style={{ fontFamily: "var(--font-sans)", color: "var(--text)" }}>
      <div className="flex flex-row items-center justify-between gap-3">
        <h1 className="text-[22px] sm:text-[24px] font-bold leading-tight" style={{ color: "var(--text)" }}>Holdings</h1>
      </div>

      {status?.caveat && (
        <p className="text-[11.5px] leading-relaxed rounded-[8px] px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}>
          {status.caveat}
        </p>
      )}
      {error && (
        <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>Holdings unavailable — the core service may not be running.</p>
      )}

      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <StatTile label="Market value" value={formatDollars(summary.total_market_value_open)} />
          <StatTile label="Cost basis" value={formatDollars(summary.total_cost_basis_open)} />
          <StatTile label="Unrealized P&L" value={formatSignedDollars(summary.total_unrealized_pnl_dollars)} color={pnlColor(summary.total_unrealized_pnl_dollars)} />
          <StatTile label="Today" value={formatSignedDollars(summary.total_day_change_dollars)} color={pnlColor(summary.total_day_change_dollars)} />
          <StatTile label="Realized P&L" value={formatSignedDollars(summary.total_realized_pnl_dollars)} color={pnlColor(summary.total_realized_pnl_dollars)} />
        </div>
      )}

      <Section title="Add a position">
        <AddPositionForm onAdded={refresh} />
      </Section>

      <Section title="Open positions">
        {openPositions.length === 0 ? (
          <EmptyRow>No open positions. Add one above.</EmptyRow>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="grid gap-2 px-2 text-[10px] font-bold uppercase" style={{ gridTemplateColumns: OPEN_GRID, letterSpacing: "0.06em", color: "var(--text-mute)" }}>
              <span>Ticker</span><span>Shares</span><span>Entry</span><span>Date</span><span>Current</span><span>Value</span><span>P&L $</span><span>P&L %</span><span></span>
            </div>
            {openPositions.map((p) => <OpenPositionRow key={p.id} position={p} onChanged={refresh} />)}
          </div>
        )}
      </Section>

      {openPositions.length > 0 && (
        <Section title="Allocation">
          <AllocationBars allocation={status?.allocation ?? []} />
        </Section>
      )}

      {closedPositions.length > 0 && (
        <Section title="Closed positions">
          <div className="flex flex-col gap-2">
            <div className="grid gap-2 px-2 text-[10px] font-bold uppercase" style={{ gridTemplateColumns: "1fr 70px 90px 90px 90px 90px 110px 90px", letterSpacing: "0.06em", color: "var(--text-mute)" }}>
              <span>Ticker</span><span>Shares</span><span>Entry date</span><span>Exit date</span><span>Entry</span><span>Exit</span><span>P&L $</span><span>P&L %</span>
            </div>
            {closedPositions.map((p) => <ClosedPositionRow key={p.id} position={p} />)}
          </div>
        </Section>
      )}

      {openPositions.length > 0 && (
        <Section
          title="Performance since purchase"
          right={
            <button type="button" onClick={loadBenchmark} disabled={benchLoading}
              className="text-[11.5px] font-bold rounded-[8px] px-3 py-[6px] disabled:opacity-60"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
              {benchLoading ? "Comparing…" : "Compare to SPY / QQQ"}
            </button>
          }
        >
          {benchSeries ? (
            <div className="flex flex-col gap-3">
              <BenchmarkChart series={benchSeries} />
              {status?.benchmark && (
                <div className="flex flex-row flex-wrap gap-4 text-[11.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>
                  <span>Actual: {formatDollars(status.benchmark.actual_market_value)}</span>
                  {status.benchmark.comparisons.map((c) => (
                    <span key={c.ticker}>
                      If SPY/QQQ instead ({c.ticker}): {formatDollars(c.hypothetical_value)} ({formatPct(c.hypothetical_pnl_pct)})
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <EmptyRow>Compares your largest open position (and total portfolio dollars) against SPY and QQQ over the same period.</EmptyRow>
          )}
        </Section>
      )}
    </section>
  );
}

export default Holdings;
