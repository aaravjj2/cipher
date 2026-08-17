"""Paper-executor market data through Cipher's local Alpaca-backed API.

This adapter deliberately talks only to the read-only core service.  It has no
Alpaca credentials and cannot reach a brokerage endpoint, which keeps the
autopilot boundary independently auditable.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .config import MarketDataConfig
from .models import Quote


_OCC = re.compile(r"^(?P<root>[A-Z.]{1,6})(?P<date>\d{6})[CP]\d{8}$")


def _timestamp(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    # Alpaca emits nanoseconds while datetime accepts microseconds.  Preserve
    # timezone semantics and truncate only excess fractional precision.
    text = re.sub(r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", text)
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


class AlpacaCoreMarketData:
    """Normalize `/api/quote` and `/api/options-chain` for the simulator."""

    def __init__(self, cfg: MarketDataConfig):
        self.cfg = cfg
        self.base_url = cfg.core_url.rstrip("/")
        self._lock = threading.RLock()
        self._chains: dict[str, tuple[float, dict[str, Any]]] = {}
        self._contracts: dict[str, dict[str, Any]] = {}

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if path not in {"/api/quote", "/api/options-chain"}:
            raise ValueError("Alpaca core adapter is restricted to read-only quote endpoints.")
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cipher core market-data request failed for {path}") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(f"Cipher core returned an invalid market-data response for {path}")
        return payload

    def _chain_payload(self, ticker: str, *, force: bool = False) -> dict[str, Any]:
        ticker = ticker.upper()
        with self._lock:
            cached = self._chains.get(ticker)
            if not force and cached and time.monotonic() - cached[0] <= self.cfg.chain_cache_seconds:
                return cached[1]
        payload = self._request("/api/options-chain", {
            "ticker": ticker,
            "feed": "opra",
            "expirations": self.cfg.chain_expiration_count,
            "fresh": "1" if force else "0",
        })
        if payload.get("feed") != "opra":
            raise RuntimeError("OPRA is unavailable; paper entries are blocked on fallback option data.")
        contracts: dict[str, dict[str, Any]] = {}
        for expiration in payload.get("expirations") or []:
            for strike_row in expiration.get("rows") or []:
                for side in ("call", "put"):
                    row = strike_row.get(side)
                    if isinstance(row, dict) and row.get("symbol"):
                        contracts[str(row["symbol"]).upper()] = row
        with self._lock:
            self._chains[ticker] = (time.monotonic(), payload)
            self._contracts.update(contracts)
        return payload

    @staticmethod
    def _quote(row: dict[str, Any]) -> Quote | None:
        try:
            bid, ask = float(row["bid"]), float(row["ask"])
        except (KeyError, TypeError, ValueError):
            return None
        if bid < 0 or ask <= 0 or ask < bid:
            return None
        return Quote(
            symbol=str(row.get("symbol") or row.get("ticker") or "").upper(),
            bid=bid,
            ask=ask,
            last=float(row["last"]) if row.get("last") is not None else None,
            timestamp=_timestamp(row.get("quote_time") or row.get("as_of")),
            volume=int(row["volume"]) if row.get("volume") is not None else None,
            open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
        )

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        requested = {str(symbol).upper() for symbol in symbols if symbol}
        option_symbols = {symbol for symbol in requested if _OCC.match(symbol)}
        underlyings = requested - option_symbols
        roots = {match.group("root") for symbol in option_symbols if (match := _OCC.match(symbol))}
        # Refresh each underlying once when its adapter cache expires.  Merely
        # rereading `_contracts` would freeze every open position at its entry
        # quote and make automated exits meaningless.
        for root in sorted(roots):
            self._chain_payload(root)
        missing_options = option_symbols - set(self._contracts)
        for symbol in sorted(missing_options):
            match = _OCC.match(symbol)
            if match:
                self._chain_payload(match.group("root"), force=True)

        out: dict[str, Quote] = {}
        with self._lock:
            rows = {symbol: self._contracts.get(symbol) for symbol in option_symbols}
        for symbol, row in rows.items():
            quote = self._quote(row) if row else None
            if quote:
                out[symbol] = quote
        for ticker in sorted(underlyings):
            payload = self._request("/api/quote", {"ticker": ticker})
            row = {**payload, "symbol": ticker, "quote_time": payload.get("as_of")}
            quote = self._quote(row)
            if quote:
                out[ticker] = quote
        return out

    def expirations(self, ticker: str) -> list[str]:
        payload = self._chain_payload(ticker)
        return [str(row["expiration"]) for row in payload.get("expirations") or [] if row.get("expiration")]

    def chain(self, ticker: str, expiration: str) -> list[dict[str, Any]]:
        payload = self._chain_payload(ticker)
        group = next((row for row in payload.get("expirations") or [] if row.get("expiration") == expiration), None)
        if not group:
            return []
        out = []
        for strike_row in group.get("rows") or []:
            for side in ("call", "put"):
                row = strike_row.get(side)
                if isinstance(row, dict):
                    out.append({**row, "expiration": row.get("expiry") or expiration})
        return out
