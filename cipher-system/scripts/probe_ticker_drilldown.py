#!/usr/bin/env python3
"""Read-only probe: can we navigate the real app to an arbitrary ticker?

The captured corpus is stuck at 9 distinct tickers because
capture_accessobsidian_scans.py can only read whichever names the real scanner
happened to surface. Deriving the Edge score needs many more observations, so the
question this answers is whether the app exposes a way to pull up a ticker we
choose rather than one it chose.

Strictly read-only: opens the app, describes the DOM around the ticker input, and
prints what it finds. Clicks nothing, submits nothing, places no orders.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_accessobsidian_scans import (  # noqa: E402
    APP_URL, close_session, command, eval_js, wait_for_app_shell,
)

SESSION = "ao-probe-drilldown"

DESCRIBE = r"""(() => {
  const out = {url: location.href, hash: location.hash};
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'),
    placeholder: el.getAttribute('placeholder'),
    id: el.id || null,
    name: el.getAttribute('name'),
    aria: el.getAttribute('aria-label'),
    cls: (el.className || '').toString().slice(0, 120),
  });
  out.inputs = [...document.querySelectorAll('input')].map(desc);
  out.buttonsSample = [...document.querySelectorAll('button')]
    .map(b => (b.innerText || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean).slice(0, 40);
  // Anything that looks like it holds the active symbol.
  out.symbolish = [...document.querySelectorAll('[class*="ticker"],[class*="symbol"],[id*="ticker"],[id*="symbol"]')]
    .slice(0, 10).map(el => ({...desc(el), text: (el.innerText||'').slice(0,60)}));
  return out;
})()"""


def main():
    print(f"opening {APP_URL}")
    command("navigate", {"url": APP_URL, "newTab": True, "group_title": "AO probe"},
            SESSION, timeout=90)
    try:
        wait_for_app_shell(SESSION, timeout_seconds=60)
        time.sleep(2)
        info = eval_js(SESSION, DESCRIBE, timeout=60)
        print(json.dumps(info, indent=2)[:4000])

        # Does the app accept a ticker in the URL? If it does, drill-in is trivial
        # and needs no typing into a search box at all.
        for candidate in ("#CI?ticker=NVDA", "#CI/NVDA", "#NVDA"):
            url = APP_URL.split("#")[0] + candidate
            print(f"\n--- trying {url}")
            command("navigate", {"url": url}, SESSION, timeout=60)
            time.sleep(3)
            state = eval_js(SESSION, "(() => ({hash: location.hash, "
                                     "head: (document.querySelector('header')?.innerText||'')"
                                     ".replace(/\\s+/g,' ').slice(0,200)}))()", timeout=30)
            print(json.dumps(state, indent=2))
    finally:
        close_session(SESSION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
