"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { addAlert, deleteAlert, fetchAlerts, fetchQuote, type AlertDelivery, type AlertKind, type AlertRule } from "@/lib/api";

const LABELS: Record<AlertKind, string> = {
  price_above: "Price rises above", price_below: "Price falls below",
  day_change_above: "Day change rises above %", day_change_below: "Day change falls below %",
  scanner_score_above: "Saved scanner score above", flow_premium_above: "Session flow premium above $",
  net_gex_above: "Net GEX above", net_gex_below: "Net GEX below",
  atm_iv_above: "Nearest ATM IV above (decimal; .45 = 45%)", atm_spread_above: "Nearest ATM spread above %",
  expiration_days_below: "Portfolio expiration within days", portfolio_delta_abs_above: "Absolute portfolio delta above",
  data_stale_count_above: "Stale/unavailable data count above",
};

export function Alerts({ ticker }: { ticker: string }) {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [deliveries, setDeliveries] = useState<AlertDelivery[]>([]);
  const [kind, setKind] = useState<AlertKind>("price_above");
  const [threshold, setThreshold] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("unsupported");
  const latched = useRef(new Set<string>());

  const reload = useCallback((signal?: AbortSignal) => fetchAlerts(signal).then((value) => { setRules(value.rules); setDeliveries(value.deliveries); }), []);
  useEffect(() => {
    const controller = new AbortController();
    reload(controller.signal).catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Could not load alerts."); });
    Promise.resolve().then(() => setPermission("Notification" in window ? Notification.permission : "unsupported"));
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    if (!rules.length) return;
    let cancelled = false;
    const evaluate = async () => {
      const browserRules = rules.filter((rule) => rule.enabled && ["price_above", "price_below", "day_change_above", "day_change_below"].includes(rule.kind));
      const symbols = [...new Set(browserRules.map((rule) => rule.ticker))];
      const quotes = new Map((await Promise.all(symbols.map(async (symbol) => [symbol, await fetchQuote(symbol)] as const))).map((row) => row));
      if (cancelled) return;
      for (const rule of browserRules) {
        const quote = quotes.get(rule.ticker);
        if (!quote) continue;
        const observed = rule.kind.startsWith("price_") ? quote.price_context : quote.day_change_pct;
        const active = rule.kind.endsWith("above") ? observed >= rule.threshold : observed <= rule.threshold;
        if (active && !latched.current.has(rule.id)) {
          latched.current.add(rule.id);
          if ("Notification" in window && Notification.permission === "granted") new Notification(`Cipher alert · ${rule.ticker}`, { body: `${LABELS[rule.kind]} ${rule.threshold}; observed ${observed.toFixed(2)}` });
        } else if (!active) latched.current.delete(rule.id);
      }
    };
    evaluate().catch(() => {});
    const timer = window.setInterval(() => evaluate().catch(() => {}), 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [rules]);

  async function createRule() {
    const value = Number(threshold);
    if (!Number.isFinite(value)) { setError("Enter a numeric threshold."); return; }
    try { await addAlert({ ticker, kind, threshold: value }); setThreshold(""); setError(null); await reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not add alert."); }
  }

  async function removeRule(id: string) {
    try { await deleteAlert(id); latched.current.delete(id); await reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not delete alert."); }
  }

  async function enableNotifications() {
    if (!("Notification" in window)) return;
    setPermission(await Notification.requestPermission());
  }

  return <div className="flex flex-col gap-4" style={{ color: "var(--text)" }}>
    <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      {/* This copy used to promise a 30-second in-tab poll and nothing else, which stopped
          being true when cipher-market-alert.timer started evaluating rules server-side and
          pushing crossings to Telegram. A panel that misdescribes when its own alerts fire is
          worse than one that says nothing: it is the difference between closing the tab
          confidently and missing a crossing. web/test asserts the current wording. */}
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-[15px] font-semibold">Market alerts</h2><p className="mt-1 text-[11px]" style={{ color: "var(--text-dim)" }}>Checked on the server every 5 minutes and delivered to Telegram, whether or not this tab is open. Also evaluated live here while it is. Rules stay on this private server.</p><p className="mt-1 text-[11px]" style={{ color: "var(--text-mute)" }}>A rule notifies on the crossing, not repeatedly while it holds, and re-arms once it returns to clear. A quote older than 10 minutes is treated as unknown, so a stale price never fires one.</p></div><button type="button" onClick={enableNotifications} disabled={permission === "unsupported"} className="rounded px-3 py-2 text-[10.5px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }} title="Browser notifications for this tab. Telegram delivery is independent and always on.">Browser notifications: {permission}</button></div>
      <p className="mt-3 text-[10px]" style={{ color: "var(--text-mute)" }}>Research notification only. Alerts cannot place, stage, or transmit orders.</p>
    </section>
    <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <h3 className="text-[12px] font-semibold">Add {ticker} rule</h3>
      <div className="mt-3 flex flex-wrap gap-2"><select value={kind} onChange={(event) => setKind(event.target.value as AlertKind)} className="rounded px-3 py-2 text-[11px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>{Object.entries(LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><input aria-label="Alert threshold" value={threshold} onChange={(event) => setThreshold(event.target.value)} type="number" step="any" placeholder="Threshold" className="rounded px-3 py-2 text-[11px]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }} /><button type="button" onClick={createRule} className="rounded px-4 py-2 text-[11px] font-semibold" style={{ background: "var(--accent)", color: "white" }}>Add alert</button></div>
      {error && <p className="mt-2 text-[10.5px]" style={{ color: "var(--neg)" }}>{error}</p>}
    </section>
    <section className="rounded-[var(--radius)] p-4" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      {rules.length === 0 ? <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>No alert rules yet.</p> : rules.map((rule) => <div key={rule.id} className="flex items-center justify-between gap-4 border-b px-2 py-3 last:border-0" style={{ borderColor: "var(--line)" }}><div><span className="font-mono text-[12px]">{rule.ticker}</span><span className="ml-3 text-[11px]" style={{ color: "var(--text-dim)" }}>{LABELS[rule.kind]} <b style={{ color: "var(--text)" }}>{rule.threshold}</b></span></div><button type="button" onClick={() => removeRule(rule.id)} className="text-[10px]" style={{ color: "var(--neg)" }}>Delete</button></div>)}
    </section>
    <section className="rounded-[var(--radius)] p-4" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><h3 className="mb-2 text-[12px] font-semibold">Deduplicated delivery ledger</h3>{deliveries.length === 0 ? <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>No server deliveries yet.</p> : deliveries.slice(0, 30).map((row) => <div key={row.delivery_key} className="border-b py-2 text-[10.5px]" style={{ borderColor: "var(--line)" }}><span>{row.created_at} · {row.channel} · {row.status}</span><p style={{ color: "var(--text-dim)" }}>{row.message}</p><p style={{ color: "var(--text-mute)" }}>Observed {row.observed ?? "unknown"} at {row.observed_at ?? "unknown"}</p></div>)}</section>
  </div>;
}
