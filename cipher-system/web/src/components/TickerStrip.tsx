"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchQuote, fetchWatchlists } from "@/lib/api";
import { loadWatchlistTickers } from "@/lib/watchlist";
import { isSupabaseConfigured } from "@/lib/supabase";

/**
 * Always-visible quote strip under the header — the watchlist as a ticker tape, so the
 * symbols the user follows stay on screen no matter which panel is open, and clicking one
 * switches the active ticker.
 *
 * Quotes come from the same real `/api/quote` endpoint and the same
 * `Promise.all(fetchQuote)` + interval pattern the Watchlists panel already uses, at the
 * same 15s cadence — nothing new is polled and no value is derived here.
 *
 * Membership is re-read from the authenticated watchlist API in hosted mode and from
 * localStorage in standalone mode. Polling keeps same-tab writes visible without relying
 * on a browser `storage` event.
 */

const REFRESH_MS = 15_000;
/** One request per symbol per tick, so the strip is capped rather than fanning out over a
 *  watchlist someone has grown to 60 names. */
const MAX_SYMBOLS = 12;

type Row = { price: number; changePct: number } | null;

type TickerStripProps = {
  /** Currently active ticker — highlighted, and always shown even if not on the watchlist. */
  activeTicker: string;
  onSelect: (ticker: string) => void;
};

export function TickerStrip({ activeTicker, onSelect }: TickerStripProps) {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [rows, setRows] = useState<Record<string, Row>>({});

  const refresh = useCallback(async () => {
    let watchlist: string[] = [];
    try {
      watchlist = isSupabaseConfigured()
        ? (await fetchWatchlists()).watchlists.flatMap((list) => list.tickers)
        : loadWatchlistTickers();
    } catch {
      // The active ticker still remains visible when the user-state service is unavailable.
    }
    const merged = [activeTicker, ...watchlist.filter((t) => t !== activeTicker)]
      .filter(Boolean)
      .slice(0, MAX_SYMBOLS);
    const entries = await Promise.all(
      merged.map(async (symbol): Promise<[string, Row]> => {
        try {
          const q = await fetchQuote(symbol);
          return [symbol, { price: q.price_context, changePct: q.day_change_pct }];
        } catch {
          return [symbol, null];
        }
      })
    );
    // Symbols and rows are committed together, after the quotes resolve, so the strip
    // never renders a row of placeholders and a dropped watchlist symbol can't linger in
    // `rows` — the map is replaced wholesale rather than merged into.
    setSymbols(merged);
    setRows(Object.fromEntries(entries));
  }, [activeTicker]);

  // Self-rescheduling rather than setInterval: the next round starts REFRESH_MS after the
  // previous one *finished*, so a slow response to a dozen quote requests can't stack
  // overlapping waves on top of each other the way a fixed interval would.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await refresh();
      if (cancelled) return;
      timer = setTimeout(tick, REFRESH_MS);
    };
    timer = setTimeout(tick, 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  if (!symbols.length) return null;

  return (
    <div
      className="flex flex-row items-center gap-[6px] overflow-x-auto px-4 py-[5px]"
      style={{
        background: "color-mix(in srgb, var(--panel) 60%, transparent)",
        borderBottom: "1px solid var(--line)",
        fontFamily: "var(--font-mono)",
        fontSize: "11px",
      }}
      aria-label="Watchlist quotes"
    >
      {symbols.map((symbol) => {
        const row = rows[symbol];
        const active = symbol === activeTicker;
        const positive = (row?.changePct ?? 0) >= 0;
        return (
          <button
            key={symbol}
            type="button"
            onClick={() => onSelect(symbol)}
            className="flex shrink-0 flex-row items-baseline gap-[7px] rounded-[6px] px-[9px] py-[3px] whitespace-nowrap"
            style={{
              background: active ? "var(--nav-active)" : "var(--panel-2)",
              border: "1px solid var(--line)",
              color: active ? "var(--text)" : "var(--text-dim)",
            }}
          >
            <span style={{ fontWeight: 700, letterSpacing: "0.06em" }}>{symbol}</span>
            <span style={{ color: active ? "var(--text)" : "var(--text-dim)" }}>
              {row ? `$${row.price.toFixed(2)}` : "···"}
            </span>
            <span
              style={{
                // Cipher uses purple=up / red=down, not the conventional green=up — same
                // convention as Header's quote block.
                color: !row ? "var(--text-mute)" : positive ? "var(--accent)" : "var(--neg)",
              }}
            >
              {row ? `${positive ? "+" : ""}${row.changePct.toFixed(2)}%` : "···"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default TickerStrip;
