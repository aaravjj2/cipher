"use client";

import { useEffect, useState } from "react";
import { fetchAutopilotStatus, type AutopilotStatus } from "@/lib/api";
import { LoadingStatus } from "@/components/ui/skeleton";

function tone(state: string) {
  if (state === "DATA_FAILURE") return "var(--negative)";
  if (state === "ACTIVE_POSITION" || state === "HEALTHY_NO_SETUP") return "var(--positive)";
  return state === "SETUP_REJECTED" ? "var(--gold)" : "var(--accent)";
}

function human(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ") : "Unavailable";
}

export function Autopilot() {
  const [data, setData] = useState<AutopilotStatus | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const load = () => fetchAutopilotStatus(controller.signal).then((next) => { setData(next); setError(""); }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Autopilot status unavailable");
    });
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, []);

  if (error) return <div className="border p-5 text-sm" style={{ borderColor: "var(--negative)", color: "var(--negative)" }}>{error}</div>;
  if (!data) return <LoadingStatus style={{ color: "var(--text-mute)" }}>Loading the paper-agent trace…</LoadingStatus>;

  const stateTone = tone(data.executor.operating_state);
  const zeroTradeHealthy = data.executor.operating_state === "HEALTHY_NO_SETUP" && (data.executor.session?.positions_opened ?? 0) === 0;
  const marketDataLabel = data.phase === "closed"
    ? "Off hours"
    : data.executor.market_data_ready ? "Ready" : "Awaiting fresh data";
  const showCurrentBlock = data.executor.operating_state === "DATA_FAILURE" || data.executor.operating_state === "SETUP_REJECTED";
  const steps = [
    ["Premarket research", data.daily_trace.premarket_plan_observed ? "complete" : "waiting"],
    ["Candidate plan", data.plan.available ? `${data.plan.candidate_count} candidates · ${human(data.plan.freshness)}` : human(data.plan.state)],
    ["Closed-bar confirmation", data.daily_trace.confirmation_cycle_observed ? "observed" : "waiting"],
    ["Risk and contract gate", `${data.executor.session?.contracts_evaluated ?? 0} evaluated · ${data.executor.session?.cards_admitted ?? 0} admitted`],
    ["Paper execution", zeroTradeHealthy ? "0 positions · healthy no-trade" : `${data.executor.session?.positions_opened ?? 0} opened · ${data.executor.session?.positions_closed ?? 0} closed`],
    ["Reconciliation", data.executor.reconciliation_passed ? "passed" : "blocked"],
  ];

  return <section data-testid="autopilot-panel" className="mx-auto flex w-full max-w-7xl flex-col gap-4" style={{ fontFamily: "var(--font-mono)" }}>
    <header className="border p-5" style={{ borderColor: stateTone, background: "var(--panel)" }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-[10px] font-bold uppercase tracking-[.14em]" style={{ color: "var(--gold)" }}>AUTONOMOUS · PAPER ONLY · AUDITABLE</p><h1 className="mt-2 text-2xl font-semibold">Autopilot</h1><p className="mt-2 max-w-3xl text-xs leading-5" style={{ color: "var(--text-dim)" }}>Research agents assemble evidence; deterministic policy owns confirmation, contract liquidity, risk, and execution eligibility. Models cannot authorize an order. Zero paper trades is a healthy outcome when no setup qualifies.</p></div>
        <span className="rounded-full border px-3 py-1.5 text-[10px] font-bold uppercase" style={{ borderColor: stateTone, color: stateTone }}>{human(data.phase)} · {human(data.executor.operating_state)}</span>
      </div>
    </header>
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{steps.map(([label, value], index) => <article key={label} className="border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><p className="text-[9px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>{String(index + 1).padStart(2, "0")} · {label}</p><p className="mt-2 text-sm font-semibold">{value}</p></article>)}</div>
    {!!data.executor.cohorts?.length && <section className="overflow-x-auto border p-5" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
      <h2 className="text-sm font-semibold">Parallel paper comparisons</h2>
      <p className="mt-2 text-xs" style={{ color: "var(--text-mute)" }}>Up to five qualified entries per portfolio per session. Results below cover the current experiment version. Earlier trades remain in portfolio history.</p>
      <table className="mt-4 w-full text-left text-xs"><caption className="sr-only">Autopilot paper portfolio performance and promotion requirements</caption>
        <thead><tr>{["Portfolio", "Trades", "Win rate", "Net P&L", "Mean trade", "Evidence", "Rejections", "Promotion"].map(label => <th key={label} scope="col" className="p-2">{label}</th>)}</tr></thead>
        <tbody>{data.executor.cohorts.map(cohort => {
          const metrics = cohort.evaluation?.current ?? cohort.evaluation?.overall;
          const blockers = cohort.evaluation?.promotion_blockers || [];
          return <tr key={cohort.cohort_id} className="border-t" style={{ borderColor: "var(--line)" }}>
            <th scope="row" className="p-2 font-medium">{human(cohort.cohort_id)} · {cohort.version}{cohort.primary ? " · Primary" : ""}</th>
            <td className="p-2">{metrics?.trades ?? "—"}</td>
            <td className="p-2">{metrics?.win_rate_pct == null ? "—" : `${metrics.win_rate_pct.toFixed(1)}%`}</td>
            <td className="p-2">{metrics?.pnl_usd == null ? "—" : `$${metrics.pnl_usd.toFixed(2)}`}</td>
            <td className="p-2">{metrics?.expectancy_usd == null ? "—" : `$${metrics.expectancy_usd.toFixed(2)}`}</td>
            <td className="p-2">{cohort.evaluation?.activity?.observed_sessions ?? "—"} observed sessions; {cohort.evaluation?.evidence_coverage?.positions_with_quote_tape ?? "—"}/{cohort.evaluation?.evidence_coverage?.positions ?? "—"} positions with quote tape</td>
            <td className="max-w-xs p-2">{Object.entries(cohort.evaluation?.activity?.rejection_reasons ?? {}).map(([reason, count]) => `${human(reason)}: ${count}`).join("; ") || "No recorded rejections"}</td>
            <td className="max-w-xs p-2">{cohort.error ? human(cohort.error) : blockers.length ? human(blockers.join(" · ")) : "Awaiting evaluation"}</td>
          </tr>;
        })}</tbody>
      </table>
    </section>}
    <div className="grid gap-4 xl:grid-cols-[1.2fr_.8fr]">
      <section className="border p-5" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><h2 className="text-sm font-semibold">Latest decision evidence</h2><div className="mt-4 space-y-2">{data.plan.candidates.length ? data.plan.candidates.slice(0, 5).map((candidate) => <div key={`${candidate.ticker}-${candidate.direction}`} className="border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="flex justify-between gap-3 text-xs"><strong>{candidate.ticker || "Unknown"} · {candidate.direction || "Unknown"}</strong><span>{candidate.score === null ? "Unavailable" : candidate.score.toFixed(1)}</span></div><p className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>{candidate.ai_evaluation?.thesis || "Awaiting bounded research synthesis"}</p></div>) : <p className="text-xs" style={{ color: "var(--text-mute)" }}>No candidate was forced. A healthy no-setup session is a valid outcome.</p>}</div></section>
      <section className="border p-5" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><h2 className="text-sm font-semibold">Execution boundary</h2><dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><div><dt style={{ color: "var(--text-mute)" }}>Market data</dt><dd className="mt-1 font-semibold">{marketDataLabel}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Ledger reconciliation</dt><dd className="mt-1 font-semibold">{data.executor.reconciliation_passed ? "Passed" : "Blocked"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Execution</dt><dd className="mt-1 font-semibold">{data.executor.execution_backend === "simulated" ? "Local paper portfolio" : "External paper disabled"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Portfolio ledger</dt><dd className="mt-1 font-semibold">{data.executor.paper_broker?.ready ? "Ready" : "Fail-closed"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Model authority</dt><dd className="mt-1 font-semibold">None</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Live execution</dt><dd className="mt-1 font-semibold">Impossible · broker orders disabled</dd></div></dl>{showCurrentBlock && data.executor.last_entry_block && <p className="mt-4 border px-3 py-2 text-[10px]" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>Latest block: {data.executor.last_entry_block.ticker ?? "unknown"} · {human(data.executor.last_entry_block.reason)}</p>}</section>
    </div>
  </section>;
}
