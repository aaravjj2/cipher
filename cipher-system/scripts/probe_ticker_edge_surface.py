#!/usr/bin/env python3
"""Read-only probe: does "Edge" appear on any per-ticker surface?

Background. Deriving the real product's Edge score needs many observations, and
the obvious source — the Flash Agentic scan — cannot supply them: all 216 captures
on disk returned exactly 2 rows, and only 9 distinct tickers have ever appeared.
The panel shows two cards, so capturing it more often or from more agents returns
the same two names.

So the question is whether Edge is reachable another way: by searching a ticker of
our choosing in the app's own ticker box (#ticker-input) and reading the resulting
view. If it is, the universe becomes sweepable and Edge becomes fittable. If it is
not, Edge can only ever be sampled two tickers at a time and the honest move is to
keep shipping it as the disclosed "Edge*" approximation.

Also dumps the TS overlay, which is a header button ("TS") whose semantics were
never captured — the reason NightVision.tsx still renders "TS — not yet implemented".

Strictly read-only: types a ticker into the app's own search box and reads text.
Places no orders, submits no forms other than the ticker search, changes no settings.
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
OUT_DIR = ROOT / "data" / "edge_probe"
SESSION = "ao-edge-probe"

# React controls the input, so setting .value directly is ignored — the value must
# be written through the native setter and an input event dispatched, or React
# overwrites it on the next render.
SET_TICKER = r"""((sym) => {
  const el = document.querySelector('#ticker-input');
  if (!el) return 'no-input';
  const proto = Object.getPrototypeOf(el);
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
    || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
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

READ_VIEW = r"""(() => {
  const header = document.querySelector('header')?.innerText || '';
  const main = document.querySelector('main')?.innerText || document.body.innerText || '';
  return {header: header.replace(/\s+/g, ' ').trim(), main};
})()"""


def header_symbol(text: str) -> str | None:
    """Header reads like 'CIPHER NIGHT VISION / CI $282.49 +2.63% ...'."""
    m = re.search(r"NIGHT VISION\s*/\s*([A-Z][A-Z0-9.\-]{0,6})\s", text)
    return m.group(1) if m else None


def find_edge(text: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"Edge[^A-Za-z0-9]{0,3}\d{1,3}", text)]


def probe_symbol(symbol: str, settle: float) -> dict:
    eval_js(SESSION, SET_TICKER.replace("SYMBOL", json.dumps(symbol)), timeout=60)
    time.sleep(settle)
    view = eval_js(SESSION, READ_VIEW, timeout=60) or {}
    header = view.get("header", "")
    main = view.get("main", "")
    return {
        "requested": symbol,
        "header_symbol": header_symbol(header),
        "switched": header_symbol(header) == symbol,
        "edge_hits": find_edge(header) + find_edge(main),
        "header": header[:300],
        "main_len": len(main),
        "main_excerpt": main[:1500],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NVDA,AAPL,SPY,COIN,PLTR,SMCI,AVGO,NFLX,UBER,XOM",
                    help="pilot list; kept small deliberately")
    ap.add_argument("--settle", type=float, default=6.0,
                    help="seconds to wait after switching before reading, so a "
                         "mid-load render is not mistaken for missing data")
    ap.add_argument("--delay", type=float, default=3.0, help="pause between symbols")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    print(f"opening {APP_URL}")
    command("navigate", {"url": APP_URL, "newTab": True, "group_title": "AO edge probe"},
            SESSION, timeout=90)
    results = []
    try:
        wait_for_app_shell(SESSION, timeout_seconds=60)
        time.sleep(3)

        # Baseline: what the default view shows before any ticker is typed.
        base = eval_js(SESSION, READ_VIEW, timeout=60) or {}
        print(f"baseline symbol: {header_symbol(base.get('header',''))}  "
              f"edge hits: {find_edge(base.get('main',''))}")

        for sym in symbols:
            r = probe_symbol(sym, args.settle)
            results.append(r)
            print(f"{sym:<6} switched={str(r['switched']):<5} "
                  f"header={r['header_symbol']!s:<6} edge={r['edge_hits'] or '-'}")
            time.sleep(args.delay)

        # TS overlay — a header button whose output was never captured.
        print("\n--- TS overlay ---")
        try:
            from capture_accessobsidian_scans import click_button_by_text
            click_button_by_text(SESSION, "TS")
            time.sleep(args.settle)
            ts = eval_js(SESSION, READ_VIEW, timeout=60) or {}
            ts_text = ts.get("main", "")
            print(ts_text[:1200])
            (OUT_DIR / f"ts_overlay_{stamp}.txt").write_text(ts_text)
            print(f"\nwrote {OUT_DIR / f'ts_overlay_{stamp}.txt'}")
        except Exception as exc:  # noqa: BLE001
            print(f"TS probe failed: {exc}")
    finally:
        close_session(SESSION)

    out = OUT_DIR / f"edge_probe_{stamp}.json"
    out.write_text(json.dumps(results, indent=2))
    switched = sum(1 for r in results if r["switched"])
    with_edge = sum(1 for r in results if r["edge_hits"])
    print(f"\nswitched {switched}/{len(results)}; showed Edge {with_edge}/{len(results)}")
    print(f"wrote {out}")
    if switched and not with_edge:
        print("\nEdge is NOT on the per-ticker view. It exists only on Flash Agentic "
              "cards, which render two at a time — so a universe sweep cannot "
              "produce more Edge observations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
