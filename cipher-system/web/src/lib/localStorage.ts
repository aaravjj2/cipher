// Thin typed wrapper around window.localStorage for local mode. Hosted user-owned
// records use the authenticated API/repository paths; local mode retains the legacy
// browser-local behavior so the standalone terminal remains offline-friendly.

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
