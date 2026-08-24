"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchQuote, fetchWatchlists } from "@/lib/api";
import { loadWatchlistTickers } from "@/lib/watchlist";
import { isSupabaseConfigured } from "@/lib/supabase";
import { GUEST_TICKERS } from "@/lib/guestCatalog";

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
const GUEST_DEMO_QUOTES: Record<string, Exclude<Row, null>> = {
  SPY: { price: 780.42, changePct: 0.31 }, QQQ: { price: 692.18, changePct: 0.48 },
  AAPL: { price: 311.24, changePct: -0.22 }, MSFT: { price: 642.73, changePct: 0.67 },
  NVDA: { price: 204.36, changePct: 1.12 }, AMZN: { price: 264.91, changePct: 0.38 },
  GOOGL: { price: 353.28, changePct: -0.14 }, META: { price: 606.52, changePct: 0.19 },
  TSLA: { price: 342.11, changePct: 1.44 }, AMD: { price: 238.64, changePct: 0.82 },
  MU: { price: 989.18, changePct: 2.06 }, AVGO: { price: 421.76, changePct: 0.55 },
};

type Row = { price: number; changePct: number } | null;

type TickerStripProps = {
  /** Currently active ticker — highlighted, and always shown even if not on the watchlist. */
  activeTicker: string;
  onSelect: (ticker: string) => void;
  guestMode?: boolean;
};

export function TickerStrip({ activeTicker, onSelect, guestMode = false }: TickerStripProps) {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [rows, setRows] = useState<Record<string, Row>>({});

  const refresh = useCallback(async () => {
    if (guestMode) {
      setSymbols([...GUEST_TICKERS]);
      setRows(GUEST_DEMO_QUOTES);
      return;
    }
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
  }, [activeTicker, guestMode]);

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
      className="cipher-no-scrollbar flex flex-row items-center gap-0 overflow-x-auto px-3"
      style={{
        background: "var(--panel)",
        borderBottom: "1px solid var(--line)",
        fontFamily: "var(--font-mono)",
        fontSize: "11px",
      }}
      aria-label={guestMode ? "Watchlist quotes · demo, not live" : "Watchlist quotes"}
    >
      {guestMode && (
        <span className="shrink-0 px-3 py-[6px] text-[10px] font-semibold" style={{ color: "var(--gold)" }}>
          Demo tape · not live
        </span>
      )}
      {symbols.map((symbol) => {
        const row = rows[symbol];
        const active = symbol === activeTicker;
        const positive = (row?.changePct ?? 0) >= 0;
        return (
          <button
            key={symbol}
            type="button"
            onClick={() => onSelect(symbol)}
            className="flex shrink-0 flex-row items-baseline gap-[6px] border-r border-[var(--line)] px-3 py-[6px] whitespace-nowrap hover:bg-[var(--panel-2)]"
            style={{
              background: active ? "var(--nav-active)" : "transparent",
              color: active ? "var(--text)" : "var(--text-dim)",
            }}
          >
            <span style={{ fontWeight: 700, letterSpacing: "0.06em" }}>{symbol}</span>
            <span style={{ color: active ? "var(--text)" : "var(--text-dim)" }}>
              {row ? `${guestMode ? "≈" : ""}$${row.price.toFixed(2)}` : "···"}
            </span>
            <span
              style={{
                color: !row ? "var(--text-mute)" : positive ? "var(--positive)" : "var(--neg)",
              }}
            >
              {row ? `${positive ? "+" : ""}${row.changePct.toFixed(2)}%${guestMode ? " demo" : ""}` : "···"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default TickerStrip;
