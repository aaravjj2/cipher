"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { fetchProductStatus, fetchQuote, type ProductStatus, type RealQuote } from "@/lib/api";
import { NightVision } from "@/components/panels/NightVision";
import { OptionsTerminal } from "@/components/panels/OptionsTerminal";
import { Spyglass } from "@/components/panels/Spyglass";
import { CompanyContext } from "@/components/panels/CompanyContext";
import { AskCipher } from "@/components/panels/AskCipher";
import { GuestShowcase } from "@/components/panels/GuestShowcase";
import { feedLabel, isYahooFeed } from "@/lib/feedLabel";

type Tab = "Overview" | "Chart" | "Options" | "Flow" | "Company" | "Agent";
const TABS: Tab[] = ["Overview", "Chart", "Options", "Flow", "Company", "Agent"];

const control = "min-w-fit px-3 py-1.5 text-[10px] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]";
const tile = "border border-[var(--line)] bg-[var(--panel)] p-4 text-left hover:border-[var(--line-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]";

function age(value: number | null) {
  if (value == null) return "unavailable";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

export function TickerWorkbench({ ticker, onNavigate, guestMode = false }: { ticker: string; onNavigate?: (panel: string, ticker?: string) => void; guestMode?: boolean }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const [quote, setQuote] = useState<RealQuote | null>(null);
  const [status, setStatus] = useState<ProductStatus | null>(null);
  const [error, setError] = useState("");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const visibleTabs: Tab[] = guestMode ? ["Overview", "Chart", "Options"] : TABS;
  const jumps = guestMode
    ? [
        { tab: "Chart" as Tab, title: "Validate price", body: "OHLCV, session context, exposure levels and exact chart evidence." },
        { tab: "Options" as Tab, title: "Compare structures", body: "Bid/ask research, Greeks, OI, IV and payoff constraints. No order ticket." },
      ]
    : [
        { tab: "Chart" as Tab, title: "Validate price", body: "OHLCV, session context, exposure levels and exact chart evidence." },
        { tab: "Options" as Tab, title: "Compare structures", body: "Bid/ask research, Greeks, OI, IV and payoff constraints. No order ticket." },
        { tab: "Flow" as Tab, title: "Inspect flow", body: "Observed prints and quote-relative side inference with timestamps." },
        { tab: "Company" as Tab, title: "Check events", body: "Company, earnings, actions and sourced context." },
        { tab: "Agent" as Tab, title: "Challenge the thesis", body: "Ask Cipher with the active ticker and visible evidence limitations." },
      ];

  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % visibleTabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + visibleTabs.length) % visibleTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = visibleTabs.length - 1;
    else return;
    event.preventDefault();
    setTab(visibleTabs[next]);
    tabRefs.current[next]?.focus();
  };

  useEffect(() => {
    const ctrl = new AbortController();
    Promise.all([fetchQuote(ticker, ctrl.signal), guestMode ? Promise.resolve(null) : fetchProductStatus(ticker, ctrl.signal)])
      .then(([nextQuote, nextStatus]) => { setQuote(nextQuote); setStatus(nextStatus); setError(""); })
      .catch((err) => { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Ticker context unavailable"); });
    return () => ctrl.abort();
  }, [ticker, guestMode]);

  if (guestMode && error && !quote) return <GuestShowcase panel="Ticker Workbench" ticker={ticker} />;

  return (
    <section className="space-y-3 font-mono" data-guest-panel={guestMode ? "Ticker Workbench" : undefined} data-guest-source={guestMode ? (quote ? "live" : "loading") : undefined}>
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-[var(--line)] pb-3">
        <div>
          <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--gold)]">One ticker · research only</p>
          <h1 className="mt-1 font-sans text-xl font-semibold">Ticker Workbench</h1>
          <p className="mt-1 text-[11px] text-[var(--text-mute)]">One evidence context for {ticker} · research only</p>
        </div>
        <div className="text-right">
          <div className="text-lg font-semibold tabular-nums">{quote ? `$${quote.price_context.toFixed(2)}` : "—"}</div>
          <div className="text-[10px] tabular-nums" style={{ color: quote && quote.day_change_pct >= 0 ? "var(--positive)" : "var(--negative)" }}>
            {quote ? `${quote.day_change_pct >= 0 ? "+" : ""}${quote.day_change_pct.toFixed(2)}% · ${feedLabel(quote.feed)}${guestMode && !isYahooFeed(quote.feed) ? " · live quote" : ""}` : error || "Loading context…"}
          </div>
        </div>
      </header>
      <div role="tablist" aria-label="Ticker workbench views" className="flex gap-1 overflow-x-auto border border-[var(--line)] bg-[var(--panel)] p-1">
        {visibleTabs.map((item, index) => (
          <button
            key={item}
            type="button"
            ref={(node) => { tabRefs.current[index] = node; }}
            id={`workbench-tab-${item.toLowerCase()}`}
            role="tab"
            aria-controls={`workbench-panel-${item.toLowerCase()}`}
            aria-selected={tab === item}
            tabIndex={tab === item ? 0 : -1}
            onKeyDown={(event) => moveTab(event, index)}
            onClick={() => setTab(item)}
            className={control}
            style={{ background: tab === item ? "var(--nav-active)" : "transparent", color: tab === item ? "var(--text)" : "var(--text-mute)" }}
          >
            {item}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`workbench-panel-${tab.toLowerCase()}`} aria-labelledby={`workbench-tab-${tab.toLowerCase()}`}>
        {tab === "Overview" && (
          <div className="space-y-3">
            {guestMode ? (
              <section aria-label="Guest data access" className="border border-[var(--line)] bg-[var(--panel)] p-4">
                <h2 className="font-sans text-sm font-semibold">Guest market preview</h2>
                <p className="mt-1 text-[10px] leading-5 text-[var(--text-mute)]">The header and this quote are live. The ticker tape is a demo snapshot, not those prints. Quote, chart, and options-chain views are available for the guest symbol set. Ticker Workbench Options is the live chain; the Options Terminal nav item is a demo showcase. Flow, company context, agent tools, private writes, and every order capability stay locked. No order ticket.</p>
              </section>
            ) : (
              <section aria-label="Ticker data readiness" className="border border-[var(--line)] bg-[var(--panel)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className="font-sans text-sm font-semibold">Evidence readiness</h2>
                    <p className="text-[10px] text-[var(--text-mute)]">{status ? `${status.session.phase} · ${status.session.exchange_time} ${status.session.timezone}` : "Loading session state…"} No order ticket.</p>
                  </div>
                  <span className="rounded-full border border-[var(--line)] px-2 py-1 text-[9px] font-bold" style={{ color: status?.healthy ? "var(--positive)" : "var(--negative)" }}>{status?.healthy ? "READY" : "REVIEW GAPS"}</span>
                </div>
                <div className="mt-3 grid gap-px bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
                  {(status?.items ?? []).map((item) => (
                    <div key={item.name} className="bg-[var(--panel-2)] px-3 py-2">
                      <div className="text-[9px] font-bold uppercase text-[var(--text-mute)]">{item.name}</div>
                      <div className="mt-1 text-[10px]">{item.state} · {age(item.age_seconds)}</div>
                      <div className="text-[9px] text-[var(--text-mute)]">{item.source}</div>
                    </div>
                  ))}
                </div>
                {status?.exceptions.length ? <div className="mt-3 border border-[var(--negative)] px-3 py-2 text-[10px] text-[var(--negative)]">{status.exceptions.map((item) => `${item.name}: ${item.state}`).join(" · ")}</div> : null}
              </section>
            )}
            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {jumps.map((item) => (
                <button key={item.tab} type="button" onClick={() => setTab(item.tab)} className={tile}>
                  <div className="font-sans text-xs font-semibold">{item.title}</div>
                  <div className="mt-1 text-[10px] leading-5 text-[var(--text-mute)]">{item.body}</div>
                </button>
              ))}
              {!guestMode && (
                <button type="button" onClick={() => onNavigate?.("Trader Journal", ticker)} className={tile}>
                  <div className="font-sans text-xs font-semibold">Record the plan</div>
                  <div className="mt-1 text-[10px] leading-5 text-[var(--text-mute)]">Save thesis, invalidation, targets, evidence and subsequent review.</div>
                </button>
              )}
            </section>
          </div>
        )}
        {tab === "Chart" && <NightVision ticker={ticker} guestMode={guestMode} />}
        {tab === "Options" && (
          <>
            {guestMode && <p className="mb-2 text-[9px] font-bold uppercase" style={{ color: "var(--gold)", letterSpacing: "0.08em" }}>Live chain · not the Options Terminal demo nav</p>}
            <OptionsTerminal ticker={ticker} onNavigate={onNavigate} />
          </>
        )}
        {tab === "Flow" && !guestMode && <Spyglass ticker={ticker} />}
        {tab === "Company" && !guestMode && <CompanyContext ticker={ticker} />}
        {tab === "Agent" && !guestMode && <AskCipher ticker={ticker} />}
      </div>
    </section>
  );
}
