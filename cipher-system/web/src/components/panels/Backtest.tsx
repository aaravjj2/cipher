"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchBacktestJob,
  startBacktest,
  type BacktestJob,
  type BacktestPartition,
  type BacktestStats,
  type BacktestEvaluation,
} from "@/lib/api";

/**
 * Backtest panel — runs core/backtest_engine.py from the browser.
 *
 * The engine produced every result in docs/backtest-findings.md (three strategies
 * rejected, one partition flagged promising) and none of it was reachable from the
 * product: it ran only from the command line, so the tool's most valuable
 * capability lived in terminal scrollback.
 *
 * Two modes, and the distinction is the point:
 *
 *   Standalone — can the signal, alone, beat random entry on timing, direction
 *                and selection at once? The hardest question available. Three
 *                strategies have failed it.
 *   Filter     — given trades you were taking anyway, does the signal's state at
 *                entry separate the good ones from the bad? Weaker and likelier.
 *                A standalone failure cannot distinguish "no information" from
 *                "information that is not an entry trigger"; this can.
 *
 * Every partition is scored against its OWN matched random control, because
 * partitioning is a multiple-comparison machine — split a trade set enough ways
 * and one slice always looks good. The UI leads with the control verdict rather
 * than the return, so a flattering number cannot be read without its refutation.
 */

const POLL_MS = 2000;

const PRESETS = [
  { label: "Mega-cap 10", value: "NVDA,AAPL,SPY,QQQ,TSLA,AMD,META,MSFT,AMZN,GOOGL" },
  { label: "NVDA + AAPL", value: "NVDA,AAPL" },
  { label: "Index only", value: "SPY,QQQ,IWM" },
];

