#!/usr/bin/env python3
"""Nightly Cipher Copilot digest -> Discord webhook.

Deterministic render (no LLM, no cost): account state, open/last trades,
regime map for tracked tickers, data-store freshness - PLUS a stateful diff
against the previous run so regime crossings (gamma-flip reclaim/loss, trend
flips) surface as alerts instead of requiring the reader to notice.

Tracked tickers come from your first non-empty cipher watchlist (falling back
to a default set), so managing the watchlist manages the digest.

Pushed through the existing DISCORD_WEBHOOK_URL; silently skips when absent.

Manual:  /home/aarav/.venvs/cipher/bin/python cipher-system/scripts/copilot_daily_digest.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE_DIR))

from core.copilot import tools  # noqa: E402
from core.copilot.tools import env_key  # noqa: E402
from core.exchange_calendar import is_session  # noqa: E402
from core.product_status import market_session, NY  # noqa: E402

DEFAULT_TRACKED = ["AMKR", "RMBS", "COST", "NVDA", "SPY"]
MAX_TRACKED = 8
DISCORD_LIMIT = 1900
STATE_PATH = Path(env_key("CIPHER_COPILOT_DIGEST_STATE") or (CORE_DIR / "data" / "copilot" / "digest_state.json"))


def _fmt(x) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.2f}"
    return str(x)


def tracked_tickers() -> list[str]:
    """First non-empty watchlist drives coverage; defaults when none exist."""
    try:
        payload = tools.dispatch("get_watchlists", {})
        for wl in payload.get("watchlists") or []:
            members = wl.get("tickers") or wl.get("members") or []
            names = [str(m.get("symbol") if isinstance(m, dict) else m).upper() for m in members]
            names = [n for n in names if n][:MAX_TRACKED]
            if len(names) >= 2:
                return names
    except Exception:  # noqa: BLE001 - watchlists are a preference, not a dependency
        pass
    return DEFAULT_TRACKED


def regime_rows(tickers: list[str]) -> list[dict]:
    rows = []
    for ticker in tickers:
        term = tools.dispatch("get_exposure_term_structure", {"ticker": ticker})
        tech = tools.dispatch("get_technicals", {"ticker": ticker})
        if term.get("error"):
            rows.append({"ticker": ticker, "error": str(term["error"])[:60]})
            continue
        near = (term.get("buckets") or {}).get("0-14d") or {}
        spot = term.get("spot")
        flip = (term.get("near_walls") or {}).get("gamma_flip_level")
        rows.append(
            {
                "ticker": ticker,
                "spot": spot,
                "flip": flip,
                "regime": (
                    ("positive" if spot >= flip else "negative")
                    if all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (spot, flip))
                    else "unknown"
                ),
                "call_wall": (term.get("near_walls") or {}).get("call_wall_strike"),
                "put_wall": (term.get("near_walls") or {}).get("put_wall_strike"),
                "trend": None if tech.get("error") else tech.get("trend"),
                "pc_oi": near.get("put_call_oi_ratio"),
                "as_of": term.get("as_of"),
                "trend_as_of": tech.get("as_of"),
            }
        )
    return rows


def _observed(value) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def _fresh_change(prev: dict, cur: dict, key: str, now: datetime) -> bool:
    before, after = _observed(prev.get(key)), _observed(cur.get(key))
    return bool(before and after and before < after
                and before.astimezone(NY).date() == now.astimezone(NY).date()
                and 0 <= (now - after).total_seconds() <= 300)


def _crossed(prev: dict | None, cur: dict, now: datetime) -> str | None:
    """One human sentence per genuine state change; None when unchanged."""
    if not prev:
        return None
    changes = []
    if (prev.get("regime") in ("positive", "negative") and cur.get("regime") in ("positive", "negative")
            and prev["regime"] != cur["regime"] and _fresh_change(prev, cur, "as_of", now)):
        direction = "reclaimed" if cur["regime"] == "positive" else "lost"
        changes.append(f"{direction} gamma-flip {_fmt(cur.get('flip'))}")
    if (prev.get("trend") and cur.get("trend") and prev["trend"] != cur["trend"]
            and _fresh_change(prev, cur, "trend_as_of", now)):
        changes.append(f"trend {prev['trend']} -> {cur['trend']}")
    return "; ".join(changes) if changes else None


def diff_regimes(prev_rows: list[dict] | None, cur_rows: list[dict], now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if not market_session(now)["is_regular"]:
        return []
    by_prev = {r.get("ticker"): r for r in (prev_rows or [])}
    alerts = []
    for row in cur_rows:
        change = _crossed(by_prev.get(row.get("ticker")), row, now)
        if change:
            alerts.append(f"- ⚠️ `{row['ticker']}` {change} (spot {_fmt(row.get('spot'))})")
    return alerts


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(rows: list[dict]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps({"saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows}, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"state save failed: {exc}", file=sys.stderr)


def build_digest() -> tuple[list[str], list[dict]]:
    tickers = tracked_tickers()
    lines: list[str] = [f"**Cipher Daily Digest** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%MZ')}", ""]
    local = tools.dispatch("get_paper_portfolio", {})
    if local.get("error"):
        lines.append(f"- local portfolios: unavailable ({str(local['error'])[:80]})")
    else:
        portfolio = local.get("portfolio") or {}
        rows = portfolio.get("portfolios") or []
        lines.append(f"**Cipher local paper portfolios** — marked equity ${_fmt(portfolio.get('combined_marked_equity'))}")
        lines.append(f"Daily realized P&L ${_fmt(portfolio.get('daily_realized_pnl'))} | open positions {sum(r.get('open_positions', 0) for r in rows)}")
        lines.append(f"Ledger as of: {portfolio.get('as_of') or 'unavailable'} · simulated only; no broker orders.")

    cur_rows = regime_rows(tickers)
    alerts = diff_regimes(load_state().get("rows"), cur_rows)
    lines.append("")
    lines.append("**Regime map** (spot vs gamma-flip; OI heuristic)")
    for row in cur_rows:
        if row.get("error"):
            lines.append(f"- `{row['ticker']}`: unavailable")
            continue
        lines.append(
            f"- `{row['ticker']}` ${_fmt(row['spot'])} | {row['regime']}-gamma (flip {_fmt(row['flip'])}) "
            f"| walls {_fmt(row['call_wall'])}/{_fmt(row['put_wall'])} | trend: {_fmt(row['trend'])} | P/C {_fmt(row['pc_oi'])}"
            f" | quote as of {row.get('as_of') or 'unknown'}"
        )
    if alerts:
        lines.insert(len(lines) - len(cur_rows), "**Changes since last run**")
        lines.extend(alerts)

    health = tools.dispatch("get_market_health", {})
    stores = health.get("stores") or []
    ages = [s.get("age_minutes") for s in stores if s.get("age_minutes") is not None]
    if ages:
        lines.append("")
        lines.append(f"_Store writes {min(ages):.0f}–{max(ages):.0f}m ago across {len(stores)} stores; not quote freshness._")

    chunks, current = [], ""
    for para in lines:
        if len(current) + len(para) + 1 > DISCORD_LIMIT:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n{para}".strip("\n")
    if current:
        chunks.append(current)
    return chunks, cur_rows


def push(chunks: list[str]) -> int:
    url = env_key("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL not set; digest skipped.", file=sys.stderr)
        return 1
    sent = 0
    for chunk in chunks:
        body = json.dumps({"content": chunk}).encode()
        # Discord 403s the default Python-urllib User-Agent; every existing
        # cipher sender sets a named one (see send_portfolio_daily_discord.py).
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Cipher-Copilot-Digest/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            sent += 1 if resp.status in (200, 204) else 0
    print(f"digest pushed: {sent}/{len(chunks)} chunks")
    return 0 if sent == len(chunks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    parser.add_argument("--no-save", action="store_true", help="do not update the diff baseline")
    parser.add_argument("--alerts-only", action="store_true", help="push ONLY when a regime/trend crossing occurred; silent otherwise (intraday mode)")
    parser.add_argument("--enable-legacy-notifications", action="store_true", help="Explicitly opt back into the retired regime/daily sender")
    opts = parser.parse_args(argv)
    if not opts.dry_run and not opts.enable_legacy_notifications:
        print('retired legacy notification job; no fetch or Discord send')
        return 0
    now = datetime.now(timezone.utc)
    if not is_session(now.astimezone(NY).date()) or (opts.alerts_only and not market_session(now)["is_regular"]):
        print("market closed; digest skipped")
        return 0
    chunks, rows = build_digest()

    if opts.alerts_only:
        prev = load_state().get("rows")
        alerts = diff_regimes(prev, rows)
        if not alerts:
            if not opts.dry_run and not opts.no_save:
                save_state(rows)
            print("no regime changes; nothing pushed")
            return 0
        text = (
            "**Cipher intraday regime alert** — "
            + datetime.now(timezone.utc).strftime("%H:%MZ")
            + "\n"
            + "\n".join(alerts)
        )
        # One merged message, hard-sliced only if it somehow exceeds limits -
        # never one Discord message per line.
        chunks = [text[i : i + DISCORD_LIMIT] for i in range(0, len(text), DISCORD_LIMIT)]

    if opts.dry_run:
        print(("\n" + "-" * 40 + "\n").join(chunks))
        return 0
    code = push(chunks)
    if code == 0 and not opts.no_save:
        save_state(rows)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
