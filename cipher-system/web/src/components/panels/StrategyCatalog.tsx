"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchStrategyCatalog,
  fetchStrategyJob,
  startStrategyEvaluation,
  type CatalogStrategy,
  type StrategyCatalog,
  type StrategyJob,
  type StrategyVerdict,
} from "@/lib/api";

/**
 * Every strategy in the repository, and what each one is allowed to claim.
 *
 * This replaces five separate leaderboards. /api/strategy, /api/price-backtest,
 * /api/edge-backtest, /api/intraday-backtest and /api/historical-backtest each
 * ranked strategies with their own scoring half, and none of them was reachable
 * from the browser. Surfacing them as they stood would have shipped 24 numbers
 * that could not be checked: three of the engines charge no transaction cost at
 * all, two strategies truncated to their five earliest signals, and the whole GEX
 * family reads today's open interest while trading past bars.
 *
 * So the panel leads with the verdict, never the return. A profit factor of 11.7
 * on six trades is shown as INSUFFICIENT, and a strategy that cannot be honestly
 * measured is shown as BLOCKED with the reason and its accrual countdown — not
 * ranked below the ones that can.
 */

const POLL_MS = 2000;

const PRESETS = [
  { label: "Mega-cap 10", value: "NVDA,AAPL,SPY,QQQ,TSLA,AMD,META,MSFT,AMZN,GOOGL" },
  { label: "Index only", value: "SPY,QQQ,IWM" },
  { label: "NVDA + AAPL", value: "NVDA,AAPL" },
];

/**
 * Ordered by how much the verdict says, not alphabetically. BLOCKED sits last
 * because it is a statement about the data, not about the strategy.
 */
const VERDICT_ORDER: Record<string, number> = {
  PASS: 0,
  IN_SAMPLE_ONLY: 1,
  FAIL: 2,
  INSUFFICIENT: 3,
  NO_TRADES: 4,
  WRONG_TIMEFRAME: 5,
  ERROR: 6,
  BLOCKED: 7,
};

const VERDICT_TONE: Record<string, { bg: string; fg: string }> = {
  PASS: { bg: "color-mix(in srgb, #16a34a 24%, transparent)", fg: "#4ade80" },
  IN_SAMPLE_ONLY: { bg: "color-mix(in srgb, #ca8a04 24%, transparent)", fg: "#facc15" },
  FAIL: { bg: "color-mix(in srgb, #dc2626 20%, transparent)", fg: "#f87171" },
  INSUFFICIENT: { bg: "var(--panel-2)", fg: "var(--text-dim)" },
  NO_TRADES: { bg: "var(--panel-2)", fg: "var(--text-mute)" },
  WRONG_TIMEFRAME: { bg: "var(--panel-2)", fg: "var(--text-mute)" },
  ERROR: { bg: "color-mix(in srgb, #dc2626 20%, transparent)", fg: "#f87171" },
  BLOCKED: { bg: "color-mix(in srgb, #7c3aed 22%, transparent)", fg: "#c4b5fd" },
};

function num(value: number | null | undefined, digits = 2): string {
  return value == null ? "—" : value.toFixed(digits);
}

function VerdictChip({ verdict }: { verdict: string }) {
  const tone = VERDICT_TONE[verdict] ?? VERDICT_TONE.INSUFFICIENT;
  return (
    <span
      className="inline-block text-[10px] font-semibold px-[7px] py-[2px] rounded-[5px] whitespace-nowrap"
      style={{ background: tone.bg, color: tone.fg, letterSpacing: "0.04em" }}
    >
      {verdict.replace(/_/g, " ")}
    </span>
  );
}