function pct(value: number | null | undefined, digits = 2): string {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

function StatGrid({ stats, label }: { stats: BacktestStats; label: string }) {
  const cells: [string, string][] = [
    ["Trades", stats.trades.toLocaleString()],
    ["Win rate", pct(stats.win_rate, 1)],
    ["Avg / trade", pct(stats.avg_return_pct, 4)],
    ["Median", pct(stats.median_return_pct, 4)],
    ["Profit factor", stats.profit_factor == null ? "—" : stats.profit_factor.toFixed(3)],
    ["Max drawdown", pct(stats.max_drawdown_pct, 1)],
  ];
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[11px] font-bold uppercase" style={{ letterSpacing: "0.1em", color: "var(--text-mute)" }}>
        {label}
      </span>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
        {cells.map(([k, v]) => (
          <div key={k} className="flex flex-row items-baseline justify-between gap-2 text-[12px]">
            <span style={{ color: "var(--text-mute)" }}>{k}</span>
            <strong style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>{v}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function PartitionRow({ name, part }: { name: string; part: BacktestPartition }) {
  const s = part.stats;
  const lift = part.lift_vs_base_pp;
  const beats = part.beats_control_range;
  // Verdict, not return, is the headline. A partition can be profitable and still
  // be indistinguishable from random entry.
  const verdict = part.note
    ? part.note
    : beats
      ? "beats its own random control"
      : "within noise of random entry";
  const tone = part.note ? "var(--text-mute)" : beats ? "var(--success)" : "var(--text-mute)";
  return (
    <tr style={{ color: "var(--text-dim)" }}>
      <td className="px-3 py-1.5 font-semibold uppercase" style={{ color: "var(--text)" }}>{name}</td>
      <td className="px-3 py-1.5 text-right tabular-nums">{s.trades.toLocaleString()}</td>
      <td className="px-3 py-1.5 text-right tabular-nums">{part.share_of_base}%</td>
      <td className="px-3 py-1.5 text-right tabular-nums">{pct(s.win_rate, 1)}</td>
      <td className="px-3 py-1.5 text-right tabular-nums">{pct(s.avg_return_pct, 4)}</td>
      <td className="px-3 py-1.5 text-right tabular-nums"
          style={{ color: lift == null ? "var(--text-mute)" : lift > 0 ? "var(--success)" : "var(--neg)" }}>
        {lift == null ? "—" : `${lift > 0 ? "+" : ""}${lift.toFixed(4)}pp`}
      </td>
      <td className="px-3 py-1.5 text-[11px]" style={{ color: tone }}>{verdict}</td>
    </tr>
  );
}

function EvaluationCard({ label, value }: { label: string; value?: BacktestEvaluation }) {
  const stats = value?.stats || value?.base;
  if (!value || !stats) {
    return <div className="rounded-[10px] p-3 text-[11px]" style={{ border: "1px solid var(--line)", color: "var(--text-mute)" }}>{label}: no eligible result</div>;
  }
  const interval = value.uncertainty?.interval;
  const serialInterval = value.serial_uncertainty?.interval;
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-[10px] p-3" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-mute)", letterSpacing: "0.1em" }}>{label}</span>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]" style={{ color: "var(--text-dim)" }}>
        <span><strong style={{ color: "var(--text)" }}>{stats.trades}</strong> trades</span>
        <span><strong style={{ color: stats.avg_return_pct > 0 ? "var(--success)" : "var(--neg)" }}>{pct(stats.avg_return_pct, 4)}</strong> avg</span>
        <span>{pct(stats.win_rate, 1)} win</span>
      </div>
      {interval && <span className="text-[10.5px]" style={{ color: value.uncertainty?.contains_zero ? "var(--warn)" : "var(--text-mute)" }}>
        IID 95% mean: {pct(interval[0], 4)} to {pct(interval[1], 4)}{value.uncertainty?.contains_zero ? " · includes zero" : ""}
      </span>}
      {serialInterval && <span className="text-[10.5px]" style={{ color: value.serial_uncertainty?.contains_zero ? "var(--warn)" : "var(--text-mute)" }}>
        Block 95% mean ({value.serial_uncertainty?.block_length} trades): {pct(serialInterval[0], 4)} to {pct(serialInterval[1], 4)}{value.serial_uncertainty?.contains_zero ? " · includes zero" : ""}
      </span>}
    </div>
  );
}

export function Backtest({ ticker }: { ticker?: string }) {
  const [mode, setMode] = useState<"filter" | "standalone">("filter");
  const [symbols, setSymbols] = useState(PRESETS[0].value);
  const [timeframe, setTimeframe] = useState("15Min");
  const [detector, setDetector] = useState("EOD Focus");
  const [lookback, setLookback] = useState(6);
  const [years, setYears] = useState(1);
  const [costBps, setCostBps] = useState(2);
  const [commissionBps, setCommissionBps] = useState(0);
  const [holdout, setHoldout] = useState(0.3);
  const [job, setJob] = useState<BacktestJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const run = useCallback(async () => {
    setError(null);
    stopPolling();
    try {
      const { job_id } = await startBacktest({ mode, symbols, timeframe, detector, lookback, years, costBps, commissionBps, holdout, seed: 17 });
      pollRef.current = setInterval(async () => {
        try {
          const next = await fetchBacktestJob(job_id);
          setJob(next);
          if (next.status === "done" || next.status === "error") stopPolling();
        } catch {
          stopPolling();
        }
      }, POLL_MS);
      setJob(await fetchBacktestJob(job_id));
    } catch (err) {
      setError((err as Error)?.message || "could not start the backtest");
    }
  }, [mode, symbols, timeframe, detector, lookback, years, costBps, commissionBps, holdout, stopPolling]);

  const busy = job?.status === "queued" || job?.status === "running";
  const result = job?.status === "done" ? job.result : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-[15px] font-bold" style={{ color: "var(--text)" }}>Signal backtest</h2>
        <p className="text-[12.5px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
          Simulated fills over historical bars — next-bar-open entries, stop assumed
          first when a bar spans both levels, costs charged on both sides. Every
          result is measured against a random-entry control matched trade-for-trade
          by symbol and direction. Research only; places no orders.
        </p>
      </div>

      {/* Controls */}
      <div className="flex flex-row flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Mode</span>
          <div className="flex flex-row items-center gap-[2px] rounded-[8px] p-[2px]"
               style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
            {(["filter", "standalone"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                aria-pressed={mode === m}
                title={m === "filter"
                  ? "Does the signal separate trades you were taking anyway?"
                  : "Can the signal beat random entry on its own?"}
                className="rounded-[6px] px-3 py-[5px] text-[12px] font-semibold capitalize"
                style={{
                  background: mode === m ? "var(--nav-active)" : "transparent",
                  color: mode === m ? "var(--text)" : "var(--text-dim)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1 min-w-[240px] flex-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Symbols</span>
          <input
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            className="rounded-[8px] px-[10px] py-[7px] text-[12px] outline-none w-full"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
          />
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Presets</span>
          <div className="flex flex-row gap-1">
            {PRESETS.map((p) => (
              <button key={p.label} type="button" onClick={() => setSymbols(p.value)}
                className="rounded-[8px] px-[10px] py-[7px] text-[11px] font-semibold"
                style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
                {p.label}
              </button>
            ))}
            {ticker && (
              <button type="button" onClick={() => setSymbols(ticker)}
                className="rounded-[8px] px-[10px] py-[7px] text-[11px] font-semibold"
                style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
                {ticker} only
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Detector</span>
          <select value={detector} onChange={(e) => setDetector(e.target.value)}
            className="rounded-[8px] px-[10px] py-[7px] text-[12px] outline-none"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}>
            <option>EOD Focus</option>
            <option>Full Session</option>
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>TF</span>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}
            className="rounded-[8px] px-[10px] py-[7px] text-[12px] outline-none"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}>
            <option>15Min</option>
            <option>5Min</option>
            <option>1Day</option>
          </select>
        </div>

        {mode === "filter" && (
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Lookback</span>
            <input type="number" min={1} max={40} value={lookback}
              onChange={(e) => setLookback(Number(e.target.value) || 6)}
              className="w-[80px] rounded-[8px] px-[10px] py-[7px] text-[12px] outline-none"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }} />
          </div>
        )}

        <details className="w-full rounded-[9px] px-3 py-2" style={{ border: "1px solid var(--line)", background: "var(--panel-1)" }}>
          <summary className="cursor-pointer text-[11px] font-semibold" style={{ color: "var(--text-dim)" }}>Experiment protocol</summary>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-[10px] uppercase" style={{ color: "var(--text-mute)" }}>History years
              <input type="number" min={0.25} max={10} step={0.25} value={years} onChange={(e) => setYears(Number(e.target.value) || 1)} className="w-[88px] rounded-[7px] px-2 py-1.5 text-[12px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
            </label>
            <label className="flex flex-col gap-1 text-[10px] uppercase" style={{ color: "var(--text-mute)" }}>Cost / side (bps)
              <input type="number" min={0} max={100} step={0.5} value={costBps} onChange={(e) => setCostBps(Math.max(0, Number(e.target.value)))} className="w-[100px] rounded-[7px] px-2 py-1.5 text-[12px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
            </label>
            <label className="flex flex-col gap-1 text-[10px] uppercase" style={{ color: "var(--text-mute)" }}>Commission / side
              <input type="number" min={0} max={100} step={0.1} value={commissionBps} onChange={(e) => setCommissionBps(Math.max(0, Number(e.target.value)))} className="w-[100px] rounded-[7px] px-2 py-1.5 text-[12px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
            </label>
            <label className="flex flex-col gap-1 text-[10px] uppercase" style={{ color: "var(--text-mute)" }}>Locked holdout
              <select value={holdout} onChange={(e) => setHoldout(Number(e.target.value))} className="rounded-[7px] px-2 py-1.5 text-[12px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}>
                <option value={0.2}>20%</option><option value={0.3}>30%</option><option value={0.4}>40%</option>
              </select>
            </label>
            <span className="max-w-[520px] text-[10.5px] leading-relaxed" style={{ color: "var(--text-mute)" }}>
              Parameters are hashed before the run. One bar on each side of the split is embargoed. Costs apply on entry and exit; seed 17 makes controls and uncertainty reproducible.
            </span>
          </div>
        </details>

        <button type="button" onClick={run} disabled={busy}
          className="rounded-[8px] px-[18px] py-[8px] text-[13px] font-semibold"
          style={{
            background: busy ? "var(--panel-2)" : "transparent",
            border: `1px solid ${busy ? "var(--line)" : "var(--accent)"}`,
            color: busy ? "var(--text-mute)" : "var(--accent)",
            cursor: busy ? "not-allowed" : "pointer",
          }}>
          {busy ? "Running…" : "Run backtest"}
        </button>
      </div>

      {error && <div className="text-[12px]" style={{ color: "var(--neg)" }}>{error}</div>}

      {job && job.status !== "done" && (
        <div className="flex flex-col gap-1.5">
          <div className="h-[8px] w-full overflow-hidden rounded-full" style={{ background: "var(--panel-2)" }}>
            <div className="h-full rounded-full transition-[width] duration-300"
              style={{ width: `${job.pct}%`, background: job.status === "error" ? "var(--neg)" : "var(--accent)" }} />
          </div>
          <span className="text-[11px]" style={{ color: job.status === "error" ? "var(--neg)" : "var(--text-mute)" }}>
            {job.error || job.message}
          </span>
        </div>
      )}

      {result && (
        <div className="flex flex-col gap-4">
          <span className="text-[11px]" style={{ color: "var(--text-mute)" }}>
            {result.symbols.length} symbols · {result.timeframe} · {result.detector_mode}
            {result.lookback_bars ? ` · ${result.lookback_bars}-bar lookback` : ""} ·
            {" "}{(result.elapsed_ms / 1000).toFixed(1)}s
          </span>

          {result.manifest && (
            <div className="flex flex-col gap-2 rounded-[10px] p-3" style={{ border: "1px solid var(--line)", background: "var(--panel-1)" }}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11px] font-bold uppercase" style={{ color: "var(--text)" }}>Locked experiment</span>
                <code className="text-[10.5px]" title={result.experiment_id} style={{ color: "var(--accent)" }}>{result.experiment_id?.slice(0, 16)}</code>
              </div>
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px]" style={{ color: "var(--text-dim)" }}>
                <span>{result.manifest.spec.cost_model.slippage_bps_per_side} slippage + {result.manifest.spec.cost_model.commission_bps_per_side} commission bps/side · {result.manifest.spec.cost_model.round_trip_bps} round trip</span>
                <span>{Math.round(result.manifest.spec.validation.holdout_fraction * 100)}% chronological holdout</span>
                <span>next-open · stop-first · research only</span>
                <span title={result.manifest.data_fingerprint}>data {result.manifest.data_fingerprint.slice(0, 10)} · exact input snapshot saved</span>
              </div>
              {result.manifest.blocked_symbols.length > 0 && <span className="text-[10.5px]" style={{ color: "var(--warn)" }}>
                Holdout blocked for: {result.manifest.blocked_symbols.join(", ")} — insufficient bars in one partition.
              </span>}
            </div>
          )}

          {result.validation && (
            <div className="grid gap-2 sm:grid-cols-2">
              <EvaluationCard label="Training partition" value={result.validation.train} />
              <EvaluationCard label="Locked holdout — primary evidence" value={result.validation.holdout} />
              {result.validation.blocker && <p className="text-[11px] sm:col-span-2" style={{ color: "var(--warn)" }}>{result.validation.blocker}</p>}
            </div>
          )}

          {result.portfolio && (
            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 rounded-[10px] p-3 text-[11px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
              <strong style={{ color: "var(--text)" }}>Portfolio constraint</strong>
              <span>${result.portfolio.starting_equity.toLocaleString()} → ${result.portfolio.ending_equity.toLocaleString()}</span>
              <span style={{ color: result.portfolio.profit_loss >= 0 ? "var(--success)" : "var(--neg)" }}>{result.portfolio.profit_loss >= 0 ? "+" : ""}${result.portfolio.profit_loss.toLocaleString()} · {pct(result.portfolio.return_pct, 3)}</span>
              <span>{result.portfolio.trades_taken} taken · {result.portfolio.trades_skipped_at_capacity} capacity skips · max {result.portfolio.max_concurrent_positions}</span>
              {result.trade_ledger && <button type="button" className="ml-auto font-semibold" style={{ color: "var(--accent)" }} onClick={() => {
                const blob = new Blob([JSON.stringify(result.trade_ledger, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a"); link.href = url; link.download = `cipher-backtest-${result.experiment_id?.slice(0, 12) || "ledger"}.json`; link.click(); URL.revokeObjectURL(url);
              }}>Download trade ledger</button>}
            </div>
          )}

          {result.mode === "filter" && result.base && result.partitions && (
            <>
              <StatGrid stats={result.base} label="Base strategy — fixed-cadence entries, no view on price" />
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left text-[12px]">
                  <thead>
                    <tr style={{ color: "var(--text-mute)" }}>
                      {["Partition", "Trades", "Share", "Win", "Avg", "Lift", "Verdict"].map((h, i) => (
                        <th key={h} className={`whitespace-nowrap border-b px-3 py-1.5 text-[10px] font-semibold ${i > 0 && i < 6 ? "text-right" : ""}`}
                          style={{ borderColor: "var(--line)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(result.partitions).map(([name, part]) => (
                      <PartitionRow key={name} name={name} part={part} />
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[11px] leading-snug" style={{ color: "var(--text-mute)" }}>
                Lift is against the base, not against zero. Partitioning inflates
                multiple-comparison risk, so a partition matters only if it clears its own
                control — and a small partition clearing one is weak evidence. See
                docs/backtest-findings.md for what has and has not held up.
              </p>
            </>
          )}

          {result.mode === "standalone" && result.stats && (
            <>
              <StatGrid stats={result.stats} label="Detector as a standalone entry trigger" />
              {result.control?.control && (
                <div className="flex flex-col gap-1.5">
                  <StatGrid
                    stats={{
                      ...result.stats,
                      trades: result.stats.trades,
                      win_rate: result.control.control.win_rate,
                      avg_return_pct: result.control.control.avg_return_pct,
                      profit_factor: result.control.control.profit_factor,
                    }}
                    label="Matched random control"
                  />
                  <span className="text-[12px] font-semibold"
                    style={{ color: result.control.detector_beats_control_range ? "var(--success)" : "var(--text-mute)" }}>
                    {result.control.detector_beats_control_range
                      ? "Detector clears every random draw."
                      : "Detector does not clear the random-entry range — no entry-timing edge demonstrated."}
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
