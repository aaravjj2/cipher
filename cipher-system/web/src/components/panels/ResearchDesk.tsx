"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchResearchDesk, type ResearchCandidate, type ResearchDeskResponse } from "@/lib/api";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

const when = (value?: string | null) => value ? new Date(value).toLocaleString("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
}) : "—";
const price = (value?: number | null) => value == null ? "—" : `$${value.toFixed(2)}`;

function Confidence({ value }: { value: ResearchCandidate["derived"]["confidence"] }) {
  const color = value === "higher" ? "var(--positive)" : value === "developing" ? "var(--gold)" : "var(--text-mute)";
  return <span className="rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase" style={{ borderColor: color, color }}>{value}</span>;
}

function CandidateRow({ row, onNavigate }: { row: ResearchCandidate; onNavigate?: (panel: string, ticker?: string) => void }) {
  const [open, setOpen] = useState(false);
  const bullish = row.derived.direction === "BULLISH";
  return <article className="rounded-xl border" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
    <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} className="grid w-full grid-cols-[32px_1fr_auto] items-center gap-3 p-3 text-left sm:grid-cols-[32px_90px_86px_72px_1fr_auto]">
      <span className="text-[11px]" style={{ color: "var(--text-mute)" }}>#{row.rank}</span>
      <strong className="text-sm">{row.ticker}</strong>
      <span className="hidden text-[11px] sm:block" style={{ color: bullish ? "var(--positive)" : row.derived.direction === "BEARISH" ? "var(--negative)" : "var(--text-mute)" }}>{row.derived.direction}</span>
      <span className="hidden text-xs sm:block">{row.derived.ranking_score?.toFixed(1) ?? "—"}</span>
      <span className="hidden truncate text-[10px] sm:block" style={{ color: "var(--text-dim)" }}>{row.derived.research_template}</span>
      <Confidence value={row.derived.confidence} />
    </button>
    {open && <div className="grid gap-4 border-t p-4 lg:grid-cols-[1.2fr_1fr]" style={{ borderColor: "var(--line)" }}>
      <div>
        <p className="mb-2 text-[9px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>Observed evidence</p>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[11px]">
          <div className="flex justify-between gap-3"><dt>Spot</dt><dd>{price(row.observed.spot)}</dd></div>
          <div className="flex justify-between gap-3"><dt>Feed</dt><dd>{row.observed.feed ?? "unknown"}</dd></div>
          <div className="flex justify-between gap-3"><dt>Target</dt><dd>{price(row.observed.target)}</dd></div>
          <div className="flex justify-between gap-3"><dt>Invalidation</dt><dd>{price(row.observed.invalidation)}</dd></div>
          <div className="flex justify-between gap-3"><dt>Coverage</dt><dd>{row.observed.coverage.status}</dd></div>
          <div className="flex justify-between gap-3"><dt>Source time</dt><dd>{when(row.observed.scanner_as_of)}</dd></div>
        </dl>
      </div>
      <div>
        <p className="mb-2 text-[9px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>Derived research read</p>
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-dim)" }}>{row.derived.thesis ?? "No thesis available."}</p>
        {!!row.derived.blockers.length && <p className="mt-2 text-[10px]" style={{ color: "var(--gold)" }}>Hold: {row.derived.blockers.join(" · ")}</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => onNavigate?.("Options Terminal", row.ticker)} className="rounded-md border px-2.5 py-1 text-[10px]" style={{ borderColor: "var(--line)" }}>Options</button>
          <button type="button" onClick={() => onNavigate?.("Night Vision", row.ticker)} className="rounded-md border px-2.5 py-1 text-[10px]" style={{ borderColor: "var(--line)" }}>Chart</button>
          <button type="button" onClick={() => onNavigate?.("Trader Journal", row.ticker)} className="rounded-md border px-2.5 py-1 text-[10px]" style={{ borderColor: "var(--line)" }}>Journal</button>
        </div>
      </div>
    </div>}
  </article>;
}

export function ResearchDesk({ onNavigate }: { onNavigate?: (panel: string, ticker?: string) => void }) {
  const [data, setData] = useState<ResearchDeskResponse | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"intraday" | "weekly">("intraday");
  useEffect(() => {
    const ctrl = new AbortController();
    fetchResearchDesk(ctrl.signal).then(setData).catch((reason) => { if (!ctrl.signal.aborted) setError(reason instanceof Error ? reason.message : "Research Desk unavailable"); });
    return () => ctrl.abort();
  }, []);
  const candidates = useMemo(() => data?.candidates?.[tab] ?? [], [data, tab]);
  if (error) return <div className="rounded-xl border p-4 text-sm" style={{ borderColor: "var(--line)", color: "var(--negative)" }}>{error}</div>;
  if (!data) return <SkeletonRegion label="Loading scheduled research…"><Skeleton className="h-24 w-full" /><Skeleton className="h-72 w-full" /></SkeletonRegion>;
  return <div className="flex flex-col gap-4" style={{ fontFamily: "var(--font-mono)" }}>
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-xl font-semibold">Research Desk</h1><p className="mt-1 text-[11px]" style={{ color: "var(--text-mute)" }}>Scheduled evidence review · generated {when(data.generated_at)} ET · {data.universe?.length ?? 0} symbols · Finviz discovery {data.discovery?.status ?? "not configured"}</p></div>
      <div className="flex rounded-lg border p-1" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        {(["intraday", "weekly"] as const).map((value) => <button key={value} type="button" onClick={() => setTab(value)} className="rounded-md px-3 py-1.5 text-[10px] font-bold uppercase" style={{ background: tab === value ? "var(--nav-active)" : "transparent", color: tab === value ? "var(--text)" : "var(--text-mute)" }}>{value}</button>)}
      </div>
    </header>
    <div className="rounded-xl border px-4 py-3 text-[10px] leading-relaxed" style={{ borderColor: "var(--line)", background: "var(--panel-2)", color: "var(--text-dim)" }}>
      {data.method ?? data.message} Rankings are research heuristics, not probabilities. Defined-risk templates require human review and cannot place orders.
    </div>
    <div className="grid grid-cols-3 gap-2 sm:max-w-md">
      <div className="rounded-lg border p-3" style={{ borderColor: "var(--line)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Candidates</span><strong>{candidates.length}</strong></div>
      <div className="rounded-lg border p-3" style={{ borderColor: "var(--line)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Reviewable</span><strong>{candidates.filter((row) => row.derived.eligible_for_deeper_review).length}</strong></div>
      <div className="rounded-lg border p-3" style={{ borderColor: "var(--line)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Errors</span><strong>{data.errors?.length ?? 0}</strong></div>
    </div>
    <section className="space-y-2" aria-label={`${tab} research candidates`}>
      {candidates.map((row) => <CandidateRow key={`${tab}-${row.ticker}`} row={row} onNavigate={onNavigate} />)}
      {!candidates.length && <div className="rounded-xl border p-8 text-center text-[11px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>{data.available ? `No ${tab} candidates passed the current scan.` : "The first scheduled capture has not run yet."}</div>}
    </section>
  </div>;
}