function Toolbar({
  symbols, setSymbols, timeframe, setTimeframe, family, setFamily, running, onRun,
}: {
  symbols: string; setSymbols: (v: string) => void;
  timeframe: string; setTimeframe: (v: string) => void;
  family: string; setFamily: (v: string) => void;
  running: boolean; onRun: () => void;
}) {
  const group = "flex flex-row items-center gap-[6px]";
  const btn = "text-[11px] px-[9px] py-[5px] rounded-[7px] whitespace-nowrap";
  const chip = (active: boolean) => ({
    background: active ? "var(--nav-active)" : "var(--panel-2)",
    color: active ? "var(--text)" : "var(--text-dim)",
    border: "1px solid var(--line)",
  });

  return (
    <div className="flex flex-row flex-wrap items-center gap-[14px] mb-[14px]">
      <div className={group}>
        <span className="text-[10px] uppercase" style={{ color: "var(--text-mute)", letterSpacing: "0.14em" }}>
          Family
        </span>
        {["all", "edge", "price", "intraday", "gex"].map((f) => (
          <button key={f} type="button" className={btn} style={chip(family === f)}
            onClick={() => setFamily(f)}>
            {f}
          </button>
        ))}
      </div>

      <div className={group}>
        <span className="text-[10px] uppercase" style={{ color: "var(--text-mute)", letterSpacing: "0.14em" }}>
          TF
        </span>
        {["1Day", "15Min"].map((t) => (
          <button key={t} type="button" className={btn} style={chip(timeframe === t)}
            onClick={() => setTimeframe(t)}>
            {t}
          </button>
        ))}
      </div>

      <div className={group}>
        {PRESETS.map((p) => (
          <button key={p.label} type="button" className={btn} style={chip(symbols === p.value)}
            onClick={() => setSymbols(p.value)}>
            {p.label}
          </button>
        ))}
      </div>

      <button
        type="button"
        onClick={onRun}
        disabled={running}
        className="text-[11px] font-semibold px-[13px] py-[6px] rounded-[7px]"
        style={{
          background: running ? "var(--panel-2)" : "var(--accent, #7c3aed)",
          color: running ? "var(--text-mute)" : "#fff",
          border: "1px solid var(--line)",
          cursor: running ? "default" : "pointer",
        }}
      >
        {running ? "Evaluating…" : "Evaluate"}
      </button>
    </div>
  );
}

