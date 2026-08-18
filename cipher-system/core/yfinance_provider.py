"""Read-only Yahoo Finance/yfinance fallback data.

This adapter is intentionally narrower than the Alpaca provider. Yahoo Finance
can provide delayed underlying history and a limited option-chain snapshot, but
it does not provide Cipher's OPRA event-time options tape or feed Greeks. Every
normalized response carries its provider/feed and a degraded-data caveat.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any, Callable


class YFinanceUnavailable(RuntimeError):
    """The anonymous fallback cannot provide the requested observation."""


def _module():
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - exercised in deployment smoke
        raise YFinanceUnavailable("yfinance is not installed") from exc
    return yf


def _ticker(symbol: str, ticker_factory: Callable[[str], Any] | None = None) -> Any:
    factory = ticker_factory or _module().Ticker
    try:
        return factory(str(symbol).upper())
    except Exception as exc:  # noqa: BLE001 - provider exceptions are data availability
        raise YFinanceUnavailable(f"Yahoo Finance ticker unavailable for {symbol.upper()}") from exc


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return None if text.lower() in {"", "nat", "nan", "none"} else text


def _get(row: Any, name: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(name, default)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return default


def _history(ticker: Any, **kwargs: Any) -> Any:
    try:
        return ticker.history(**kwargs)
    except TypeError:
        # Older yfinance versions do not accept every keyword. Keep the fallback
        # compatible without broadening the request or hiding provider failures.
        kwargs.pop("auto_adjust", None)
        return ticker.history(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise YFinanceUnavailable(f"Yahoo Finance history unavailable: {type(exc).__name__}") from exc


def _rows(frame: Any) -> list[tuple[Any, Any]]:
    if frame is None or not hasattr(frame, "iterrows"):
        return []
    return list(frame.iterrows())


def _caveat(kind: str) -> str:
    if kind == "options":
        return (
            "Yahoo Finance/yfinance option-chain snapshot is delayed and unofficial for research. "
            "OPRA event-time trades and feed Greeks are not available; missing fields remain unknown."
        )
    if kind == "bars":
        return "Yahoo Finance/yfinance OHLCV is delayed/unofficial and may have provider-specific gaps."
    return "Yahoo Finance/yfinance quote is delayed/unofficial; it is a degraded fallback, not OPRA/SIP data."


def quote(symbol: str, *, ticker_factory: Callable[[str], Any] | None = None) -> dict:
    ticker = _ticker(symbol, ticker_factory)
    try:
        fast = getattr(ticker, "fast_info", {}) or {}
    except Exception:  # noqa: BLE001 - fast_info is an optional provider shortcut
        fast = {}
    last = _number(_get(fast, "last_price"))
    prior = _number(_get(fast, "previous_close"))
    observed_at = None
    if last is None or prior is None:
        frame = _history(ticker, period="5d", interval="1d", auto_adjust=False)
        history_rows = _rows(frame)
        if history_rows:
            observed_at = _iso(history_rows[-1][0])
            last = last if last is not None else _number(_get(history_rows[-1][1], "Close"))
            if prior is None and len(history_rows) >= 2:
                prior = _number(_get(history_rows[-2][1], "Close"))
    if last is None:
        raise YFinanceUnavailable(f"Yahoo Finance returned no quote for {str(symbol).upper()}")
    return {
        "ticker": str(symbol).upper(),
        "bid": None,
        "ask": None,
        "mid": None,
        "last": last,
        "price_context": last,
        "price_context_kind": "delayed_close",
        "as_of": observed_at or datetime.now(timezone.utc).isoformat(),
        "feed": "yahoo",
        "provider": "yfinance",
        "day_change_pct": ((last - prior) / prior * 100.0) if prior else None,
        "prior_close": prior,
        "availability": {"status": "available"},
        "caveat": _caveat("quote"),
    }


def bars(
    symbol: str,
    timeframe: str,
    *,
    limit: int = 200,
    start: datetime | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
) -> dict:
    intervals = {
        "1m": ("1m", "7d"),
        "5m": ("5m", "60d"),
        "15m": ("15m", "60d"),
        "1h": ("60m", "730d"),
        "4h": ("60m", "730d"),
        "1d": ("1d", "max"),
        "1w": ("1wk", "max"),
    }
    if timeframe not in intervals:
        raise ValueError(f"unsupported yfinance timeframe: {timeframe!r}")
    interval, period = intervals[timeframe]
    ticker = _ticker(symbol, ticker_factory)
    kwargs: dict[str, Any] = {"interval": interval, "auto_adjust": False}
    if start is not None:
        kwargs.update({"start": start, "end": datetime.now(timezone.utc)})
    else:
        kwargs["period"] = period
    frame = _history(ticker, **kwargs)
    normalized = []
    for stamp, row in _rows(frame):
        normalized.append(
            {
                "time": _iso(stamp),
                "open": _number(_get(row, "Open")),
                "high": _number(_get(row, "High")),
                "low": _number(_get(row, "Low")),
                "close": _number(_get(row, "Close")),
                "volume": _number(_get(row, "Volume")),
            }
        )
    normalized = [row for row in normalized if row["time"] is not None]
    if not normalized:
        raise YFinanceUnavailable(f"Yahoo Finance returned no {timeframe} bars for {str(symbol).upper()}")
    if start is None:
        normalized = normalized[-max(1, min(int(limit), 1000)) :]
    return {
        "ticker": str(symbol).upper(),
        "timeframe": timeframe,
        "feed": "yahoo",
        "provider": "yfinance",
        "bars": normalized,
        "caveat": _caveat("bars"),
        "read_only": True,
    }


def _occ_symbol(symbol: str, expiry: str, strike: float, option_type: str) -> str:
    kind = "C" if option_type == "call" else "P"
    return f"{symbol.upper()}{datetime.fromisoformat(expiry).strftime('%y%m%d')}{kind}{int(round(strike * 1000)):08d}"


def option_chain(
    symbol: str,
    *,
    expiration_count: int = 6,
    ticker_factory: Callable[[str], Any] | None = None,
) -> list[dict]:
    ticker = _ticker(symbol, ticker_factory)
    try:
        raw_expirations = list(getattr(ticker, "options", ()) or ())
    except Exception as exc:  # noqa: BLE001
        raise YFinanceUnavailable(f"Yahoo Finance options unavailable for {str(symbol).upper()}") from exc
    today = date.today().isoformat()
    expirations = sorted(str(expiry) for expiry in raw_expirations if str(expiry) >= today)
    expirations = expirations[: max(1, min(int(expiration_count), 12))]
    if not expirations:
        raise YFinanceUnavailable(f"Yahoo Finance returned no future options for {str(symbol).upper()}")
    contracts: list[dict] = []
    for expiry in expirations:
        try:
            chain = ticker.option_chain(expiry)
        except Exception as exc:  # noqa: BLE001
            raise YFinanceUnavailable(f"Yahoo Finance option chain unavailable for {expiry}") from exc
        for option_type, frame in (("call", getattr(chain, "calls", None)), ("put", getattr(chain, "puts", None))):
            for _, row in _rows(frame):
                strike = _number(_get(row, "strike"))
                if strike is None:
                    continue
                bid = _number(_get(row, "bid"))
                ask = _number(_get(row, "ask"))
                mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None
                contract = str(_get(row, "contractSymbol") or _occ_symbol(symbol, expiry, strike, option_type))
                contracts.append(
                    {
                        "symbol": contract,
                        "type": option_type,
                        "strike": strike,
                        "expiry": expiry,
                        "bid": bid,
                        "ask": ask,
                        "mid": mid,
                        "last": _number(_get(row, "lastPrice")),
                        "size": None,
                        "trade_time": _iso(_get(row, "lastTradeDate")),
                        "exchange": None,
                        "volume": _number(_get(row, "volume")),
                        "open_interest": _number(_get(row, "openInterest")),
                        "open_interest_date": None,
                        "iv": _number(_get(row, "impliedVolatility")),
                        # yfinance does not supply Cipher's feed Greeks.
                        "gamma": None,
                        "delta": None,
                        "theta": None,
                        "vega": None,
                        "rho": None,
                        "quote_time": _iso(_get(row, "lastTradeDate")),
                        "feed": "yahoo",
                        "provider": "yfinance",
                    }
                )
    if not contracts:
        raise YFinanceUnavailable(f"Yahoo Finance returned no option contracts for {str(symbol).upper()}")
    return contracts


def capability_status() -> dict:
    try:
        _module()
    except YFinanceUnavailable:
        return {
            "status": "unavailable",
            "quotes": "unavailable",
            "bars": "unavailable",
            "options_chain": "unavailable",
            "matrix": "unavailable",
            "flow": "unavailable",
            "caveat": "Install yfinance to enable the anonymous fallback.",
        }
    return {
        "status": "available",
        "quotes": "available_degraded",
        "bars": "available_degraded",
        "options_chain": "limited_degraded",
        "matrix": "limited_degraded",
        "flow": "unavailable",
        "caveat": _caveat("options"),
    }
