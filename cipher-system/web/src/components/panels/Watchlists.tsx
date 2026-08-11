"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { cn } from "@/lib/utils";
import { fetchQuote } from "@/lib/api";
import { loadWatchlistTickers, saveWatchlistTickers } from "@/lib/watchlist";
import type { WatchlistItem } from "@/types/cipher";

/**
 * My Watchlists panel — add/remove ticker table with today's move, backed by real
 * /api/quote data per ticker. Ticker membership has no server-side backend (confirmed
 * against the legacy vanilla-JS frontend, which stores the same list in localStorage), so
 * it's persisted the same way here (lib/watchlist.ts — shared with Header's "+ Watchlist"
 * quick-add button). There's no lightweight real equivalent to the mock's "Compact 100"
 * score, so that column is dropped rather than showing a fabricated number.
 */

const REFRESH_MS = 15_000;

function formatPct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatPrice(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatSignedDollar(value: number): string {
  const sign = value < 0 ? "-" : "+";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

/** Normalizes free-text ticker input to the "$TICKER" display form used by this panel. */
function normalizeTicker(raw: string): string {
  const trimmed = raw.trim().toUpperCase().replace(/^\$/, "");
  return trimmed;
}

// ---------------------------------------------------------------------------
// Column layout — shared by the header row and every data row so cells line up.
// ---------------------------------------------------------------------------

const GRID_TEMPLATE_COLUMNS = "1.4fr 100px 110px 100px 34px";
const HEADER_CELLS = ["TICKER", "% CHANGE", "$ CHANGE", "PRICE"];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Watchlists() {
  const [tickers, setTickers] = useState<string[]>([]);
  const [items, setItems] = useState<Record<string, WatchlistItem>>({});
  const [inputValue, setInputValue] = useState("");
  const [loaded, setLoaded] = useState(false);

  // Load persisted membership on mount (client-only — avoids a hydration mismatch against
  // whatever's already in localStorage, and picks up anything added via Header's
  // "+ Watchlist" button on a different panel).
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) { setTickers(loadWatchlistTickers()); setLoaded(true); }
    });
    return () => { cancelled = true; };
  }, []);

  const refreshQuotes = useCallback(async (list: string[]) => {
    await Promise.all(
      list.map(async (ticker) => {
        try {
          const q = await fetchQuote(ticker);
          setItems((prev) => ({
            ...prev,
            [ticker]: {
              ticker,
              price: q.price_context,
              changePct: q.day_change_pct,
              changeAbs: q.price_context - q.prior_close,
              compactScore: null,
            },
          }));
        } catch {
          setItems((prev) => ({
            ...prev,
            [ticker]: { ticker, price: null, changePct: null, changeAbs: null, compactScore: null },
          }));
        }
      })
    );
  }, []);

  useEffect(() => {
    if (!loaded) return;
    refreshQuotes(tickers);
    const interval = setInterval(() => refreshQuotes(tickers), REFRESH_MS);
    return () => clearInterval(interval);
  }, [loaded, tickers, refreshQuotes]);

  const addTicker = () => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    const ticker = normalizeTicker(trimmed);
    if (tickers.includes(ticker)) {
      setInputValue("");
      return;
    }
    const next = [...tickers, ticker];
    setTickers(next);
    saveWatchlistTickers(next);
    setInputValue("");
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    addTicker();
  };

  const removeTicker = (ticker: string) => {
    const next = tickers.filter((t) => t !== ticker);
    setTickers(next);
    saveWatchlistTickers(next);
  };

  return (
    <section className="watchlists flex flex-col gap-6" style={{ color: "var(--text)" }}>
      {/* Heading */}
      <div>
        <h1 className="text-[28px] font-bold leading-tight" style={{ color: "var(--text)" }}>
          My Watchlists
        </h1>
        <p className="text-[14px]" style={{ color: "var(--text-mute)" }}>
          Your tickers, with today&apos;s live move from the Cipher core service.
        </p>
      </div>

      {/* Add-ticker row */}
      <form onSubmit={handleSubmit} className="flex flex-row items-center gap-2">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Add a ticker (e.g. NVDA)"
          aria-label="Add a ticker"
          className="flex-1 min-w-0 h-[38px] px-3 rounded-[8px] outline-none"
          style={{
            background: "var(--panel-2)",
            border: "1px solid var(--line)",
            color: "var(--text)",
            fontFamily: "var(--font-mono)",
            fontSize: "13px",
          }}
        />
        <button
          type="submit"
          aria-label="Add ticker"
          className="grid place-items-center w-[38px] h-[38px] rounded-[8px] shrink-0 font-bold text-[18px] leading-none"
          style={{ background: "var(--accent)", color: "#ffffff" }}
        >
          +
        </button>
      </form>

      {/* Table — horizontally scrollable within its own container (same pattern as Strike Matrix) */}
      <div className="watchlists-scroll overflow-x-auto rounded-[10px]" style={{ border: "1px solid var(--line)" }}>
        <div style={{ minWidth: "460px" }}>
          {/* Header row */}
          <div className="grid" style={{ display: "grid", gridTemplateColumns: GRID_TEMPLATE_COLUMNS }}>
            {HEADER_CELLS.map((label, i) => (
              <div
                key={label}
                className="text-[11px] font-bold uppercase px-3 py-[10px]"
                style={{
                  letterSpacing: "0.1em",
                  color: "var(--text-mute)",
                  fontFamily: "var(--font-mono)",
                  textAlign: i === 0 ? "left" : "right",
                }}
              >
                {label}
              </div>
            ))}
            <div />
          </div>

          {/* Data rows */}
          {tickers.map((ticker) => (
            <WatchlistRow
              key={ticker}
              item={items[ticker] ?? { ticker, price: null, changePct: null, changeAbs: null, compactScore: null }}
              onRemove={() => removeTicker(ticker)}
            />
          ))}

          {tickers.length === 0 && (
            <div
              className="px-3 py-6 text-center text-[13px]"
              style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}
            >
              No tickers yet — add one above.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function WatchlistRow({ item, onRemove }: { item: WatchlistItem; onRemove: () => void }) {
  const isEmpty = item.price === null;
  const isPositive = (item.changePct ?? 0) >= 0;

  return (
    <div
      className="grid items-center"
      style={{ display: "grid", gridTemplateColumns: GRID_TEMPLATE_COLUMNS, borderBottom: "1px solid var(--line-soft)" }}
    >
      <div className="px-3 py-3 font-bold" style={{ fontFamily: "var(--font-mono)", color: "var(--text)", fontSize: "13px" }}>
        ${item.ticker}
      </div>

      {isEmpty ? (
        <div className="col-span-3 px-3 py-3 text-right" style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}>
          …
        </div>
      ) : (
        <>
          <div
            className="px-3 py-3 text-right font-semibold"
            style={{ fontFamily: "var(--font-mono)", fontSize: "13px", color: isPositive ? "var(--accent)" : "var(--neg)" }}
          >
            {formatPct(item.changePct as number)}
          </div>
          <div
            className="px-3 py-3 text-right font-semibold"
            style={{ fontFamily: "var(--font-mono)", fontSize: "13px", color: isPositive ? "var(--accent)" : "var(--neg)" }}
          >
            {formatSignedDollar(item.changeAbs as number)}
          </div>
          <div className="px-3 py-3 text-right" style={{ fontFamily: "var(--font-mono)", fontSize: "13px", color: "var(--text)" }}>
            {formatPrice(item.price as number)}
          </div>
        </>
      )}

      <div className="px-2 flex justify-center">
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${item.ticker}`}
          className={cn("grid place-items-center w-[22px] h-[22px] rounded-[6px] shrink-0", "transition-colors duration-150")}
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--neg)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-mute)")}
        >
          ×
        </button>
      </div>
    </div>
  );
}

export default Watchlists;
