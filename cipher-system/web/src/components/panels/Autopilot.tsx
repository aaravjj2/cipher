"use client";

import { useEffect, useState } from "react";
import { fetchAutopilotStatus, type AutopilotStatus } from "@/lib/api";

function tone(state: string) {
  if (state === "DATA_FAILURE") return "var(--negative)";
  if (state === "ACTIVE_POSITION") return "var(--positive)";
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

  if (error) return <div className="rounded-xl border p-5 text-sm" style={{ borderColor: "var(--negative)", color: "var(--negative)" }}>{error}</div>;
  if (!data) return <div style={{ color: "var(--text-mute)" }}>Loading the paper-agent trace…</div>;

  const stateTone = tone(data.executor.operating_state);
  const steps = [
    ["Premarket research", data.daily_trace.premarket_plan_observed ? "complete" : "waiting"],
    ["Candidate plan", data.plan.available ? `${data.plan.candidate_count} candidates` : human(data.plan.state)],
    ["Closed-bar confirmation", data.daily_trace.confirmation_cycle_observed ? "observed" : "waiting"],
    ["Risk and contract gate", `${data.executor.counts.contract_candidates ?? 0} candidates retained`],
    ["Paper execution", `${data.daily_trace.paper_submissions} submissions`],
    ["Reconciliation", data.executor.reconciliation_passed ? "passed" : "blocked"],
  ];

  return <section data-testid="autopilot-panel" className="mx-auto flex w-full max-w-7xl flex-col gap-4" style={{ fontFamily: "var(--font-mono)" }}>
    <header className="rounded-xl border p-5" style={{ borderColor: stateTone, background: "var(--panel)" }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-[10px] font-bold uppercase tracking-[.14em]" style={{ color: "var(--gold)" }}>AUTONOMOUS · PAPER ONLY · AUDITABLE</p><h1 className="mt-2 text-2xl font-semibold">Autopilot</h1><p className="mt-2 max-w-3xl text-xs leading-5" style={{ color: "var(--text-dim)" }}>Research agents assemble evidence; deterministic policy owns confirmation, contract liquidity, risk, and execution eligibility. Models cannot authorize an order.</p></div>
        <span className="rounded-full border px-3 py-1.5 text-[10px] font-bold uppercase" style={{ borderColor: stateTone, color: stateTone }}>{human(data.phase)} · {human(data.executor.operating_state)}</span>
      </div>
    </header>
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{steps.map(([label, value], index) => <article key={label} className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><p className="text-[9px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>{String(index + 1).padStart(2, "0")} · {label}</p><p className="mt-2 text-sm font-semibold">{value}</p></article>)}</div>
    <div className="grid gap-4 xl:grid-cols-[1.2fr_.8fr]">
      <section className="rounded-xl border p-5" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><h2 className="text-sm font-semibold">Latest decision evidence</h2><div className="mt-4 space-y-2">{data.plan.candidates.length ? data.plan.candidates.slice(0, 5).map((candidate) => <div key={`${candidate.ticker}-${candidate.direction}`} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="flex justify-between gap-3 text-xs"><strong>{candidate.ticker} · {candidate.direction}</strong><span>{candidate.score.toFixed(1)}</span></div><p className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>{candidate.ai_evaluation?.thesis || "Awaiting bounded research synthesis"}</p></div>) : <p className="text-xs" style={{ color: "var(--text-mute)" }}>No candidate was forced. A healthy no-setup session is a valid outcome.</p>}</div></section>
      <section className="rounded-xl border p-5" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><h2 className="text-sm font-semibold">Execution boundary</h2><dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><div><dt style={{ color: "var(--text-mute)" }}>Market data</dt><dd className="mt-1 font-semibold">{data.executor.market_data_ready ? "Ready" : "Blocked"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Reconciliation</dt><dd className="mt-1 font-semibold">{data.executor.reconciliation_passed ? "Passed" : "Blocked"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Execution</dt><dd className="mt-1 font-semibold">{data.executor.execution_backend === "alpaca_paper" ? "Alpaca Paper" : "Local simulation"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Paper account</dt><dd className="mt-1 font-semibold">{data.executor.paper_broker?.ready ? "Ready" : "Fail-closed"}</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Model authority</dt><dd className="mt-1 font-semibold">None</dd></div><div><dt style={{ color: "var(--text-mute)" }}>Live execution</dt><dd className="mt-1 font-semibold">Impossible</dd></div></dl>{data.executor.last_entry_block && <p className="mt-4 rounded-lg border px-3 py-2 text-[10px]" style={{ borderColor: "var(--gold)", color: "var(--gold)" }}>Latest block: {data.executor.last_entry_block.ticker ?? "unknown"} · {human(data.executor.last_entry_block.reason)}</p>}</section>
    </div>
  </section>;
}
