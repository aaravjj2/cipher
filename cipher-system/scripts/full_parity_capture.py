#!/usr/bin/env python3
"""Record every API payload both products produce, panel by panel, for a 1v1 diff.

The existing capture only tapped whatever the real app fetched when a ticker was
switched, which turned out to be two endpoints. This walks each panel on BOTH the
real product and the local one, recording every fetch/XHR either makes, so the
comparison covers what each app actually renders from rather than one view of it.

Both sides are captured in the same run, back to back, because the alternative is
comparing a live local response against a reference taken an hour earlier — which
already produced one false regression in this project (a 0.019% parity error read
as 0.951% purely from drift).

Read-only: navigates, clicks panel tabs, types a ticker into the app's own search
box, and reads responses the app requested itself. No orders, no settings changes.

Usage:
  python3 scripts/full_parity_capture.py --symbols NVDA,SPY
  python3 scripts/full_parity_capture.py --side real --symbols NVDA
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
    close_session, command, eval_js,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "full_parity"

SITES = {
    "real": "https://www.accessobsidian.com/app#CI",
    "local": "http://127.0.0.1:8283/",
}

# Panels are visited in this order on both sides. Scan panels are excluded on
# purpose: a Cluster/Flash scan is a minutes-long universe sweep, and firing one on
# each side per symbol would dominate the run without adding per-ticker parity.
PANELS = ["Night Vision", "Strike Matrix", "Spyglass", "Trident"]

INSTALL_HOOK = r"""(() => {
  if (window.__cipherTap) { window.__cipherTap.clear(); return 'reused'; }
  const MAX = 600;
  const log = [];
  window.__cipherTap = {log, clear: () => { log.length = 0; }};
  const record = (url, status, body) => {
    if (!url || /\.(js|css|png|jpg|jpeg|svg|woff2?|ico|map)(\?|$)/i.test(url)) return;
    log.push({url: String(url), status, at: Date.now(),
              body: typeof body === 'string' ? body.slice(0, 600000) : null});
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
  XMLHttpRequest.prototype.open = function (m, url, ...rest) {
    this.__cipherUrl = url; return origOpen.call(this, m, url, ...rest);
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
  const el = document.querySelector('#ticker-input')
    || [...document.querySelectorAll('input')].find(i => /ticker/i.test(i.placeholder || i.getAttribute('aria-label') || ''));
  if (!el) return 'no-input';
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(el, sym);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  for (const t of ['keydown', 'keypress', 'keyup']) {
    el.dispatchEvent(new KeyboardEvent(t, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
  }
  const form = el.closest('form');
  if (form) form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  return 'set:' + sym;
})(SYMBOL)"""

CLICK = r"""((label) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const btns = [...document.querySelectorAll('button, a, [role=tab]')];
  const el = btns.find(b => norm(b.innerText) === label)
    || btns.find(b => norm(b.innerText).startsWith(label));
  if (!el) return 'missing';
  el.click();
  return 'clicked';
})(LABEL)"""

DRAIN = "(() => { const l = window.__cipherTap.log.slice(); window.__cipherTap.clear(); return l; })()"
HEADER = "(() => (document.querySelector('header')?.innerText || '').replace(/\\s+/g,' ').trim())()"
MAIN_TEXT = "(() => (document.querySelector('main')?.innerText || document.body.innerText || '').slice(0, 20000))()"


def drain(session):
    out = []
    for call in eval_js(session, DRAIN, timeout=120) or []:
        entry = {"url": call.get("url"), "status": call.get("status")}
        body = call.get("body")
        if body:
            try:
                entry["json"] = json.loads(body)
            except (ValueError, TypeError):
                entry["text"] = body[:2000]
        out.append(entry)
    return out


def capture_side(side, symbols, settle, panel_settle):
    url = SITES[side]
    session = f"parity-{side}"
    print(f"\n--- {side}: {url}")
    command("navigate", {"url": url, "newTab": True, "group_title": f"parity {side}"},
            session, timeout=90)
    result = {"side": side, "url": url, "captured_at": datetime.now(timezone.utc).isoformat(),
              "symbols": {}}
    try:
        # Wait for the shell rather than sleeping a fixed amount — reading a
        # half-rendered app is how this project previously manufactured two bug
        # reports that did not exist.
        for _ in range(40):
            text = eval_js(session, MAIN_TEXT, timeout=30) or ""
            if "Strike Matrix" in text or "Night Vision" in text or len(text) > 200:
                break
            time.sleep(2)
        print("  hook:", eval_js(session, INSTALL_HOOK, timeout=30))

        for symbol in symbols:
            print(f"  {symbol}")
            per_symbol = {"panels": {}}
            result["symbols"][symbol] = per_symbol

            # Panel first, THEN the ticker switch. The real product caches per
            # ticker, so switching symbol before opening a panel means every fetch
            # happens while the wrong panel is mounted and the panel itself records
            # nothing — which is exactly what a first pass produced: zero calls on
            # 6 of 8 real-side panels.
            for panel in PANELS:
                clicked = eval_js(session, CLICK.replace("LABEL", json.dumps(panel)), timeout=60)
                if str(clicked) != "clicked":
                    per_symbol["panels"][panel] = {"error": "panel button not found"}
                    print(f"    {panel:<14} button not found")
                    continue
                time.sleep(panel_settle)
                drain(session)  # discard whatever the panel switch alone triggered

                # Setting the ticker to the value it already holds is a no-op, so a
                # second panel would record nothing. Prime with a different symbol
                # first to guarantee the switch actually fires.
                primer = "IBM" if symbol != "IBM" else "KO"
                eval_js(session, SET_TICKER.replace("SYMBOL", json.dumps(primer)), timeout=60)
                time.sleep(3)
                drain(session)
                eval_js(session, SET_TICKER.replace("SYMBOL", json.dumps(symbol)), timeout=60)
                time.sleep(settle)
                calls = drain(session)

                header = str(eval_js(session, HEADER, timeout=30) or "")
                per_symbol.setdefault("header", header[:400])
                m = re.search(r"/\s*([A-Z][A-Z0-9.\-]{0,6})\s", header)
                per_symbol.setdefault("header_symbol", m.group(1) if m else None)

                per_symbol["panels"][panel] = {
                    "calls": calls,
                    "endpoints": sorted({(c["url"] or "").split("?")[0] for c in calls}),
                    "text": str(eval_js(session, MAIN_TEXT, timeout=60) or "")[:6000],
                }
                names = [e.rsplit("/", 1)[-1] for e in per_symbol["panels"][panel]["endpoints"]]
                print(f"    {panel:<14} {len(calls):>2} calls {names[:5]}")
    finally:
        close_session(session)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NVDA,SPY,AAPL")
    ap.add_argument("--side", choices=("real", "local", "both"), default="both")
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument("--panel-settle", type=float, default=9.0)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    sides = ["real", "local"] if args.side == "both" else [args.side]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    written = []
    for side in sides:
        payload = capture_side(side, symbols, args.settle, args.panel_settle)
        path = OUT_DIR / f"{side}_{stamp}.json"
        path.write_text(json.dumps(payload, indent=2))
        written.append(path)
        print(f"  wrote {path}")

    print("\ncaptured:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
