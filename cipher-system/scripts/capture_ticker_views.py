#!/usr/bin/env python3
"""Capture the real product's Night Vision + Strike Matrix data per ticker.

Why network interception rather than reading the page. The app's `main` element
holds only the toolbar — 106 characters for NVDA — because the ladder and the
matrix render into canvas/SVG and detached containers. Scraping text would yield
rounded, formatted, partial numbers. Hooking `fetch`/`XMLHttpRequest` instead
captures the exact payloads the app itself received, which is what a parity
comparison needs.

The hook is installed once per session, before any ticker is selected, and only
records responses the app requested on its own. Nothing extra is fetched, no
endpoint is called that the app did not call, and no orders are placed.

Usage:
    python3 scripts/capture_ticker_views.py --symbols NVDA,AAPL,SPY
    python3 scripts/capture_ticker_views.py --symbols-file universe.txt --delay 4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_accessobsidian_scans import (  # noqa: E402
    APP_URL, close_session, command, eval_js, wait_for_app_shell,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "ticker_views"
SESSION = "ao-ticker-views"

# Installed once, before any ticker switch. Keeps a bounded ring buffer so a long
# sweep cannot grow the page's memory without limit.
INSTALL_HOOK = r"""(() => {
  if (window.__cipherTap) return 'already';
  const MAX = 400;
  const log = [];
  window.__cipherTap = {log, clear: () => { log.length = 0; }};
  const record = (url, status, body) => {
    if (!url || /\.(js|css|png|jpg|svg|woff2?|ico)(\?|$)/i.test(url)) return;
    log.push({url: String(url), status, at: Date.now(),
              body: typeof body === 'string' ? body.slice(0, 400000) : null});
    if (log.length > MAX) log.shift();
  };

  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await origFetch.apply(this, args);
    try {
      const url = (args[0] && args[0].url) || args[0];
      res.clone().text().then((t) => record(url, res.status, t)).catch(() => {});
    } catch (e) {}
    return res;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__cipherUrl = url;
    return origOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener('load', () => {
      try { record(this.__cipherUrl, this.status, this.responseText); } catch (e) {}
    });
    return origSend.apply(this, args);
  };
  return 'installed';
})()"""

SET_TICKER = r"""((sym) => {
  const el = document.querySelector('#ticker-input');
  if (!el) return 'no-input';
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(el, sym);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  for (const type of ['keydown', 'keypress', 'keyup']) {
    el.dispatchEvent(new KeyboardEvent(type, {
      key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
    }));
  }
  const form = el.closest('form');
  if (form) form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  return 'set:' + sym;
})(SYMBOL)"""

DRAIN = "(() => { const l = window.__cipherTap.log.slice(); window.__cipherTap.clear(); return l; })()"
HEADER = "(() => (document.querySelector('header')?.innerText || '').replace(/\\s+/g,' ').trim())()"


def header_symbol(text: str) -> str | None:
    m = re.search(r"NIGHT VISION\s*/\s*([A-Z][A-Z0-9.\-]{0,6})\s", text)
    return m.group(1) if m else None


def capture(symbol: str, settle: float) -> dict:
    eval_js(SESSION, DRAIN, timeout=60)                 # discard prior traffic
    eval_js(SESSION, SET_TICKER.replace("SYMBOL", json.dumps(symbol)), timeout=60)
    time.sleep(settle)
    header = str(eval_js(SESSION, HEADER, timeout=30) or "")
    calls = eval_js(SESSION, DRAIN, timeout=120) or []
    parsed = []
    for c in calls:
        entry = {"url": c.get("url"), "status": c.get("status")}
        body = c.get("body")
        if body:
            try:
                entry["json"] = json.loads(body)
            except (ValueError, TypeError):
                entry["text"] = body[:2000]
        parsed.append(entry)
    return {
        "requested": symbol,
        "header_symbol": header_symbol(header),
        "switched": header_symbol(header) == symbol,
        "header": header[:300],
        "calls": parsed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NVDA,AAPL,SPY")
    ap.add_argument("--symbols-file", default="")
    ap.add_argument("--settle", type=float, default=8.0,
                    help="seconds to wait for the app's own requests to land; too "
                         "short reads a half-loaded view and looks like missing data")
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.symbols_file:
        symbols = [s.strip().upper() for s in
                   Path(args.symbols_file).read_text().split() if s.strip()]
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else OUT_DIR / f"views_{stamp}.json"

    print(f"opening {APP_URL}")
    command("navigate", {"url": APP_URL, "newTab": True, "group_title": "AO views"},
            SESSION, timeout=90)
    results = []
    try:
        wait_for_app_shell(SESSION, timeout_seconds=60)
        time.sleep(3)
        print("hook:", eval_js(SESSION, INSTALL_HOOK, timeout=30))

        for sym in symbols:
            r = capture(sym, args.settle)
            results.append(r)
            urls = {c["url"].split("?")[0].rsplit("/", 1)[-1] for c in r["calls"] if c.get("url")}
            print(f"{sym:<6} switched={str(r['switched']):<5} calls={len(r['calls']):<3} "
                  f"{sorted(urls)[:6]}")
            time.sleep(args.delay)
    finally:
        close_session(SESSION)

    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