export function StrategyCatalogPanel() {
  const [catalog, setCatalog] = useState<StrategyCatalog | null>(null);
  const [job, setJob] = useState<StrategyJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [symbols, setSymbols] = useState(PRESETS[0].value);
  const [timeframe, setTimeframe] = useState("1Day");
  const [family, setFamily] = useState("all");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchStrategyCatalog(controller.signal)
      .then(setCatalog)
      .catch((e) => setError(String(e?.message ?? e)));
    return () => controller.abort();
  }, []);

  // The poll re-schedules itself, so it has to reach its own current definition
  // rather than the binding captured when the callback was created. A ref keeps
  // that indirection explicit; referencing `poll` inside `poll` would close over
  // a stale copy and silently stop updating.
  const pollRef = useRef<(jobId: string) => void>(() => {});
  const poll = useCallback((jobId: string) => {
    fetchStrategyJob(jobId)
      .then((next) => {
        setJob(next);
        if (next.status === "queued" || next.status === "running") {
          timer.current = setTimeout(() => pollRef.current(jobId), POLL_MS);
        }
      })
      .catch((e) => setError(String(e?.message ?? e)));
  }, []);

  // Assigned in an effect rather than during render: writing a ref while
  // rendering is a side effect in the render phase, which React may run more than
  // once per commit.
  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const run = useCallback(() => {
    setError(null);
    startStrategyEvaluation({
      family: family === "all" ? undefined : family,
      symbols, timeframe, years: timeframe === "1Day" ? 5 : 1,
    })
      .then((r) => poll(r.job_id))
      .catch((e) => setError(String(e?.message ?? e)));
  }, [family, symbols, timeframe, poll]);

  const running = job?.status === "queued" || job?.status === "running";
  const verdicts = job?.result?.results ?? null;

  // Before a run there are no verdicts, only the register of what exists. Showing
  // the blocked reasons at that point is the honest default: it is what the tool
  // knows without measuring anything.
  const rows: Array<CatalogStrategy | StrategyVerdict> = verdicts
    ? [...verdicts].sort((a, b) =>
        (VERDICT_ORDER[a.verdict] ?? 9) - (VERDICT_ORDER[b.verdict] ?? 9) ||
        ((b.metrics?.trades ?? 0) - (a.metrics?.trades ?? 0)))
    : (catalog?.strategies ?? []).filter(
        (s) => family === "all" || s.family === family);

  return (
    <div className="p-[18px]" style={{ color: "var(--text)" }}>
      <h2 className="text-[15px] font-semibold mb-[4px]">Strategy catalog</h2>
      <p className="text-[11.5px] leading-[1.6] mb-[14px]" style={{ color: "var(--text-dim)" }}>
        {catalog?.standard ??
          "A strategy passes only by beating a random-entry control matched trade-for-trade by symbol and direction."}
      </p>

      {catalog && (
        <div className="flex flex-row flex-wrap gap-[16px] mb-[14px] text-[11px]"
             style={{ color: "var(--text-dim)" }}>
          <span><b style={{ color: "var(--text)" }}>{catalog.summary.total}</b> catalogued</span>
          <span><b style={{ color: "var(--text)" }}>{catalog.summary.evaluable}</b> evaluable</span>
          <span><b style={{ color: "#c4b5fd" }}>{catalog.summary.blocked}</b> blocked — not scored</span>
          {job?.result?.cost_source && (
            <span>cost: <b style={{ color: "var(--text)" }}>{job.result.cost_source}</b></span>
          )}
        </div>
      )}

      <Toolbar
        symbols={symbols} setSymbols={setSymbols}
        timeframe={timeframe} setTimeframe={setTimeframe}
        family={family} setFamily={setFamily}
        running={!!running} onRun={run}
      />

      {running && (
        <div className="text-[11px] mb-[10px]" style={{ color: "var(--text-dim)" }}>
          {job?.pct}% — {job?.message}
        </div>
      )}
      {error && (
        <div className="text-[11px] mb-[10px]" style={{ color: "#f87171" }}>{error}</div>
      )}
      {job?.status === "error" && (
        <div className="text-[11px] mb-[10px]" style={{ color: "#f87171" }}>{job.error}</div>
      )}

      {job?.result && (
        <div className="flex flex-row flex-wrap gap-[10px] mb-[12px] text-[11px]">
          {Object.entries(job.result.verdicts).map(([v, n]) => (
            <span key={v} className="flex flex-row items-center gap-[5px]">
              <VerdictChip verdict={v} />
              <b>{n}</b>
            </span>
          ))}
        </div>
      )}

      {/* Keep the seven-column verdict register readable on narrow screens. The
          wrapper owns horizontal scrolling; the table retains enough width for
          Strategy/Why text and numeric columns instead of collapsing into a
          page-level overflow trap. */}
      <div className="overflow-x-auto rounded-[8px]" style={{ border: "1px solid var(--line)" }}>
        <table aria-label="Strategy catalog verdicts" className="w-full min-w-[760px] text-[11.5px]" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "var(--text-mute)" }}>
              <th scope="col" className="text-left font-medium py-[6px] pr-[10px]">Strategy</th>
              <th scope="col" className="text-left font-medium py-[6px] pr-[10px]">Family</th>
              <th scope="col" className="text-left font-medium py-[6px] pr-[10px]">Verdict</th>
              <th scope="col" className="text-right font-medium py-[6px] pr-[10px]">Trades</th>
              <th scope="col" className="text-right font-medium py-[6px] pr-[10px]">Avg</th>
              <th scope="col" className="text-right font-medium py-[6px] pr-[10px]">PF</th>
              <th scope="col" className="text-left font-medium py-[6px]">Why</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const v = "verdict" in row ? (row as StrategyVerdict) : null;
              const c = v ? null : (row as CatalogStrategy);
              const verdict = v?.verdict ?? (c?.evaluable ? "—" : "BLOCKED");
              const why = v?.reason ?? c?.blocked_reason ?? c?.data_requirement ?? "";
              const accrual = v?.accrual;
              return (
                <tr key={row.strategy_id} style={{ borderTop: "1px solid var(--line)" }}>
                  <th scope="row" className="py-[7px] pr-[10px] text-left font-mono text-[11px]">{row.strategy_id}</th>
                  <td className="py-[7px] pr-[10px]" style={{ color: "var(--text-dim)" }}>
                    {row.family}
                  </td>
                  <td className="py-[7px] pr-[10px]">
                    {verdict === "—"
                      ? <span style={{ color: "var(--text-mute)" }}>—</span>
                      : <VerdictChip verdict={verdict} />}
                  </td>
                  <td className="py-[7px] pr-[10px] text-right tabular-nums">
                    {v?.metrics?.trades ?? "—"}
                  </td>
                  <td className="py-[7px] pr-[10px] text-right tabular-nums">
                    {v?.metrics ? `${num(v.metrics.avg_return_pct, 4)}%` : "—"}
                  </td>
                  <td className="py-[7px] pr-[10px] text-right tabular-nums">
                    {v?.metrics ? num(v.metrics.profit_factor, 3) : "—"}
                  </td>
                  <td className="py-[7px] leading-[1.5]" style={{ color: "var(--text-dim)" }}>
                    {why}
                    {accrual && (
                      <span style={{ color: "#c4b5fd" }}> · {accrual}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {!verdicts && catalog && (
        <p className="text-[11px] mt-[12px]" style={{ color: "var(--text-mute)" }}>
          No evaluation has been run in this session. The table above is the register of
          what exists and what can be measured — press Evaluate to produce verdicts.
        </p>
      )}
    </div>
  );
}

export default StrategyCatalogPanel;
