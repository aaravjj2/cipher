// Thin typed wrapper around window.localStorage. Cipher's Watchlists, Journal, and Chart
// Saves panels have no server-side backend for this data (confirmed against the legacy
// vanilla-JS frontend at app/public.legacy/app.js, which persists the same 3 features to
// localStorage under a single `cipher_local_v1` blob) — this file is the real data source
// for those panels, not a stand-in for one.

export function readLocal<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeLocal<T>(key: string, value: T): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or disabled — the UI still works, just won't persist */
  }
}
