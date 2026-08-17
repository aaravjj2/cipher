"use client";

import { useEffect, useMemo, useState } from "react";
import { analyzeOptionsStructure, fetchOptionsChain, type BuilderLeg, type OptionTerminalContract, type OptionsBuilderResponse, type OptionsChainResponse } from "@/lib/api";

const n = (value: number | null | undefined, digits = 2) => value == null ? "—" : value.toFixed(digits);
const pct = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const money = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function asLeg(contract: OptionTerminalContract, side: "buy" | "sell"): BuilderLeg {
  return { ...contract, contract: contract.symbol, expiration: contract.expiry, side, quantity: 1 };
}

export function OptionsTerminal({ ticker, onNavigate }: { ticker: string; onNavigate?: (panel: string, ticker?: string) => void }) {
  const [chain, setChain] = useState<OptionsChainResponse | null>(null);
  const [expiry, setExpiry] = useState("");
  const [legs, setLegs] = useState<BuilderLeg[]>([]);
  const [analysis, setAnalysis] = useState<OptionsBuilderResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    fetchOptionsChain(ticker, 6, ctrl.signal).then((result) => { setChain(result); setExpiry(result.expirations[0]?.expiration ?? ""); }).catch((err) => { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Option chain unavailable"); });
    return () => ctrl.abort();
  }, [ticker]);

  useEffect(() => {
    if (!chain || !legs.length) return;
    const ctrl = new AbortController();
    analyzeOptionsStructure(ticker, chain.spot, legs, ctrl.signal).then(setAnalysis).catch((err) => { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Structure analysis failed"); });
    return () => ctrl.abort();
  }, [ticker, chain, legs]);

  const selected = chain?.expirations.find((item) => item.expiration === expiry);
  const rows = useMemo(() => selected?.rows ?? [], [selected]);
  const contracts = useMemo(() => rows.flatMap((row) => [row.call, row.put].filter((x): x is OptionTerminalContract => Boolean(x))), [rows]);
  const atm = contracts.length && chain ? contracts.reduce((best, row) => Math.abs(row.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? row : best) : null;

  function preset(name: string) {
    if (!chain || !atm) return;
    const calls = contracts.filter((x) => x.type === "call").sort((a, b) => a.strike - b.strike);
    const puts = contracts.filter((x) => x.type === "put").sort((a, b) => a.strike - b.strike);
    const call = calls.reduce((best, x) => Math.abs(x.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? x : best);
    const put = puts.reduce((best, x) => Math.abs(x.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? x : best);
    const higher = calls.find((x) => x.strike > call.strike) ?? call;
    const lower = [...puts].reverse().find((x) => x.strike < put.strike) ?? put;
    const stock: BuilderLeg = { type: "stock", side: "buy", quantity: 100, entry_price: chain.spot };
    if (name === "long_call") setLegs([asLeg(call, "buy")]);
    if (name === "csp") setLegs([asLeg(lower, "sell")]);
    if (name === "covered_call") setLegs([stock, asLeg(higher, "sell")]);
    if (name === "vertical") setLegs([asLeg(call, "buy"), asLeg(higher, "sell")]);
    if (name === "collar") setLegs([stock, asLeg(lower, "buy"), asLeg(higher, "sell")]);
    if (name === "calendar") {
      const next = chain.expirations[1]?.rows.flatMap((r) => [r.call]).filter((x): x is OptionTerminalContract => Boolean(x)).reduce<OptionTerminalContract | null>((best, x) => !best || Math.abs(x.strike - call.strike) < Math.abs(best.strike - call.strike) ? x : best, null);
      if (next) setLegs([asLeg(call, "sell"), asLeg(next, "buy")]);
    }
  }

  if (error && !chain) return <div style={{ color: "var(--negative)" }}>{error}</div>;
  if (!chain) return <div style={{ color: "var(--text-mute)" }}>Loading executable option chain for {ticker}…</div>;

  return <div className="space-y-4" style={{ fontFamily: "var(--font-mono)" }}>
    <div className="flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-xl font-semibold">Options Terminal</h1><p className="text-[11px]" style={{ color: "var(--text-mute)" }}>{ticker} {chain.spot.toFixed(2)} · {chain.feed} · newest quote {chain.as_of ?? "—"}</p><div className="mt-2 flex gap-2"><button type="button" onClick={() => onNavigate?.("Night Vision", ticker)} className="rounded-md border px-2 py-1 text-[9px]" style={{ borderColor: "var(--line)" }}>Validate chart</button><button type="button" onClick={() => onNavigate?.("Trader Journal", ticker)} className="rounded-md border px-2 py-1 text-[9px]" style={{ borderColor: "var(--line)" }}>Record thesis</button></div></div><div className="text-right text-[10px]" style={{ color: "var(--text-mute)" }}>IV rank: {chain.iv_rank == null ? `unavailable · ${chain.sessions ?? 0}/${chain.minimum_sessions ?? 20} sessions` : `${chain.iv_rank.toFixed(1)} · percentile ${chain.iv_percentile?.toFixed(1) ?? "—"}`}<br />{chain.readiness ?? "COLLECTING"} · {chain.metric?.replaceAll("_", " ") ?? "history pending"}<br />{chain.open_interest_caveat}</div></div>
    <div className="flex flex-wrap gap-2">{chain.expirations.map((item) => <button key={item.expiration} onClick={() => setExpiry(item.expiration)} className="rounded-lg border px-3 py-2 text-[11px]" style={{ borderColor: item.expiration === expiry ? "var(--accent)" : "var(--line)", color: item.expiration === expiry ? "var(--accent)" : "var(--text-dim)" }}>{item.expiration} · {item.dte}D<br /><span className="text-[9px]">EM {n(item.expected_move)} · skew {pct(item.put_call_25d_skew)}</span></button>)}</div>
    <div className="flex flex-wrap gap-2">{[["long_call","Long call"],["csp","Cash-secured put"],["covered_call","Covered call"],["vertical","Bull call vertical"],["collar","Collar"],["calendar","Calendar"]].map(([id,label]) => <button key={id} type="button" onClick={() => preset(id)} className="rounded-lg border px-3 py-1.5 text-[10px]" style={{ borderColor: "var(--line)" }}>{label}</button>)}<button type="button" onClick={() => setLegs([])} className="rounded-lg border px-3 py-1.5 text-[10px]" style={{ borderColor: "var(--line)" }}>Clear</button></div>
    {analysis && legs.length > 0 && <div className="grid gap-2 rounded-xl border p-3 text-[11px] sm:grid-cols-3 xl:grid-cols-6" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><span>Debit {money(analysis.net_debit)}</span><span>Credit {money(analysis.net_credit)}</span><span>Max profit {analysis.max_profit_unbounded ? "unbounded" : money(analysis.max_profit)}</span><span>Max loss {analysis.max_loss_unbounded ? "unbounded" : money(analysis.max_loss)}</span><span>Risk/structure {money(analysis.risk_per_structure)}</span><span>BE {analysis.breakevens.map((x) => x.toFixed(2)).join(", ") || "—"}</span><span>Δ {n(analysis.aggregate_greeks.delta)}</span>{analysis.assignment_warning && <span style={{ color: "var(--gold)" }}>Short-option assignment review required{analysis.ex_dividend_warning ? "; short calls also need ex-dividend review" : ""}</span>}{analysis.liquidity_warnings.length > 0 && <span className="sm:col-span-3" style={{ color: "var(--gold)" }}>Liquidity: {analysis.liquidity_warnings.join(", ")}</span>}{analysis.calendar_caveat && <span className="sm:col-span-3" style={{ color: "var(--gold)" }}>{analysis.calendar_caveat}</span>}</div>}
    {legs.length > 0 && <div className="rounded-xl border p-3 text-[10px]" style={{ borderColor: "var(--line)" }}><div className="mb-2 font-bold">RESEARCH STRUCTURE · NO ORDER TICKET</div>{legs.map((leg, index) => <div key={`${leg.contract ?? "stock"}-${index}`} className="flex flex-wrap items-center gap-2 py-1"><button onClick={() => setLegs((old) => old.map((x,i) => i === index ? { ...x, side: x.side === "buy" ? "sell" : "buy" } : x))} className="w-12 rounded border" style={{ borderColor: "var(--line)" }}>{leg.side}</button><span>{leg.type === "stock" ? `${leg.quantity} shares` : `${leg.quantity}x ${leg.expiration} ${leg.strike} ${leg.type}`}</span><button onClick={() => setLegs((old) => old.filter((_,i) => i !== index))} style={{ color: "var(--negative)" }}>remove</button></div>)}</div>}
    <div className="max-h-[520px] overflow-auto rounded-xl border" style={{ borderColor: "var(--line)" }}><table className="w-full min-w-[1180px] text-center text-[10px]"><thead className="sticky top-0" style={{ background: "var(--panel)" }}><tr>{["C actions","C bid/ask","C sprd","C IV","C Δ","C Γ","C θ","C vol/OI","Strike","P vol/OI","P θ","P Γ","P Δ","P IV","P sprd","P bid/ask","P actions"].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead><tbody>{rows.map((row) => { const c=row.call,p=row.put; return <tr key={row.strike} className="border-t" style={{ borderColor: "var(--line)" }}><td>{c && <><button onClick={() => setLegs((x) => [...x,asLeg(c,"buy")])}>+B</button> <button onClick={() => setLegs((x) => [...x,asLeg(c,"sell")])}>+S</button></>}</td><td>{n(c?.bid)}/{n(c?.ask)}</td><td title={c?.liquidity_flags.join(", ")}>{n(c?.spread_pct,1)}%</td><td>{pct(c?.iv)}</td><td>{n(c?.delta,3)}</td><td>{n(c?.gamma,4)}</td><td>{n(c?.theta,3)}</td><td>{n(c?.volume,0)}/{n(c?.open_interest,0)}</td><td className="px-3 py-2 font-bold">{row.strike}</td><td>{n(p?.volume,0)}/{n(p?.open_interest,0)}</td><td>{n(p?.theta,3)}</td><td>{n(p?.gamma,4)}</td><td>{n(p?.delta,3)}</td><td>{pct(p?.iv)}</td><td title={p?.liquidity_flags.join(", ")}>{n(p?.spread_pct,1)}%</td><td>{n(p?.bid)}/{n(p?.ask)}</td><td>{p && <><button onClick={() => setLegs((x) => [...x,asLeg(p,"buy")])}>+B</button> <button onClick={() => setLegs((x) => [...x,asLeg(p,"sell")])}>+S</button></>}</td></tr>; })}</tbody></table></div>
    <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>Executable research marks use ask for buys and bid for sells. Liquidity flags expose stale/wide/low-volume/low-OI conditions. Nothing here can submit an order.</p>
  </div>;
}
