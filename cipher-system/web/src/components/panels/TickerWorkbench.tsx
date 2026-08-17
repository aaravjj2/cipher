"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { fetchProductStatus, fetchQuote, type ProductStatus, type RealQuote } from "@/lib/api";
import { NightVision } from "@/components/panels/NightVision";
import { OptionsTerminal } from "@/components/panels/OptionsTerminal";
import { Spyglass } from "@/components/panels/Spyglass";
import { CompanyContext } from "@/components/panels/CompanyContext";
import { AskCipher } from "@/components/panels/AskCipher";

type Tab = "Overview" | "Chart" | "Options" | "Flow" | "Company" | "Agent";
const TABS: Tab[] = ["Overview", "Chart", "Options", "Flow", "Company", "Agent"];

function age(value: number | null) {
  if (value == null) return "unavailable";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

export function TickerWorkbench({ ticker, onNavigate }: { ticker: string; onNavigate?: (panel: string, ticker?: string) => void }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const [quote, setQuote] = useState<RealQuote | null>(null);
  const [status, setStatus] = useState<ProductStatus | null>(null);
  const [error, setError] = useState("");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    setTab(TABS[next]);
    tabRefs.current[next]?.focus();
  };

  useEffect(() => {
    const ctrl = new AbortController();
    Promise.all([fetchQuote(ticker, ctrl.signal), fetchProductStatus(ticker, ctrl.signal)])
      .then(([nextQuote, nextStatus]) => { setQuote(nextQuote); setStatus(nextStatus); setError(""); })
      .catch((err) => { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Ticker context unavailable"); });
    return () => ctrl.abort();
  }, [ticker]);

  return <div className="space-y-4" style={{ fontFamily: "var(--font-mono)" }}>
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-xl font-semibold">Ticker Workbench</h1><p className="text-[11px]" style={{ color: "var(--text-mute)" }}>One evidence context for {ticker} · research only</p></div>
      <div className="text-right"><div className="text-lg font-semibold">{quote ? `$${quote.price_context.toFixed(2)}` : "—"}</div><div className="text-[10px]" style={{ color: quote && quote.day_change_pct >= 0 ? "var(--positive)" : "var(--negative)" }}>{quote ? `${quote.day_change_pct >= 0 ? "+" : ""}${quote.day_change_pct.toFixed(2)}% · ${quote.feed}` : error || "Loading context…"}</div></div>
    </header>
    <div role="tablist" aria-label="Ticker workbench views" className="flex gap-1 overflow-x-auto rounded-lg border p-1" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
      {TABS.map((item, index) => <button key={item} ref={(node) => { tabRefs.current[index] = node; }} id={`workbench-tab-${item.toLowerCase()}`} role="tab" aria-controls={`workbench-panel-${item.toLowerCase()}`} aria-selected={tab === item} tabIndex={tab === item ? 0 : -1} onKeyDown={(event) => moveTab(event, index)} onClick={() => setTab(item)} className="min-w-fit rounded-md px-3 py-1.5 text-[10px] font-semibold" style={{ background: tab === item ? "var(--nav-active)" : "transparent", color: tab === item ? "var(--text)" : "var(--text-mute)" }}>{item}</button>)}
    </div>
    <div role="tabpanel" id={`workbench-panel-${tab.toLowerCase()}`} aria-labelledby={`workbench-tab-${tab.toLowerCase()}`}>
    {tab === "Overview" && <div className="space-y-4">
      <section aria-label="Ticker data readiness" className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="text-sm font-semibold">Evidence readiness</h2><p className="text-[10px]" style={{ color: "var(--text-mute)" }}>{status ? `${status.session.phase} · ${status.session.exchange_time} ${status.session.timezone}` : "Loading session state…"}</p></div><span className="rounded-full border px-2 py-1 text-[9px] font-bold" style={{ borderColor: "var(--line)", color: status?.healthy ? "var(--positive)" : "var(--negative)" }}>{status?.healthy ? "READY" : "REVIEW GAPS"}</span></div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{(status?.items ?? []).map((item) => <div key={item.name} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}><div className="text-[9px] font-bold uppercase" style={{ color: "var(--text-mute)" }}>{item.name}</div><div className="mt-1 text-[10px]">{item.state} · {age(item.age_seconds)}</div><div className="text-[9px]" style={{ color: "var(--text-mute)" }}>{item.source}</div></div>)}</div>
        {status?.exceptions.length ? <div className="mt-3 rounded-lg border px-3 py-2 text-[10px]" style={{ borderColor: "var(--negative)", color: "var(--negative)" }}>{status.exceptions.map((item) => `${item.name}: ${item.state}`).join(" · ")}</div> : null}
      </section>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {[{ tab: "Chart" as Tab, title: "Validate price", body: "OHLCV, session context, exposure levels and exact chart evidence." }, { tab: "Options" as Tab, title: "Compare structures", body: "Executable bid/ask research, Greeks, OI, IV and payoff constraints." }, { tab: "Flow" as Tab, title: "Inspect flow", body: "Observed prints and quote-relative side inference with timestamps." }, { tab: "Company" as Tab, title: "Check events", body: "Company, earnings, actions and sourced context." }, { tab: "Agent" as Tab, title: "Challenge the thesis", body: "Ask Cipher with the active ticker and visible evidence limitations." }].map((item) => <button key={item.tab} type="button" onClick={() => setTab(item.tab)} className="rounded-xl border p-4 text-left" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><div className="text-xs font-semibold">{item.title}</div><div className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>{item.body}</div></button>)}
        <button type="button" onClick={() => onNavigate?.("Trader Journal", ticker)} className="rounded-xl border p-4 text-left" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><div className="text-xs font-semibold">Record the plan</div><div className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>Save thesis, invalidation, targets, evidence and subsequent review.</div></button>
      </section>
    </div>}
    {tab === "Chart" && <NightVision ticker={ticker} />}
    {tab === "Options" && <OptionsTerminal ticker={ticker} onNavigate={onNavigate} />}
    {tab === "Flow" && <Spyglass ticker={ticker} />}
    {tab === "Company" && <CompanyContext ticker={ticker} />}
    {tab === "Agent" && <AskCipher ticker={ticker} />}
    </div>
  </div>;
}
