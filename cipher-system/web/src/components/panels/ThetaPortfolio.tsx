"use client";

import { useState } from "react";
import type { PaperPortfoliosResponse } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { hostedApiUrl } from "@/lib/supabase";

type Candidate = { id: number; observed_at: string; body: string; reason: string; media_json: string | null };
const display = (value: unknown) => value == null ? "Unknown" : String(value);

export function ThetaPortfolio({ data }: { data: NonNullable<PaperPortfoliosResponse["theta"]> }) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [error, setError] = useState("");
  const [corrections, setCorrections] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  async function request(body?: object) {
    const token = await getAccessToken();
    const response = await fetch(hostedApiUrl("/api/theta-review"), { method: body ? "POST" : "GET", credentials: "include", headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Review unavailable");
    return result;
  }
  async function review(id?: number, action?: string) {
    setBusy(true); setError("");
    try {
      if (id != null) await request({ candidate_id: id, action, correction: corrections[id] || undefined });
      setCandidates((await request()).candidates);
    } catch (err) { setError(err instanceof Error ? err.message : "Review unavailable"); }
    finally { setBusy(false); }
  }
  const book = data.quote_based;
  return <section className="space-y-3 border border-[var(--line)] bg-[var(--panel)] p-4 text-xs">
    <h2 className="text-sm font-semibold">Theta · {book.mode.replaceAll("_", " ")}</h2>
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      <div>Available cash: {display(book.available_cash)}</div><div>Open exposure: {display(book.open_exposure)}</div>
      <div>Pending exits: {display(book.pending_exits)}</div><div>Unresolved P&amp;L: {display(book.unresolved_pnl)}</div>
      <div>Realized P&amp;L: {display(book.realized_pnl)}</div><div>Execution: {display(book.execution_health)}</div>
      <div>Data: {display(book.data_health)}</div><div>Notifications: {display(book.notification_health)}</div>
    </div>
    {book.rollout && !book.rollout.eligible && <p>Activation waiting: {book.rollout.reasons.map(r => r.replaceAll("_", " ")).join("; ")}</p>}
    <div className="overflow-auto"><table className="w-full text-left" aria-label="Theta quote positions"><thead><tr>{["Position", "Status", "Ticker", "P&L", "Quote age (s)", "Pending exit", "Data issue"].map(h => <th key={h} className="p-1">{h}</th>)}</tr></thead><tbody>{book.positions.map(p => <tr key={String(p.id)}>{["id", "status", "symbol", "mark_pnl", "quote_age_seconds", "pending_reason", "mark_error"].map(k => <td key={k} className="p-1">{display(p[k])}</td>)}</tr>)}</tbody></table></div>
    <details><summary>Historical Theta signal-price ledger · validated P&amp;L unknown</summary>
      <p>Telegram-reported estimated total: {display(data.historical.reported_estimated_pnl)}. Unpriced historical closes: {display(data.historical.unknown_closed_pnl)}.</p>
      <div className="overflow-auto"><table aria-label="Theta historical positions"><tbody>{data.historical.positions.map(p => <tr key={String(p.id)}>{["id", "symbol", "expiration", "status", "entry_price", "realized_pnl"].map(k => <td key={k} className="p-2">{display(p[k])}</td>)}</tr>)}</tbody></table></div>
    </details>
    <button disabled={busy} className="border px-3 py-2" onClick={() => void review()}>Load image review queue (owner sign-in required)</button>
    {error && <p role="alert">{error}</p>}
    {candidates.map(c => <article key={c.id} className="space-y-2 border p-3">
      <p>Message {c.id} · {c.observed_at} · {c.reason}</p><pre className="whitespace-pre-wrap">{c.body}</pre>
      {c.media_json && <pre className="whitespace-pre-wrap">{display(JSON.parse(c.media_json).text)}</pre>}
      <label className="block">Corrected complete signal<textarea className="block w-full border bg-transparent p-2" value={corrections[c.id] || ""} onChange={e => setCorrections({ ...corrections, [c.id]: e.target.value })} placeholder="Buy SPY 600C qty 1; debit 2.00 points; 2026-09-11; swing" /></label>
      <p>Approval queues fresh contract and quote validation. Signals older than two minutes cannot execute.</p>
      <button disabled={busy} className="mr-2 border px-3 py-2" onClick={() => void review(c.id, "approve")}>Approve correction</button>
      <button disabled={busy} className="border px-3 py-2" onClick={() => void review(c.id, "reject")}>Reject</button>
    </article>)}
  </section>;
}
