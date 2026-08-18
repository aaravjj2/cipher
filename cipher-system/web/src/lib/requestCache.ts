/** Small same-tab GET coordinator for Cipher's read-only API.
 *
 * Panels may ask for the same quote/matrix simultaneously. The coordinator
 * shares one in-flight request and keeps a deliberately short, session-aware
 * result cache. A subscriber abort only detaches that subscriber; it never
 * cancels a request another mounted panel still needs.
 */
type Entry = { value: unknown; expiresAt: number };

const values = new Map<string, Entry>();
const inFlight = new Map<string, Promise<unknown>>();
const counters = { network: 0, cacheHits: 0, sharedHits: 0, invalidations: 0 };
let cacheGeneration = 0;

function regularSession(now = new Date()): boolean {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit",
    minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const part = (kind: Intl.DateTimeFormatPartTypes) => parts.find((x) => x.type === kind)?.value ?? "";
  if (["Sat", "Sun"].includes(part("weekday"))) return false;
  const minutes = Number(part("hour")) % 24 * 60 + Number(part("minute"));
  return minutes >= 570 && minutes < 960;
}

export function requestTtl(path: string, now = new Date()): number {
  const live = regularSession(now);
  if (path.startsWith("/api/quote")) return live ? 5_000 : 20_000;
  if (path.startsWith("/api/matrix") || path.startsWith("/api/flow")) return live ? 20_000 : 90_000;
  if (path.startsWith("/api/bars") || path.startsWith("/api/options-chain")) return live ? 30_000 : 300_000;
  if (path.startsWith("/api/scan/universe")) return 15 * 60_000;
  if (path.startsWith("/api/company-context")) return 15 * 60_000;
  if (path.startsWith("/api/product-status")) return live ? 15_000 : 60_000;
  if (path.startsWith("/api/journal") || path.startsWith("/api/watchlists") || path.startsWith("/api/alerts")) return 2_000;
  return live ? 10_000 : 30_000;
}

function withAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

export function coordinatedGet<T>(path: string, loader: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  const now = Date.now();
  const cached = values.get(path);
  if (cached && cached.expiresAt > now) {
    counters.cacheHits += 1;
    return withAbort(Promise.resolve(cached.value as T), signal);
  }
  let pending = inFlight.get(path) as Promise<T> | undefined;
  if (pending) counters.sharedHits += 1;
  if (!pending) {
    counters.network += 1;
    const generation = cacheGeneration;
    pending = loader().then((value) => {
      if (generation === cacheGeneration) {
        values.set(path, { value, expiresAt: Date.now() + requestTtl(path) });
      }
      return value;
    }).finally(() => {
      if (inFlight.get(path) === pending) inFlight.delete(path);
    });
    inFlight.set(path, pending);
  }
  return withAbort(pending, signal);
}

export function invalidateRequests(prefix = "/api/"): void {
  for (const key of values.keys()) if (key.startsWith(prefix)) values.delete(key);
  counters.invalidations += 1;
}

/** Clear all user-scoped read models when the authenticated subject changes. */
export function resetRequestCache(): void {
  cacheGeneration += 1;
  values.clear();
  inFlight.clear();
  counters.invalidations += 1;
}

export function clientRequestStats() {
  return { ...counters, cachedResources: values.size, inFlightResources: inFlight.size };
}
