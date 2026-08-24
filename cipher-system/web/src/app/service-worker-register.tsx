"use client";

import { useEffect } from "react";

/**
 * Registers the PWA service worker so the terminal can be installed on Android.
 *
 * updateViaCache:'none' makes the browser fetch sw.js itself bypassing the HTTP
 * cache, and the periodic registration.update() lets a long-lived (pinned or
 * standalone PWA) session pick up a new worker without a navigation — the old
 * worker otherwise can keep serving stale authenticated state for up to 24h.
 */
export default function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    navigator.serviceWorker
      .register("/sw.js", { updateViaCache: "none" })
      .then((registration) => {
        // Check for updates immediately and then every 15 minutes while open.
        registration.update().catch(() => {});
        timer = setInterval(() => registration.update().catch(() => {}), 15 * 60 * 1000);
      })
      .catch(() => {
        // Non-fatal: the app works without offline support / installability.
      });
    return () => {
      if (timer) clearInterval(timer);
    };
  }, []);

  return null;
}
