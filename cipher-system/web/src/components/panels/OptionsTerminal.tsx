"use client";

import { useEffect, useMemo, useState } from "react";
import { analyzeOptionsStructure, fetchOptionsChain, type BuilderLeg, type OptionTerminalContract, type OptionsBuilderResponse, type OptionsChainResponse } from "@/lib/api";
import { feedLabel } from "@/lib/feedLabel";
import { LoadingStatus } from "@/components/ui/skeleton";

const n = (value: number | null | undefined, digits = 2) => value == null ? "—" : value.toFixed(digits);
const pct = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const money = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function asLeg(contract: OptionTerminalContract, side: "buy" | "sell"): BuilderLeg {
  return { ...contract, contract: contract.symbol, expiration: contract.expiry, side, quantity: 1 };
}

const control = "h-8 border border-[var(--line)] px-2 text-[10px] text-[var(--text-dim)] hover:border-[var(--line-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]";
const goldControl = `${control} text-[var(--gold)]`;

function LegAction({ contract, side, onAdd }: { contract: OptionTerminalContract; side: "buy" | "sell"; onAdd: (leg: BuilderLeg) => void }) {
  const label = side === "buy" ? "+B" : "+S";
  return (
    <button
      type="button"
      onClick={() => onAdd(asLeg(contract, side))}
      className="px-1 font-bold text-[var(--gold)] hover:text-[var(--gold-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]"
      aria-label={`${side} ${contract.type} ${contract.strike}`}
    >
      {label}
    </button>
  );
}

