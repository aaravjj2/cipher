"""Typed tool registry for Cipher Copilot.

Every tool returns a JSON-safe dict stamped with ``source`` and ``as_of`` so the
engine can cite provenance and disclose staleness. Tools never raise to the
model: failures come back as ``{"error": ...}`` and the turn continues, which is
the same contract ask_cipher.py established for tool results.

Heavy or credential-holding modules (``app``, ``scanner``, earnings_model) are
imported lazily inside their functions so importing this module stays cheap and
unit tests can monkeypatch paths without touching the network.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

CORE_DIR = Path(__file__).resolve().parents[1]
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

# core/copilot -> core -> cipher-system -> cipher-github -> workspace root.
WORKSPACE = Path(os.environ.get("CIPHER_WORKSPACE") or CORE_DIR.parents[2])
RUNTIME_DATA = Path(os.environ.get("CIPHER_RUNTIME_DATA") or WORKSPACE / "runtime" / "data")
CAPTURE_DIR = RUNTIME_DATA / "live_option_chains"
GEX_DB = RUNTIME_DATA / "gex_history.sqlite"
OPTION_HISTORY_DB = RUNTIME_DATA / "option_history.sqlite"
BARS_DB = RUNTIME_DATA / "historical_bars.sqlite"
TRADIER_DB = RUNTIME_DATA / "tradier_stream.sqlite"

DEFAULT_FEED = os.environ.get("ALPACA_DATA_FEED") or "opra"

MAX_TOOL_RESULT_CHARS = 40_000


def env_key(name: str) -> str | None:
    """Reads one key from the process environment, cipher-system/.env, then the
    canonical runtime secret store. Two .env candidates are needed because this
    file may live at either the symlinked checkout (cipher-system/.env ->
    runtime/config/cipher.env) or the resolved real path, where only
    runtime/config/cipher.env exists. Mirrors ask_cipher's degrade-to-None
    contract."""
    candidates = [
        CORE_DIR / ".env",
        WORKSPACE / "runtime" / "config" / "cipher.env",
    ]
    for env_path in candidates:
        if not env_path.is_file():
            continue
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() == name:
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
    return os.environ.get(name) or None


def bounded_json(value: object, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Valid JSON within a provider context budget, degrading by shrinking
    lists/strings before ever emitting invalid JSON. Same ladder as ask_cipher."""
    encoded = json.dumps(value, default=str, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return encoded

    def compact(item: object, list_limit: int, string_limit: int, depth: int = 0) -> object:
        if depth >= 5:
            return "[nested data omitted]"
        if isinstance(item, dict):
            return {str(k): compact(v, list_limit, string_limit, depth + 1) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            rows = [compact(v, list_limit, string_limit, depth + 1) for v in item[:list_limit]]
            if len(item) > list_limit:
                rows.append({"omitted_items": len(item) - list_limit})
            return rows
        if isinstance(item, str) and len(item) > string_limit:
            return item[:string_limit] + "…"
        return item

    for list_limit, string_limit in ((12, 600), (6, 320), (3, 180), (1, 96)):
        candidate = json.dumps(
            {"truncated": True, "original_chars": len(encoded), "data": compact(value, list_limit, string_limit)},
            default=str,
            separators=(",", ":"),
        )
        if len(candidate) <= max_chars:
            return candidate
    return json.dumps({"truncated": True, "original_chars": len(encoded)})


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _stamp(source: str, as_of: Any, payload: dict) -> dict:
    parsed = as_of if isinstance(as_of, datetime) else _parse_iso(as_of)
    out = {"source": source, "as_of": _iso(parsed) if parsed else None}
    out.update(payload)
    return out


# --------------------------------------------------------------------------
# Local capture store
# --------------------------------------------------------------------------


def _capture_path(ticker: str) -> Path | None:
    """Newest dated capture file for one ticker, by filename date prefix."""
    symbol = str(ticker or "").strip().upper()
    if not CAPTURE_DIR.is_dir() or not symbol:
        return None
    matches = sorted(CAPTURE_DIR.glob(f"*_{symbol}.jsonl"))
    return matches[-1] if matches else None


def _last_json_line(path: Path, chunk_bytes: int = 4 * 1024 * 1024) -> dict | None:
    """Parses only the final line of a multi-megabyte JSONL capture; captures
    append chronologically, so the last line is the newest snapshot and reading
    whole files (hundreds of MB/day/ticker) would be waste."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= chunk_bytes:
            raw = handle.read()
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            return json.loads(lines[-1]) if lines else None
        tail = b""
        while size > 0:
            step = min(chunk_bytes, size)
            size -= step
            handle.seek(size)
            tail = handle.read(step) + tail
            parts = [ln for ln in tail.splitlines() if ln.strip()]
            if len(parts) >= 2:
                return json.loads(parts[-1])
        return json.loads(tail.strip()) if tail.strip() else None


def latest_capture(ticker: str) -> dict | None:
    path = _capture_path(ticker)
    if not path:
        return None
    try:
        snapshot = _last_json_line(path)
    except (OSError, ValueError):
        return None
    if not snapshot:
        return None
    snapshot["_file"] = path.name
    return snapshot


# --------------------------------------------------------------------------
# Live market data via core/app.py fetchers
# --------------------------------------------------------------------------


def get_quote(ticker: str) -> dict:
    import app

    q = app.quote(ticker)
    if not q:
        return {"error": f"no quote available for {ticker!r}"}
    return _stamp(
        "alpaca_quote",
        q.get("as_of"),
        {
            "ticker": q.get("ticker"),
            "last": q.get("last"),
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "mid": q.get("mid"),
            "price_context": q.get("price_context"),
            "day_change_pct": q.get("day_change_pct"),
            "prior_close": q.get("prior_close"),
            "feed": q.get("feed"),
        },
    )


def get_bars(ticker: str, timeframe: str = "1d", limit: int = 120) -> dict:
    import app

    allowed = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
    if timeframe not in allowed:
        return {"error": f"timeframe must be one of {sorted(allowed)}"}
    payload = app.bars(ticker.upper(), timeframe, max(1, min(int(limit), 1000)))
    rows = payload.get("bars") or payload.get("candles") or []

    def _pick(bar: dict, keys: tuple[str, ...]):
        for key in keys:
            if key in bar:
                return bar[key]
        return None

    # Long histories must survive tight provider budgets (Groq = 3.5k chars),
    # so the bulk of the series ships as compact [date, close] pairs and only
    # recent sessions carry full OHLCV.
    closes_compact = [[str(_pick(b, ("t", "time", "timestamp")))[:10], round(float(_pick(b, ("c", "close"))), 2)] for b in rows]
    recent_full = []
    for bar in rows[-10:]:
        recent_full.append(
            {
                "time": str(_pick(bar, ("t", "time", "timestamp")))[:16],
                "o": _pick(bar, ("o", "open")),
                "h": _pick(bar, ("h", "high")),
                "l": _pick(bar, ("l", "low")),
                "c": _pick(bar, ("c", "close")),
                "v": _pick(bar, ("v", "volume")),
            }
        )
    return _stamp(
        "alpaca_bars",
        payload.get("as_of") or datetime.now(timezone.utc),
        {
            "ticker": ticker.upper(),
            "timeframe": timeframe,
            "count": len(closes_compact),
            "closes": closes_compact,
            "recent_ohlc": recent_full,
        },
    )


# --------------------------------------------------------------------------
# GEX / exposure
# --------------------------------------------------------------------------


_GEX_SCALE_NOTE = (
    "net_gex per strike = call_gamma*call_oi*100*spot^2*0.01 - put_gamma*put_oi*100*spot^2*0.01 "
    "(public open-interest heuristic, NOT verified dealer positioning)"
)


def get_gex_matrix(ticker: str, expiration_count: int = 2) -> dict:
    """Live matrix from Alpaca OPRA through app.matrix; falls back to the newest
    local chain capture when the live call fails, clearly labelled by source."""
    symbol = str(ticker or "").strip().upper()
    try:
        import app

        feed = app.resolve_options_feed(DEFAULT_FEED)
        payload = app.matrix(symbol, feed, 0.06, max(1, min(int(expiration_count), 6)))
        summary = payload.get("summary") or {}
        return _stamp(
            "alpaca_matrix_live",
            payload.get("as_of") or (payload.get("quote") or {}).get("as_of"),
            {
                "ticker": symbol,
                "spot": (payload.get("quote") or {}).get("price_context"),
                "expirations": payload.get("expirations") or [],
                "summary": summary,
                "rows_count": len(payload.get("rows") or []),
                "note": _GEX_SCALE_NOTE,
            },
        )
    except Exception as exc:  # noqa: BLE001 - fallback path must stay reachable
        derived = derive_gex_from_capture(symbol)
        derived["live_error"] = f"{type(exc).__name__}: {exc}"
        return derived


def derive_gex_from_capture(ticker: str) -> dict:
    """Computes the canonical GEX/VEX strike profile from the newest local chain
    capture (which carries per-contract gamma, IV and open interest), so the
    copilot still answers when OPRA is unreachable."""
    symbol = str(ticker or "").strip().upper()
    snap = latest_capture(symbol)
    if not snap:
        return {"error": f"no local chain capture for {symbol} under {CAPTURE_DIR}"}
    quote = {}
    try:
        quote = get_quote(symbol)
    except Exception:  # noqa: BLE001 - spot fallbacks below
        quote = {}
    spot = quote.get("price_context") or quote.get("mid") or quote.get("last")
    if not spot:
        hist = latest_gamma_snapshot(symbol)
        spot = (hist or {}).get("spot")
    if not spot:
        return {"error": f"no spot available for {symbol}; cannot scale GEX"}

    cells: dict[tuple[str, float], dict] = {}
    for c in snap.get("contracts") or []:
        key = (str(c.get("expiry")), float(c.get("strike") or 0))
        cell = cells.setdefault(key, {"call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0, "call_oi": 0.0, "put_oi": 0.0})
        gamma = c.get("gamma")
        oi = c.get("open_interest")
        if gamma is None or oi is None:
            continue
        gex = float(gamma) * float(oi) * 100 * float(spot) ** 2 * 0.01
        if str(c.get("type")).lower().startswith("c"):
            cell["call_gex"] += gex
            cell["call_oi"] += float(oi)
        else:
            cell["put_gex"] -= gex
            cell["put_oi"] += float(oi)
        cell["net_gex"] = cell["call_gex"] + cell["put_gex"]

    ranked = sorted(cells.items(), key=lambda kv: kv[0][1])
    rows = [
        {"expiry": expiry, "strike": strike, **vals}
        for (expiry, strike), vals in ranked
    ]
    net_by_strike: dict[float, float] = {}
    for (expiry, strike), vals in cells.items():
        net_by_strike[strike] = net_by_strike.get(strike, 0.0) + vals["net_gex"]
    ordered = sorted(net_by_strike.items())
    cumulative = 0.0
    flip_strike = None
    prev_sign = None
    for strike, net in ordered:
        cumulative += net
        sign = 1 if cumulative >= 0 else -1
        if prev_sign is not None and sign != prev_sign:
            flip_strike = strike
        prev_sign = sign
    call_wall = max(ordered, key=lambda kv: kv[1])[0] if ordered else None
    put_wall = min(ordered, key=lambda kv: kv[1])[0] if ordered else None
    total_net = sum(net for _, net in ordered)
    return _stamp(
        "local_capture_gex",
        snap.get("timestamp"),
        {
            "ticker": symbol,
            "spot": spot,
            "capture_file": snap.get("_file"),
            "contract_count": snap.get("contract_count"),
            "cells_used": len(rows),
            "total_net_gex": round(total_net, 2),
            "gamma_flip_strike": flip_strike,
            "call_wall_strike": call_wall,
            "put_wall_strike": put_wall,
            "rows_preview": rows[:60],
            "note": _GEX_SCALE_NOTE + "; walls/flip derived from a single capture, not a dealer feed",
        },
    )


def latest_gamma_snapshot(ticker: str) -> dict | None:
    """Most recent computed snapshot row from gex_history.sqlite, if any."""
    if not GEX_DB.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{GEX_DB}?mode=ro", uri=True, timeout=2.0) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "select captured_at, spot, call_wall_strike, put_wall_strike, gamma_flip_level, day_change_pct "
                "from gex_snapshots where ticker=? order by captured_at desc limit 1",
                (str(ticker).upper(),),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def get_gamma_history(ticker: str, days: int = 5) -> dict:
    symbol = str(ticker or "").strip().upper()
    if not GEX_DB.is_file():
        return {"error": f"gex_history.sqlite not found at {GEX_DB}"}
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 30)))).isoformat()
    try:
        with sqlite3.connect(f"file:{GEX_DB}?mode=ro", uri=True, timeout=2.0) as db:
            rows = db.execute(
                "select s.captured_at, s.spot, sum(c.net_gex) as net_gex_total, "
                "max(s.gamma_flip_level) as gamma_flip_level "
                "from gex_snapshots s join gex_strike_cells c on c.snapshot_id=s.id "
                "where s.ticker=? and s.captured_at>=? group by s.id order by s.captured_at",
                (symbol, since),
            ).fetchall()
    except sqlite3.Error as exc:
        return {"error": f"gex_history query failed: {exc}"}
    series = [
        {"captured_at": r[0], "spot": r[1], "net_gex_total": round(r[2], 2) if r[2] is not None else None, "gamma_flip_level": r[3]}
        for r in rows[-400:]
    ]
    return _stamp(
        "gex_history_sqlite",
        series[-1]["captured_at"] if series else None,
        {"ticker": symbol, "points": len(series), "series": series},
    )


# --------------------------------------------------------------------------
# Vol structure / flow / scanner / research report
# --------------------------------------------------------------------------


def _iv_from_capture(snap: dict, spot: float, *, min_dte: int = 2, today=None) -> dict:
    """Per-expiry ATM IV and 25-delta skew from a chain capture (contracts carry
    iv + delta). Expiries under ``min_dte`` days are dropped: 0-1DTE contracts
    near spot carry degenerate IVs that would poison the term structure.
    Pure shape so selection rules stay testable."""
    import datetime as dt

    today = today or dt.date.today()
    by_exp: dict[str, list[dict]] = {}
    for c in snap.get("contracts") or []:
        iv = c.get("iv")
        if not iv:
            continue
        try:
            dte = (dt.date.fromisoformat(str(c.get("expiry"))[:10]) - today).days
        except ValueError:
            continue
        if dte < min_dte:
            continue
        by_exp.setdefault(str(c.get("expiry")), []).append(c)

    term = []
    for expiry in sorted(by_exp):
        contracts = by_exp[expiry]
        atm = min(contracts, key=lambda c: abs(float(c.get("strike") or 0) - spot))
        put25 = min(
            (c for c in contracts if str(c.get("type")).lower().startswith("p") and c.get("delta") is not None and -0.45 <= float(c["delta"]) <= -0.10),
            key=lambda c: abs(float(c["delta"]) + 0.25),
            default=None,
        )
        call25 = min(
            (c for c in contracts if str(c.get("type")).lower().startswith("c") and c.get("delta") is not None and 0.10 <= float(c["delta"]) <= 0.45),
            key=lambda c: abs(float(c["delta"]) - 0.25),
            default=None,
        )
        skew = None
        if put25 and call25 and put25.get("iv") and call25.get("iv"):
            skew = round(float(put25["iv"]) - float(call25["iv"]), 6)
        term.append(
            {
                "expiry": expiry,
                "atm_strike": atm.get("strike"),
                "atm_iv": round(float(atm["iv"]), 6) if atm.get("iv") else None,
                "put_25d_iv": round(float(put25["iv"]), 6) if put25 and put25.get("iv") else None,
                "call_25d_iv": round(float(call25["iv"]), 6) if call25 and call25.get("iv") else None,
                "skew_25d": skew,
                "contracts_with_iv": len(contracts),
            }
        )
    return {"expirations": term[:12]}


def get_iv_structure(ticker: str) -> dict:
    symbol = str(ticker or "").strip().upper()
    if OPTION_HISTORY_DB.is_file():
        try:
            with sqlite3.connect(f"file:{OPTION_HISTORY_DB}?mode=ro", uri=True, timeout=2.0) as db:
                db.row_factory = sqlite3.Row
                snap = db.execute(
                    "select observed_at, front_expiry, front_atm_iv, front_skew_25d, term_slope, "
                    "total_open_interest, total_volume, median_spread_pct "
                    "from snapshots where ticker=? order by observed_at desc limit 1",
                    (symbol,),
                ).fetchone()
                expiries = []
                if snap:
                    expiries = [
                        dict(r)
                        for r in db.execute(
                            "select observed_at, expiration, atm_iv, put_call_25d_skew, dte "
                            "from expiration_metrics where ticker=? and observed_at=? order by dte limit 8",
                            (symbol, snap["observed_at"]),
                        ).fetchall()
                    ]
                if snap:
                    payload = dict(snap)
                    payload["expirations"] = expiries
                    return _stamp("option_history_sqlite", payload.pop("observed_at"), payload)
        except sqlite3.Error:
            pass
    snap = latest_capture(symbol)
    if not snap:
        return {"error": f"no option history or capture for {symbol}"}
    spot = None
    try:
        q = get_quote(symbol)
        spot = q.get("price_context") or q.get("mid") or q.get("last")
    except Exception:  # noqa: BLE001 - skew needs spot only for ATM pick quality
        spot = None
    if spot:
        derived = _iv_from_capture(snap, float(spot))
        if derived["expirations"]:
            derived["spot"] = spot
            return _stamp("local_capture_derived", snap.get("timestamp"), {"ticker": symbol, **derived})
    # Legacy coarse fallback when no IV/spot data survives the capture parse.
    by_exp: dict[str, list[float]] = {}
    for c in snap.get("contracts") or []:
        iv = c.get("iv")
        if iv:
            by_exp.setdefault(str(c.get("expiry")), []).append(float(iv))
    term = sorted(
        ({"expiry": exp, "median_iv": round(sorted(vals)[len(vals) // 2], 6)} for exp, vals in by_exp.items()),
        key=lambda row: row["expiry"],
    )[:8]
    return _stamp(
        "local_capture_derived",
        snap.get("timestamp"),
        {"ticker": symbol, "expirations": term, "note": "coarse median IV per expiry; option_history.sqlite has richer 25d-skew metrics"},
    )


def get_options_flow(ticker: str, min_premium: float = 5000, limit: int = 40) -> dict:
    symbol = str(ticker or "").strip().upper()
    if not TRADIER_DB.is_file():
        return {"error": f"tradier stream store not present at {TRADIER_DB}; flow tape unavailable"}
    try:
        import tradier_flow

        payload = tradier_flow.flow(
            symbol,
            spot=None,
            min_premium=float(min_premium),
            limit=max(1, min(int(limit), 150)),
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"flow read failed: {type(exc).__name__}: {exc}"}
    if not payload:
        return {"error": f"no captured tradier session for {symbol}"}
    session = payload.get("session") or {}
    trades = payload.get("trades") or payload.get("rows") or []
    return _stamp(
        "tradier_stream_sqlite",
        session.get("date") or session.get("session_date"),
        {"ticker": symbol, "trade_count": len(trades), "sample": trades[: int(limit)], "fields": sorted(trades[0].keys()) if trades else []},
    )


def scan_setups(tickers: list[str] | None = None, mode: str = "short", strategy: str = "cipher") -> dict:
    """Runs the real Setup Scanner over at most six tickers per call; the
    scanner's per-ticker cost (OPRA paging) is what bounds the list."""
    import app
    import scanner

    names = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    if not names:
        names = ["SPY", "QQQ", "NVDA", "AAPL", "TSLA", "AMD"]
    names = names[:6]
    feed = app.resolve_options_feed(DEFAULT_FEED)
    results = []
    for symbol in names:
        try:
            read = scanner.analyze_ticker(app.matrix, symbol, feed, mode, strategy)
            results.append(
                {
                    "ticker": symbol,
                    "spot": read.get("spot"),
                    "direction": read.get("direction"),
                    "score": read.get("score"),
                    "setup_kind": read.get("setup_kind"),
                    "supports": read.get("supports"),
                    "resistances": read.get("resistances"),
                    "pull_target": read.get("pull_target"),
                    "vacuum_targets": read.get("vacuum_targets"),
                    "invalidation": read.get("invalidation"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not kill the scan
            results.append({"ticker": symbol, "error": f"{type(exc).__name__}: {exc}"})
    return _stamp(
        "cipher_scanner",
        datetime.now(timezone.utc),
        {"mode": mode, "strategy": strategy, "results": results},
    )


def get_research_report() -> dict:
    import market_research_agent

    report = market_research_agent.latest()
    if not report:
        return {"error": "no market-research report generated yet (cipher-market-research timer has not run)"}
    return _stamp("market_research_agent", report.get("generated_at"), {"report": report})


# --------------------------------------------------------------------------
# Earnings model
# --------------------------------------------------------------------------

_EM_PARENT = str(CORE_DIR.parents[1])


def _earnings_model():
    if _EM_PARENT not in sys.path:
        sys.path.insert(0, _EM_PARENT)
    from earnings_model import model as em_model

    return em_model


def get_earnings_forecast(ticker: str) -> dict:
    symbol = str(ticker or "").strip().upper()
    try:
        em = _earnings_model()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"earnings_model unavailable: {type(exc).__name__}: {exc}"}
    artifacts = em.load_trained_models()
    if not artifacts:
        return {"error": "no trained earnings models on disk; run `earnings_model train` first"}
    conn = None
    try:
        from earnings_model import db as em_db

        conn = em_db.init_db()
        prediction = em.predict_for_symbol(symbol, conn=conn)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"prediction failed: {type(exc).__name__}: {exc}"}
    finally:
        if conn is not None:
            conn.close()
    if isinstance(prediction, dict) and prediction.get("error"):
        return {"error": prediction["error"]}
    return _stamp("earnings_model_v2", datetime.now(timezone.utc), {"ticker": symbol, "prediction": prediction})


def get_earnings_radar(days: int = 21, tickers: list[str] | None = None) -> dict:
    try:
        from earnings_model import db as em_db
    except Exception as exc:  # noqa: BLE001
        return {"error": f"earnings_model unavailable: {type(exc).__name__}: {exc}"}
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=max(1, min(int(days), 60)))
    conn = None
    try:
        conn = em_db.init_db()
        events = em_db.get_all_earnings(conn, min_date=today.isoformat(), max_date=horizon.isoformat())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"radar query failed: {type(exc).__name__}: {exc}"}
    finally:
        if conn is not None:
            conn.close()
    wanted = {str(t).upper() for t in (tickers or [])}
    rows = [e for e in events if not wanted or str(e.get("symbol", "")).upper() in wanted]
    rows.sort(key=lambda e: str(e.get("earnings_date") or e.get("date") or ""))
    return _stamp(
        "earnings_sqlite",
        datetime.now(timezone.utc),
        {"window_days": int(days), "event_count": len(rows), "events": rows[:80]},
    )


# --------------------------------------------------------------------------
# Portfolio / journal / watchlists / headlines / health
# --------------------------------------------------------------------------


def get_paper_portfolio() -> dict:
    import paper_portfolio_api

    snap = paper_portfolio_api.snapshot()
    return _stamp("paper_portfolio_api", snap.get("as_of"), {"portfolio": snap})


def get_journal_entries(ticker: str | None = None, limit: int = 10) -> dict:
    import trader_journal

    payload = trader_journal.list_entries(ticker=str(ticker).upper() if ticker else None)
    entries = payload.get("entries") or []
    slim = []
    for entry in entries[: max(1, min(int(limit), 25))]:
        slim.append({k: entry.get(k) for k in ("id", "ticker", "created_at", "thesis", "status", "notes", "setup") if k in entry})
    return _stamp("trader_journal_sqlite", datetime.now(timezone.utc), {"count": len(entries), "entries": slim})


def get_watchlists() -> dict:
    import watchlists

    payload = watchlists.list_all()
    lists = payload.get("watchlists") or payload
    if isinstance(lists, dict):
        lists = [{"id": k, **v} if isinstance(v, dict) else {"id": k, "tickers": v} for k, v in lists.items()]
    return _stamp("watchlists_sqlite", datetime.now(timezone.utc), {"watchlists": lists})


def get_headlines(ticker: str, limit: int = 8) -> dict:
    from company_research_engine import yahoo_rss_headlines

    items = yahoo_rss_headlines(str(ticker).upper(), max(1, min(int(limit), 20))) or []
    return _stamp(
        "yahoo_rss",
        datetime.now(timezone.utc),
        {"ticker": str(ticker).upper(), "headlines": items},
    )


def _earnings_db_last_events(symbol: str, limit: int = 4) -> list[dict]:
    try:
        from earnings_model import db as em_db

        conn = em_db.init_db()
        try:
            rows = em_db.get_earnings_for_symbol(conn, symbol)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - context only, never fatal
        return []
    dated = sorted(rows or [], key=lambda r: str(r.get("earnings_date")))
    return [{"date": str(r.get("earnings_date"))[:10], "eps_estimate": r.get("eps_estimate"), "eps_actual": r.get("eps_actual")} for r in dated[-int(limit):]]


def get_earnings_dates(ticker: str) -> dict:
    """Upcoming earnings straight from yfinance's live calendar - the local
    collector stores only *reported* events, so radar-style questions about the
    future need this. Falls back to last reported events for context either way."""
    symbol = str(ticker or "").strip().upper()
    upcoming: list[dict] = []
    est: dict = {}
    try:
        import yfinance

        cal = yfinance.Ticker(symbol).calendar or {}
        dates = cal.get("Earnings Date") or []
        if isinstance(dates, (list, tuple)):
            upcoming = [{"date": str(d)} for d in dates]
        elif dates:
            upcoming = [{"date": str(dates)}]
        est = {
            "eps_avg": cal.get("Earnings Average"),
            "revenue_avg": cal.get("Revenue Average"),
            "dividend_date": str(cal.get("Dividend Date")) if cal.get("Dividend Date") else None,
            "ex_dividend_date": str(cal.get("Ex-Dividend Date")) if cal.get("Ex-Dividend Date") else None,
        }
    except Exception as exc:  # noqa: BLE001 - degrade to history-only
        est["error"] = f"{type(exc).__name__}: {exc}"
    return _stamp(
        "yfinance_calendar",
        datetime.now(timezone.utc),
        {
            "ticker": symbol,
            "upcoming_earnings": upcoming,
            "estimates": est,
            "last_reported": _earnings_db_last_events(symbol),
            "note": "upcoming date is provider-scheduled and can shift; verify near the date",
        },
    )


# --------------------------------------------------------------------------
# Derived analytics: technicals / exposure term structure / strategy compare
# --------------------------------------------------------------------------


def _technicals_from_closes(closes: list[float]) -> dict:
    """Pure trend math over daily closes; kept side-effect-free for tests."""
    if len(closes) < 20:
        return {"error": f"need >=20 closes, got {len(closes)}"}
    last = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    n50 = min(50, len(closes))
    ma50 = sum(closes[-n50:]) / n50
    hi, lo = max(closes), min(closes)
    ret = lambda n: round((last / closes[-min(n, len(closes))] - 1) * 100, 2) if len(closes) > n else None  # noqa: E731
    return {
        "last": round(last, 2),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
        "above_ma20": last >= ma20,
        "above_ma50": last >= ma50,
        "off_high_pct": round((last / hi - 1) * 100, 2),
        "off_low_pct": round((last / lo - 1) * 100, 2),
        "return_5d_pct": ret(6),
        "return_20d_pct": ret(21),
        "trend": "up" if last >= ma20 >= ma50 else ("down" if last < ma20 <= ma50 else "mixed"),
    }


def get_technicals(ticker: str) -> dict:
    d = get_bars(ticker, timeframe="1d", limit=120)
    if d.get("error"):
        return d
    closes = [row[1] for row in d.get("closes") or []]
    out = _technicals_from_closes(closes)
    if out.get("error"):
        return out
    return _stamp("alpaca_bars_derived", d.get("as_of"), {"ticker": ticker.upper(), **out})


def _bucket_exposure(rows: list[dict], today=None) -> dict:
    """Aggregates matrix strike-rows into DTE buckets with honest OI coverage.
    Pure function so bucketing rules are testable without network."""
    import datetime as dt

    today = today or dt.date.today()
    buckets = {
        "0-14d": {"lo": 0, "hi": 14},
        "15-45d": {"lo": 15, "hi": 45},
        "46-90d": {"lo": 46, "hi": 90},
    }
    agg = {k: {"gex": 0.0, "vex": 0.0, "call_oi": 0.0, "put_oi": 0.0, "covered": 0, "cells": 0} for k in buckets}
    oi_by_strike: dict[float, list[float]] = {}

    def num(v):
        return float(v) if v is not None else 0.0

    for row in rows:
        strike = row.get("strike")
        for cell in row.get("cells") or []:
            try:
                dte = (dt.date.fromisoformat(str(cell.get("expiration"))[:10]) - today).days
            except ValueError:
                continue
            for name, cfg in buckets.items():
                if cfg["lo"] <= dte <= cfg["hi"]:
                    a = agg[name]
                    a["cells"] += 1
                    net_gex = cell.get("net_gex")
                    net_vex = cell.get("net_vex")
                    a["gex"] += num(net_gex) if net_gex is not None else num(cell.get("call_gex")) + num(cell.get("put_gex"))
                    a["vex"] += num(net_vex) if net_vex is not None else num(cell.get("call_vex")) + num(cell.get("put_vex"))
                    coi, poi = cell.get("call_oi"), cell.get("put_oi")
                    if coi is not None or poi is not None:
                        a["covered"] += 1
                        a["call_oi"] += num(coi)
                        a["put_oi"] += num(poi)
                        if dte <= 45:
                            e = oi_by_strike.setdefault(float(strike), [0.0, 0.0])
                            e[0] += num(coi)
                            e[1] += num(poi)

    def shape(a: dict) -> dict:
        pc_oi = round(a["put_oi"] / a["call_oi"], 2) if a["call_oi"] else None
        return {
            "net_gex_musd": round(a["gex"] / 1e6, 2),
            "net_vex_musd": round(a["vex"] / 1e6, 2),
            "call_oi": int(a["call_oi"]),
            "put_oi": int(a["put_oi"]),
            "put_call_oi_ratio": pc_oi,
            "oi_coverage_pct": round(100 * a["covered"] / a["cells"]) if a["cells"] else 0,
        }

    top_oi = sorted(
        ((strike, int(v[0]), int(v[1])) for strike, v in oi_by_strike.items()),
        key=lambda t: -(t[1] + t[2]),
    )[:6]
    return {name: shape(a) for name, a in agg.items()} | {"top_oi_strikes_le45d": [{"strike": s, "call_oi": c, "put_oi": p} for s, c, p in top_oi]}


def get_exposure_term_structure(ticker: str, expiration_count: int = 12) -> dict:
    """Long-dated GEX/VEX/OI structure by DTE bucket - the 'months out' view
    that near-expiry walls alone cannot answer."""
    symbol = str(ticker or "").strip().upper()
    try:
        import app

        feed = app.resolve_options_feed(DEFAULT_FEED)
        payload = app.matrix(symbol, feed, 0.15, max(1, min(int(expiration_count), 36)))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    spot = (payload.get("quote") or {}).get("price_context")
    rows = payload.get("rows") or []
    if not rows:
        return {"error": f"no matrix rows returned for {symbol}"}
    summary = payload.get("summary") or {}
    return _stamp(
        "alpaca_matrix_live_term",
        (payload.get("quote") or {}).get("as_of"),
        {
            "ticker": symbol,
            "spot": spot,
            "near_walls": {k: summary.get(k) for k in ("call_wall_strike", "put_wall_strike", "gamma_flip_level", "gamma_flip_candidates")},
            "buckets": _bucket_exposure(rows),
            "note": _GEX_SCALE_NOTE,
        },
    )


def scan_strategies(ticker: str, strategies: str = "cipher,flash,cluster") -> dict:
    """One ticker through every scanner strategy for a consensus view."""
    import app
    import scanner

    symbol = str(ticker or "").strip().upper()
    feed = app.resolve_options_feed(DEFAULT_FEED)
    wanted = [s.strip() for s in str(strategies).split(",") if s.strip()]
    results = []
    for strat in wanted:
        try:
            read = scanner.analyze_ticker(app.matrix, symbol, feed, "short", strat)
            results.append(
                {
                    "strategy": strat,
                    "direction": read.get("direction"),
                    "score": read.get("score"),
                    "setup_kind": read.get("setup_kind"),
                    "supports": read.get("supports"),
                    "resistances": read.get("resistances"),
                    "invalidation": read.get("invalidation"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one strategy failing must not kill the rest
            results.append({"strategy": strat, "error": f"{type(exc).__name__}: {exc}"})
    directions = [r.get("direction") for r in results if r.get("direction")]
    consensus = (
        f"{max(set(directions), key=directions.count)} ({directions.count(max(set(directions), key=directions.count))}/{len(directions)})"
        if directions
        else None
    )
    return _stamp(
        "cipher_scanner_multi",
        datetime.now(timezone.utc),
        {"ticker": symbol, "consensus": consensus, "results": results},
    )


def get_backtest_evidence(ticker: str | None = None) -> dict:
    """Aggregates Cipher's own completed research artifacts - the walkforward
    verdicts and scan-cluster backtests that already ran offline. Chat must
    never re-run backtests (minutes each); it reads what the studies recorded,
    including their REJECTED verdicts."""
    runtime = RUNTIME_DATA
    out: list[dict] = []

    wf_path = runtime / "eod_option_walkforward" / "report.json"
    try:
        wf = json.loads(wf_path.read_text())
        aggregates = wf.get("aggregate_results") or []
        for agg in [a for a in aggregates if isinstance(a, dict)][:4]:
            out.append(
                {
                    "study": "eod_option_walkforward",
                    "as_of": wf.get("generated_at"),
                    "execution_model": agg.get("execution_model"),
                    "policy": agg.get("policy"),
                    "months": agg.get("months"),
                    "trades": agg.get("trades"),
                    "win_rate_pct": agg.get("win_rate_pct"),
                    "total_pnl_usd": agg.get("total_pnl_dollars"),
                    "profit_factor": agg.get("profit_factor"),
                }
            )
    except (OSError, ValueError):
        pass

    obs_path = runtime / "backtests" / "obsidian_pine_ytd_2026.json"
    try:
        obs = json.loads(obs_path.read_text())
        if isinstance(obs, dict):
            aggregate = obs.get("aggregate")
            if not isinstance(aggregate, dict):
                aggregate = {}
            out.append(
                {
                    "study": "obsidian_eod_pine_ytd",
                    "as_of": obs.get("generated_at"),
                    "period": obs.get("period"),
                    "symbols": obs.get("symbols_returned"),
                    "summary": {k: aggregate[k] for k in list(aggregate)[:8]},
                    "caveats": (obs.get("caveats") or [])[:3],
                }
            )
    except (OSError, ValueError):
        pass

    results_dir = runtime / "backtest_results"
    if results_dir.is_dir():
        wanted = str(ticker or "").upper()
        for path in sorted(results_dir.glob("backtest_*.json"), reverse=True):
            stem = path.stem.split("_", 1)[1]
            if wanted and not stem.startswith(wanted):
                continue
            try:
                bt = json.loads(path.read_text())
                if not isinstance(bt, dict):
                    continue
                predictions = bt.get("predictions") or []
                if not isinstance(predictions, list):
                    predictions = []
                hits = [p for p in predictions if isinstance(p, dict) and (p.get("outcome_hit") or p.get("hit"))]
                out.append(
                    {
                        "study": "cluster_scan_backtest",
                        "ticker": bt.get("ticker"),
                        "as_of": path.stat().st_mtime,
                        "snapshots": bt.get("total_snapshots"),
                        "clusters": bt.get("clusters_detected"),
                        "predictions": len(predictions),
                        "hits": len(hits),
                    }
                )
            except (OSError, ValueError):
                continue
            if wanted:
                break

    earnings_md = runtime / "earnings_gap_magnitude_walkforward" / "report.md"
    try:
        head = earnings_md.read_text()[:600]
        for token in ("Verdict:", "Status:"):
            if token in head:
                segment = head.split(token, 1)[1].split("\n")[0].strip(" *|")
                out.append({"study": "earnings_gap_magnitude_walkforward", token.rstrip(":").lower(): segment})
                break
    except OSError:
        pass

    if not out:
        return {"error": f"no research evidence found under {runtime}"}
    return _stamp(
        "local_research_artifacts",
        datetime.now(timezone.utc),
        {"studies": len(out), "evidence": out, "note": "these are completed study records; chat cannot launch new backtests"},
    )


def get_market_health() -> dict:
    """Freshness of every store the copilot reads, so answers can disclose age
    instead of manufacturing precision."""
    now = datetime.now(timezone.utc)

    def age(ts: str | None) -> float | None:
        parsed = _parse_iso(ts)
        return round((now - parsed).total_seconds() / 60, 1) if parsed else None

    stores: list[dict] = []
    symbols = sorted({p.name.rsplit("_", 1)[-1].replace(".jsonl", "") for p in CAPTURE_DIR.glob("*_*.jsonl")}) if CAPTURE_DIR.is_dir() else []
    for symbol in symbols[:14]:
        snap = latest_capture(symbol)
        ts = snap.get("timestamp") if snap else None
        stores.append({"store": f"chain_capture:{symbol}", "as_of": ts, "age_minutes": age(ts)})
    gamma = latest_gamma_snapshot("SPY") or {}
    stores.append({"store": "gex_history", "as_of": gamma.get("captured_at"), "age_minutes": age(gamma.get("captured_at"))})
    stores.append({"store": "tradier_stream", "path_exists": TRADIER_DB.is_file()})
    if BARS_DB.is_file():
        try:
            with sqlite3.connect(f"file:{BARS_DB}?mode=ro", uri=True, timeout=2.0) as db:
                row = db.execute("select max(timestamp) from historical_bars").fetchone()
            stores.append({"store": "historical_bars", "as_of": row[0] if row else None})
        except sqlite3.Error:
            stores.append({"store": "historical_bars", "error": "unreadable"})
    providers_ready = {name: bool(env_key(var)) for name, var in (("groq", "GROQ_API_KEY"), ("openrouter", "OPENROUTER_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"))}
    return _stamp(
        "local_stores",
        now,
        {"stores": stores, "llm_providers_configured": providers_ready},
    )


# --------------------------------------------------------------------------
# Paper account (read-only; see core/copilot/account.py for the boundary)
# --------------------------------------------------------------------------


def get_account() -> dict:
    from core.copilot import account

    return account.get_account()


def get_positions() -> dict:
    from core.copilot import account

    return account.get_positions()


def get_orders(status: str = "all", limit: int = 25) -> dict:
    from core.copilot import account

    return account.get_orders(status=status, limit=limit)


def get_trade_history(days: int = 7) -> dict:
    from core.copilot import account

    return account.get_trade_history(days=days)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

TOOL_IMPLS: dict[str, Callable[..., dict]] = {
    "get_quote": get_quote,
    "get_bars": get_bars,
    "get_gex_matrix": get_gex_matrix,
    "get_gamma_history": get_gamma_history,
    "get_iv_structure": get_iv_structure,
    "get_options_flow": get_options_flow,
    "scan_setups": scan_setups,
    "get_research_report": get_research_report,
    "get_earnings_forecast": get_earnings_forecast,
    "get_earnings_radar": get_earnings_radar,
    "get_earnings_dates": get_earnings_dates,
    "get_backtest_evidence": get_backtest_evidence,
    "get_paper_portfolio": get_paper_portfolio,
    "get_journal_entries": get_journal_entries,
    "get_watchlists": get_watchlists,
    "get_headlines": get_headlines,
    "get_market_health": get_market_health,
    "get_account": get_account,
    "get_positions": get_positions,
    "get_orders": get_orders,
    "get_trade_history": get_trade_history,
    "get_technicals": get_technicals,
    "get_exposure_term_structure": get_exposure_term_structure,
    "scan_strategies": scan_strategies,
}

TOOL_SPECS: dict[str, dict] = {
    "get_quote": {
        "description": "Live delayed quote for one ticker: last, bid, ask, mid, day change percent, prior close.",
        "params": {"ticker": {"type": "string", "required": True}},
    },
    "get_bars": {
        "description": "Recent OHLCV bars for one ticker. timeframes: 1m,5m,15m,1h,4h,1d,1w.",
        "params": {"ticker": {"type": "string", "required": True}, "timeframe": {"type": "string"}, "limit": {"type": "integer"}},
    },
    "get_gex_matrix": {
        "description": "Gamma exposure (GEX/VEX) strike surface for one ticker with walls and gamma-flip level. Falls back to newest local chain capture when live OPRA fails.",
        "params": {"ticker": {"type": "string", "required": True}, "expiration_count": {"type": "integer"}},
    },
    "get_gamma_history": {
        "description": "Time series of total net GEX, spot and gamma-flip level for one ticker over recent days from gex_history.sqlite.",
        "params": {"ticker": {"type": "string", "required": True}, "days": {"type": "integer"}},
    },
    "get_iv_structure": {
        "description": "Options volatility term structure for one ticker: ATM IV per expiration, 25-delta put skew, term slope.",
        "params": {"ticker": {"type": "string", "required": True}},
    },
    "get_options_flow": {
        "description": "Captured options flow tape for one ticker from the Tradier stream store, classified by aggressor side.",
        "params": {"ticker": {"type": "string", "required": True}, "min_premium": {"type": "number"}, "limit": {"type": "integer"}},
    },
    "scan_setups": {
        "description": "Run Cipher's setup scanner (direction, score, supports/resistances, targets) over up to six tickers; defaults to SPY,QQQ,NVDA,AAPL,TSLA,AMD.",
        "params": {"tickers": {"type": "array", "items": {"type": "string"}}, "mode": {"type": "string"}, "strategy": {"type": "string"}},
    },
    "get_research_report": {
        "description": "Latest scheduled market-research report ranking research candidates across index/large-cap/semiconductor/tactical universes.",
        "params": {},
    },
    "get_earnings_forecast": {
        "description": "Trained ML earnings-move forecast for one ticker: direction probability, gap magnitude, suggested option strategy shape.",
        "params": {"ticker": {"type": "string", "required": True}},
    },
    "get_earnings_radar": {
        "description": "Upcoming earnings events within N days across the tracked universe, optionally filtered to tickers.",
        "params": {"days": {"type": "integer"}, "tickers": {"type": "array", "items": {"type": "string"}}},
    },
    "get_earnings_dates": {
        "description": "NEXT earnings date for one ticker from the live provider calendar (the local store only has reported events), plus EPS/revenue estimates and last reported results.",
        "params": {"ticker": {"type": "string", "required": True}},
    },
    "get_backtest_evidence": {
        "description": "Cipher's own completed research records: EOD walkforward aggregates, Obsidian EOD YTD study, per-ticker cluster scan backtests, earnings-gap verdicts. Read-only; cannot launch new backtests.",
        "params": {"ticker": {"type": "string"}},
    },
    "get_paper_portfolio": {
        "description": "Simulated paper portfolio positions, marks and risk snapshot (never connected to a real brokerage).",
        "params": {},
    },
    "get_journal_entries": {
        "description": "Recent trader-journal entries, optionally filtered to one ticker.",
        "params": {"ticker": {"type": "string"}, "limit": {"type": "integer"}},
    },
    "get_watchlists": {
        "description": "All watchlists and their member tickers.",
        "params": {},
    },
    "get_headlines": {
        "description": "Recent public news headlines for one ticker from a public RSS feed.",
        "params": {"ticker": {"type": "string", "required": True}, "limit": {"type": "integer"}},
    },
    "get_market_health": {
        "description": "Freshness of every local data store plus which LLM provider keys are configured. Call before trusting any other data.",
        "params": {},
    },
    "get_account": {
        "description": "The user's Alpaca PAPER account status: equity, cash, blocks, day-trade count. Read-only; never places orders.",
        "params": {},
    },
    "get_positions": {
        "description": "Open paper-account positions with entry price, current mark, and unrealized P&L (stocks and option contracts).",
        "params": {},
    },
    "get_orders": {
        "description": "Recent paper-account orders (default all statuses, newest first) incl. limit prices and fills.",
        "params": {"status": {"type": "string"}, "limit": {"type": "integer"}},
    },
    "get_trade_history": {
        "description": "FIFO round-trip realized P&L from recent filled paper trades: per-trade entry/exit/PnL, win rate, open remainders.",
        "params": {"days": {"type": "integer"}},
    },
    "get_technicals": {
        "description": "Trend frame for one ticker from daily closes: MA20/MA50 position, distance from highs/lows, 5d/20d returns, trend label.",
        "params": {"ticker": {"type": "string", "required": True}},
    },
    "get_exposure_term_structure": {
        "description": "Long-dated exposure view: GEX/VEX/OI by DTE bucket (0-14d, 15-45d, 46-90d), put/call OI ratios, top open-interest strikes - for 'months out' questions.",
        "params": {"ticker": {"type": "string", "required": True}, "expiration_count": {"type": "integer"}},
    },
    "scan_strategies": {
        "description": "Run cipher+flash+cluster scanner strategies over ONE ticker with a consensus verdict - use instead of three scan_setups calls.",
        "params": {"ticker": {"type": "string", "required": True}, "strategies": {"type": "string"}},
    },
}


def to_openai_specs() -> list[dict]:
    """OpenAI function-calling schema for every registered tool."""
    specs = []
    for name, impl in TOOL_IMPLS.items():
        meta = TOOL_SPECS[name]
        properties = {}
        required = []
        for param, schema in meta["params"].items():
            properties[param] = {k: v for k, v in schema.items() if k != "required"}
            if schema.get("required"):
                required.append(param)
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta["description"],
                    "parameters": {"type": "object", "properties": properties, "required": required},
                },
            }
        )
    return specs


def dispatch(name: str, args: dict) -> dict:
    """One tool call, never raising: errors are data the model can reason about."""
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"error": f"unknown tool {name!r}; valid tools: {sorted(TOOL_IMPLS)}"}
    try:
        return impl(**(args or {}))
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
        return {"error": f"{type(exc).__name__}: {exc}"}
