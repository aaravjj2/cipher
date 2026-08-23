"use client";

import { useEffect } from "react";

/** Registers the PWA service worker so the terminal can be installed on Android. */
export default function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window !== "undefined" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // Non-fatal: the app works without offline support / installability.
      });
    }
  }, []);

  return null;
}