export function OptionsTerminal({ ticker, onNavigate }: { ticker: string; onNavigate?: (panel: string, ticker?: string) => void }) {
  const [chain, setChain] = useState<OptionsChainResponse | null>(null);
  const [expiry, setExpiry] = useState("");
  const [legs, setLegs] = useState<BuilderLeg[]>([]);
  const [analysis, setAnalysis] = useState<OptionsBuilderResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    fetchOptionsChain(ticker, 6, ctrl.signal)
      .then((result) => {
        setChain(result);
        setExpiry(result.expirations[0]?.expiration ?? "");
      })
      .catch((err) => {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Option chain unavailable");
      });
    return () => ctrl.abort();
  }, [ticker]);

  useEffect(() => {
    if (!chain || !legs.length) return;
    const ctrl = new AbortController();
    analyzeOptionsStructure(ticker, chain.spot, legs, ctrl.signal)
      .then(setAnalysis)
      .catch((err) => {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Structure analysis failed");
      });
    return () => ctrl.abort();
  }, [ticker, chain, legs]);

  const selected = chain?.expirations.find((item) => item.expiration === expiry);
  const rows = useMemo(() => selected?.rows ?? [], [selected]);
  const contracts = useMemo(
    () => rows.flatMap((row) => [row.call, row.put].filter((x): x is OptionTerminalContract => Boolean(x))),
    [rows],
  );
  const oiDate = useMemo(() => {
    const dates = [...new Set(contracts.map((c) => c.open_interest_date).filter((d): d is string => Boolean(d)))];
    if (dates.length === 1) return dates[0];
    if (dates.length > 1) return "mixed";
    return null;
  }, [contracts]);
  const atm = contracts.length && chain
    ? contracts.reduce((best, row) => Math.abs(row.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? row : best)
    : null;

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
      const next = chain.expirations[1]?.rows
        .flatMap((r) => [r.call])
        .filter((x): x is OptionTerminalContract => Boolean(x))
        .reduce<OptionTerminalContract | null>((best, x) => !best || Math.abs(x.strike - call.strike) < Math.abs(best.strike - call.strike) ? x : best, null);
      if (next) setLegs([asLeg(call, "sell"), asLeg(next, "buy")]);
    }
  }

  if (error && !chain) {
    return (
      <section data-testid="options-terminal" className="border border-[var(--line)] bg-[var(--panel)] p-5">
        <h1 className="text-lg font-semibold">Options Terminal</h1>
        <p className="mt-2 text-sm text-[var(--negative)]">{error}</p>
        <p className="mt-3 text-xs text-[var(--text-mute)]">Missing chain data stays unavailable. No values are filled with zero. Nothing here can submit an order.</p>
      </section>
    );
  }
  if (!chain) {
    return (
      <section data-testid="options-terminal">
        <LoadingStatus className="text-sm text-[var(--text-mute)]">Loading option chain for {ticker}…</LoadingStatus>
      </section>
    );
  }

  const ivRank = chain.iv_rank == null
    ? `unavailable · ${chain.sessions ?? 0}/${chain.minimum_sessions ?? 20} sessions`
    : `${chain.iv_rank.toFixed(1)} · percentile ${chain.iv_percentile?.toFixed(1) ?? "—"}`;

  const stats = analysis && legs.length > 0
    ? [
        ["Debit", money(analysis.net_debit)],
        ["Credit", money(analysis.net_credit)],
        ["Max profit", analysis.max_profit_unbounded ? "unbounded" : money(analysis.max_profit)],
        ["Max loss", analysis.max_loss_unbounded ? "unbounded" : money(analysis.max_loss)],
        ["Risk/structure", money(analysis.risk_per_structure)],
        ["BE", analysis.breakevens.map((x) => x.toFixed(2)).join(", ") || "—"],
        ["Δ", n(analysis.aggregate_greeks.delta)],
      ] as const
    : [];

  return (
    <section className="space-y-3 font-mono" data-testid="options-terminal">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-[var(--line)] pb-3">
        <div>
          <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--gold)]">Structure research · no order ticket</p>
          <h1 className="mt-1 font-sans text-xl font-semibold">Options Terminal</h1>
          <p className="mt-1 text-[11px] leading-5 text-[var(--text-mute)]">
            {ticker} {chain.spot.toFixed(2)} · {feedLabel(chain.feed)} · newest quote {chain.as_of ?? "—"}
          </p>
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={() => onNavigate?.("Night Vision", ticker)} className={control}>Validate chart</button>
            <button type="button" onClick={() => onNavigate?.("Trader Journal", ticker)} className={control}>Record thesis</button>
          </div>
        </div>
        <div className="text-right text-[10px] tabular-nums text-[var(--text-mute)]">
          IV rank: {ivRank}<br />
          {chain.readiness ?? "COLLECTING"} · {chain.metric?.replaceAll("_", " ") ?? "history pending"}<br />
          {chain.open_interest_caveat}
          <br />
          OI as of {oiDate ?? "unknown"}
        </div>
      </header>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Expirations">
        {chain.expirations.map((item) => {
          const active = item.expiration === expiry;
          return (
            <button
              key={item.expiration}
              type="button"
              onClick={() => setExpiry(item.expiration)}
              className={`border px-3 py-2 text-left text-[11px] tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)] ${active ? "border-[var(--gold)] text-[var(--gold)]" : "border-[var(--line)] text-[var(--text-dim)]"}`}
              aria-pressed={active}
            >
              {item.expiration} · {item.dte}D
              <br />
              <span className="text-[9px] text-[var(--text-mute)]">EM {n(item.expected_move)} · skew {pct(item.put_call_25d_skew)}</span>
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Structure presets">
        {([["long_call", "Long call"], ["csp", "Cash-secured put"], ["covered_call", "Covered call"], ["vertical", "Bull call vertical"], ["collar", "Collar"], ["calendar", "Calendar"]] as const).map(([id, label]) => (
          <button key={id} type="button" onClick={() => preset(id)} className={control}>{label}</button>
        ))}
        <button type="button" onClick={() => setLegs([])} className={goldControl}>Clear</button>
      </div>

      {stats.length > 0 && (
        <dl className="grid gap-px border border-[var(--line)] bg-[var(--line)] text-[10px] sm:grid-cols-3 xl:grid-cols-7">
          {stats.map(([label, value]) => (
            <div key={label} className="bg-[var(--panel-2)] p-2">
              <dt className="text-[var(--text-mute)]">{label}</dt>
              <dd className="mt-1 tabular-nums text-[var(--text)]">{value}</dd>
            </div>
          ))}
          {analysis?.assignment_warning && (
            <div className="bg-[var(--panel)] p-2 text-[var(--gold)] sm:col-span-3 xl:col-span-7">
              Short-option assignment review required{analysis.ex_dividend_warning ? "; short calls also need ex-dividend review" : ""}
            </div>
          )}
          {analysis && analysis.liquidity_warnings.length > 0 && (
            <div className="bg-[var(--panel)] p-2 text-[var(--gold)] sm:col-span-3 xl:col-span-7">Liquidity: {analysis.liquidity_warnings.join(", ")}</div>
          )}
          {analysis?.calendar_caveat && (
            <div className="bg-[var(--panel)] p-2 text-[var(--gold)] sm:col-span-3 xl:col-span-7">{analysis.calendar_caveat}</div>
          )}
        </dl>
      )}

      {legs.length > 0 && (
        <div className="border border-[var(--line)] bg-[var(--panel)] p-3 text-[10px]">
          <div className="mb-2 font-bold uppercase tracking-[0.1em] text-[var(--gold)]">RESEARCH STRUCTURE · NO ORDER TICKET</div>
          {legs.map((leg, index) => (
            <div key={`${leg.contract ?? "stock"}-${index}`} className="flex flex-wrap items-center gap-2 border-t border-[var(--line-soft)] py-1.5">
              <button
                type="button"
                onClick={() => setLegs((old) => old.map((x, i) => i === index ? { ...x, side: x.side === "buy" ? "sell" : "buy" } : x))}
                className={`w-12 ${control}`}
              >
                {leg.side}
              </button>
              <span className="tabular-nums">{leg.type === "stock" ? `${leg.quantity} shares` : `${leg.quantity}x ${leg.expiration} ${leg.strike} ${leg.type}`}</span>
              <button type="button" onClick={() => setLegs((old) => old.filter((_, i) => i !== index))} className="text-[var(--negative)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]">remove</button>
            </div>
          ))}
        </div>
      )}

      <div className="max-h-[520px] overflow-auto border border-[var(--line)]" role="region" aria-label="Option chain">
        <table className="w-full min-w-[1180px] text-center text-[10px] tabular-nums">
          <thead className="sticky top-0 bg-[var(--panel)]">
            <tr>
              {["C actions", "C bid/ask", "C sprd", "C IV", "C Δ", "C Γ", "C θ", "C vol/OI", "Strike", "P vol/OI", "P θ", "P Γ", "P Δ", "P IV", "P sprd", "P bid/ask", "P actions"].map((h) => (
                <th key={h} className="px-2 py-2 font-sans font-medium text-[var(--text-mute)]">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const c = row.call;
              const p = row.put;
              const near = atm && row.strike === atm.strike;
              return (
                <tr key={row.strike} className="border-t border-[var(--line)]" style={{ background: near ? "var(--panel-2)" : undefined }}>
                  <td>{c && <><LegAction contract={c} side="buy" onAdd={(leg) => setLegs((x) => [...x, leg])} /> <LegAction contract={c} side="sell" onAdd={(leg) => setLegs((x) => [...x, leg])} /></>}</td>
                  <td>{n(c?.bid)}/{n(c?.ask)}</td>
                  <td title={c?.liquidity_flags.join(", ")}>{n(c?.spread_pct, 1)}%</td>
                  <td>{pct(c?.iv)}</td>
                  <td>{n(c?.delta, 3)}</td>
                  <td>{n(c?.gamma, 4)}</td>
                  <td>{n(c?.theta, 3)}</td>
                  <td>{n(c?.volume, 0)}/{n(c?.open_interest, 0)}</td>
                  <td className="px-3 py-2 font-bold text-[var(--text)]">{row.strike}</td>
                  <td>{n(p?.volume, 0)}/{n(p?.open_interest, 0)}</td>
                  <td>{n(p?.theta, 3)}</td>
                  <td>{n(p?.gamma, 4)}</td>
                  <td>{n(p?.delta, 3)}</td>
                  <td>{pct(p?.iv)}</td>
                  <td title={p?.liquidity_flags.join(", ")}>{n(p?.spread_pct, 1)}%</td>
                  <td>{n(p?.bid)}/{n(p?.ask)}</td>
                  <td>{p && <><LegAction contract={p} side="buy" onAdd={(leg) => setLegs((x) => [...x, leg])} /> <LegAction contract={p} side="sell" onAdd={(leg) => setLegs((x) => [...x, leg])} /></>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] leading-5 text-[var(--text-mute)]">
        Executable research marks use ask for buys and bid for sells. Liquidity flags expose stale/wide/low-volume/low-OI conditions. Missing IV, Greeks, or open interest stay unavailable. OI as of {oiDate ?? "unknown"}. Nothing here can submit an order.
      </p>
    </section>
  );
}
