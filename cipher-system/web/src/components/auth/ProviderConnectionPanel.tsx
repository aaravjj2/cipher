"use client";

import { useEffect, useState } from "react";
import {
  connectProviderSession,
  disconnectProviderSession,
  fetchProviderSessionStatus,
  type ProviderSessionStatus,
} from "@/lib/api";
import { isSupabaseConfigured } from "@/lib/supabase";

function statusLabel(status: ProviderSessionStatus["status"] | null): string {
  if (status === "connected") return "Connected";
  if (status === "expired") return "Expired";
  if (status === "unavailable") return "Unavailable";
  return "Disconnected";
}

export function ProviderConnectionPanel() {
  const [status, setStatus] = useState<ProviderSessionStatus | null>(null);
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [optionsFeed, setOptionsFeed] = useState<"opra" | "indicative">("opra");
  const [stockFeed, setStockFeed] = useState<"sip" | "iex">("sip");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isSupabaseConfigured()) return undefined;
    const controller = new AbortController();
    fetchProviderSessionStatus(controller.signal)
      .then(setStatus)
      .catch(() => setStatus({ status: "unavailable", options_feed: null, stock_feed: null, expires_at: null, read_only: true }));
    return () => controller.abort();
  }, []);

  const connect = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const next = await connectProviderSession({ key, secret, options_feed: optionsFeed, stock_feed: stockFeed });
      setStatus(next);
      setMessage("Connected for this session. Credentials are not persisted.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Provider connection failed.");
    } finally {
      setKey("");
      setSecret("");
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setMessage(null);
    try {
      setStatus(await disconnectProviderSession());
      setMessage("Provider session cleared.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Provider disconnect failed.");
    } finally {
      setBusy(false);
    }
  };

  if (!isSupabaseConfigured()) return null;

  return (
    <section className="flex flex-col gap-4 rounded-[var(--radius)] p-6" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-bold" style={{ color: "var(--text)" }}>Alpaca session connection</h2>
          <p className="mt-1 text-[12px]" style={{ color: "var(--text-dim)" }}>Bring your own read-only market-data credentials for this session-only connection.</p>
        </div>
        <span className="rounded-full px-3 py-1 text-[11px] font-bold uppercase" style={{ color: status?.status === "connected" ? "var(--success)" : "var(--text-mute)", background: "var(--panel-2)" }}>
          {statusLabel(status?.status ?? null)}
        </span>
      </div>

      <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-mute)" }}>
        Keys are sent over HTTPS to the authenticated backend, held only in bounded process memory, and cleared on disconnect, expiry, logout, or backend restart. They are never written to Supabase or browser storage. Cipher uses them only for read-only market-data requests and cannot place broker orders.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-[12px]">
          <span style={{ color: "var(--text-dim)" }}>Alpaca key</span>
          <input type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} className="rounded-md px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
        </label>
        <label className="flex flex-col gap-1.5 text-[12px]">
          <span style={{ color: "var(--text-dim)" }}>Alpaca secret</span>
          <input type="password" autoComplete="off" value={secret} onChange={(event) => setSecret(event.target.value)} className="rounded-md px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
        </label>
        <label className="flex flex-col gap-1.5 text-[12px]">
          <span style={{ color: "var(--text-dim)" }}>Options feed</span>
          <select value={optionsFeed} onChange={(event) => setOptionsFeed(event.target.value as "opra" | "indicative")} className="rounded-md px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}>
            <option value="opra">OPRA</option>
            <option value="indicative">Indicative · degraded</option>
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-[12px]">
          <span style={{ color: "var(--text-dim)" }}>Stock feed</span>
          <select value={stockFeed} onChange={(event) => setStockFeed(event.target.value as "sip" | "iex")} className="rounded-md px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}>
            <option value="sip">SIP</option>
            <option value="iex">IEX · degraded</option>
          </select>
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy || !key || !secret} onClick={() => void connect()} className="rounded-md px-3 py-2 text-[12px] font-semibold disabled:opacity-50" style={{ background: "var(--accent)", color: "var(--bg)" }}>
          {busy ? "Working…" : "Connect for this session"}
        </button>
        <button type="button" disabled={busy || status?.status !== "connected"} onClick={() => void disconnect()} className="rounded-md border px-3 py-2 text-[12px] font-semibold disabled:opacity-50" style={{ borderColor: "var(--line)", color: "var(--text)" }}>
          Disconnect
        </button>
      </div>

      {message && <p role="status" className="text-[12px]" style={{ color: "var(--text-dim)" }}>{message}</p>}
    </section>
  );
}
