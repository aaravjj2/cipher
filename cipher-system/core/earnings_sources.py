"""Free, best-effort upcoming earnings adapters with explicit uncertainty."""
from __future__ import annotations

from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable


def _iso_day(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _yahoo_one(symbol: str) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is not installed; install requirements.txt") from exc
    calendar = yf.Ticker(symbol).get_calendar()
    if hasattr(calendar, "to_dict"):
        calendar = calendar.to_dict()
    calendar = calendar or {}
    raw_dates = calendar.get("Earnings Date") or calendar.get("EarningsDate") or []
    if not isinstance(raw_dates, (list, tuple)):
        raw_dates = [raw_dates]
    rows = []
    for raw in raw_dates:
        day = _iso_day(raw)
        if day:
            rows.append({"symbol": symbol, "scheduled_date": day, "timing": "UNKNOWN",
                         "status": "ESTIMATED", "provider": "yahoo_finance_via_yfinance",
                         "provider_event_id": f"yahoo:{symbol}:{day}"})
    return rows


def yahoo_events(symbols: Iterable[str], *, fetch_one: Callable[[str], list[dict]] | None = None) -> tuple[list[dict], list[dict]]:
    events, errors = [], []
    stocks = [str(symbol).upper() for symbol in symbols if str(symbol).upper() not in {"SPY", "QQQ", "IWM"}]
    def fetch(symbol: str):
        return symbol, (fetch_one or _yahoo_one)(symbol)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in stocks}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, rows = future.result()
                events.extend(rows)
            except Exception as exc:
                errors.append({"provider": "yahoo_finance_via_yfinance", "symbol": symbol,
                               "error": f"{type(exc).__name__}: {exc}"})
    events.sort(key=lambda row: (row.get("scheduled_date") or "", row.get("symbol") or ""))
    return events, errors


def _finviz_bulk(symbols: Iterable[str]) -> list[dict]:
    try:
        from finvizfinance.screener.financial import Financial
    except ImportError as exc:
        raise RuntimeError("finvizfinance is not installed; install requirements.txt") from exc
    # Constrain the request to Cipher's universe. Earnings("This Month") walks
    # every Finviz result page and can take minutes even for a 26-symbol job.
    screen = Financial()
    screen.set_filter(filters_dict={"Earnings Date": "This Month"}, ticker=",".join(symbols))
    frame = screen.screener_view(order="Earnings Date", limit=200, verbose=0, sleep_sec=2)
    return [] if frame is None else [dict(row) for row in frame.to_dict(orient="records")]


def finviz_events(symbols: Iterable[str], *, fetch_fn: Callable[[], list[dict]] | None = None) -> tuple[list[dict], list[dict]]:
    wanted = {str(symbol).upper() for symbol in symbols}
    try:
        raw_rows = fetch_fn() if fetch_fn else _finviz_bulk(wanted)
        from .finviz_discovery import normalize_ticker_rows
        raw_rows = normalize_ticker_rows(raw_rows)
    except Exception as exc:
        return [], [{"provider": "finviz_public_html", "error": f"{type(exc).__name__}: {exc}"}]
    events = []
    for row in raw_rows:
        symbol = str(row.get("Ticker") or row.get("ticker") or "").upper()
        if symbol not in wanted:
            continue
        raw = row.get("Earnings") or row.get("Earnings Date")
        day = _iso_day(raw)
        if not day:
            # Finviz commonly renders MM/DD/YYYY with an optional AM/PM suffix.
            token = str(raw or "").split()[0]
            for fmt in ("%m/%d/%Y", "%m/%d/%y"):
                try:
                    day = datetime.strptime(token, fmt).date().isoformat()
                    break
                except ValueError:
                    pass
        if not day:
            token = " ".join(str(raw or "").split()[:2]).split("/")[0]
            try:
                parsed = datetime.strptime(f"{token} {date.today().year}", "%b %d %Y").date()
                day = parsed.isoformat()
            except ValueError:
                pass
        if day:
            if date.fromisoformat(day) < date.today():
                continue
            text = str(raw).lower()
            timing = "BMO" if "bmo" in text or "before" in text or "/b" in text else "AMC" if "amc" in text or "after" in text or "/a" in text else "UNKNOWN"
            events.append({"symbol": symbol, "scheduled_date": day, "timing": timing,
                           "status": "ESTIMATED", "provider": "finviz_public_html",
                           "provider_event_id": f"finviz:{symbol}:{day}:{timing}"})
    return events, []


def collect(symbols: list[str], *, yahoo_fetch=None, finviz_fetch=None, observed_at: str | None = None) -> dict:
    yahoo, yahoo_errors = yahoo_events(symbols, fetch_one=yahoo_fetch)
    finviz, finviz_errors = finviz_events(symbols, fetch_fn=finviz_fetch)
    rows = yahoo + finviz
    by_symbol: dict[str, set[str]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], set()).add(row["scheduled_date"])
    for row in rows:
        row["conflict"] = len(by_symbol.get(row["symbol"], ())) > 1
        row["observed_at"] = observed_at or datetime.now(timezone.utc).isoformat()
        row["point_in_time_ready"] = True
    return {"status": "AVAILABLE" if rows else "UNAVAILABLE", "provider": "yahoo+finviz",
            "events": rows, "errors": yahoo_errors + finviz_errors,
            "detail": ("Estimated dates from free Yahoo/Finviz sources; conflicts are preserved. "
                       "Company confirmation is required for CONFIRMED status."),
            "read_only": True}
