import { readLocal, writeLocal } from "@/lib/localStorage";

const STORAGE_KEY = "cipher_watchlist_v1";
const DEFAULT_TICKERS = ["SPY", "QQQ", "IWM", "NVDA", "AAPL"];

export function loadWatchlistTickers(): string[] {
  return readLocal<string[]>(STORAGE_KEY, DEFAULT_TICKERS);
}

export function saveWatchlistTickers(tickers: string[]): void {
  writeLocal(STORAGE_KEY, tickers);
}

/** Adds a ticker (called from Header's "+ Watchlist" button) and returns the updated list. */
export function addToWatchlist(ticker: string): string[] {
  const current = loadWatchlistTickers();
  const normalized = ticker.trim().toUpperCase();
  if (!normalized || current.includes(normalized)) return current;
  const next = [...current, normalized];
  saveWatchlistTickers(next);
  return next;
}
