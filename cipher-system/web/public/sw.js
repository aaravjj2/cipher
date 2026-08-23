/* Cipher PWA service worker — minimal by design.
 *
 * The terminal's data is live and no-store, so we never cache /api/* responses.
 * We only cache the app shell and hashed static assets so an installed Android
 * app launches instantly and survives a flaky connection.
 */
const CACHE = "cipher-shell-v2";
const SHELL = ["/", "/index.html", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Never intercept credential/session traffic or cross-origin Supabase requests.
  // The old catch-all static branch cached GET /auth/session, replaying an authenticated
  // profile after logout while the real APIs correctly returned 401.
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return;

  if (request.mode === "navigate") {
    // Fresh-first for the app shell; fall back to the cached shell when offline.
    event.respondWith(
      fetch(request).catch(() => caches.match("/index.html"))
    );
    return;
  }

  const cacheableStatic = url.pathname.startsWith("/_next/static/")
    || url.pathname.startsWith("/icons/")
    || url.pathname === "/manifest.webmanifest"
    || url.pathname === "/favicon.ico";
  if (!cacheableStatic) return;

  // Cache-first for an explicit set of static assets, refreshing in the background.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const clone = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
