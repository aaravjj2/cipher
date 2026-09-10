"""Paper-executor market data through Cipher's local Alpaca-backed API.

This adapter deliberately talks only to the read-only core service.  It may use
server-side Alpaca credentials to establish a guest-scoped provider session;
those credentials never enter the plan, browser, executor payload, or broker
order path.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

from .config import MarketDataConfig
from .models import Quote


_OCC = re.compile(r"^(?P<root>[A-Z.]{1,6})(?P<date>\d{6})[CP]\d{8}$")


def _timestamp(value: Any) -> datetime:
    if not value:
        raise ValueError("quote timestamp missing")
    text = str(value).replace("Z", "+00:00")
    # Alpaca emits nanoseconds while datetime accepts microseconds.  Preserve
    # timezone semantics and truncate only excess fractional precision.
    text = re.sub(r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", text)
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid quote timestamp") from exc
    if stamp.tzinfo is None:
        raise ValueError("quote timestamp requires timezone")
    return stamp.astimezone(timezone.utc)


class AlpacaCoreMarketData:
    """Normalize `/api/quote` and `/api/options-chain` for the simulator."""

    def __init__(self, cfg: MarketDataConfig):
        self.cfg = cfg
        self.base_url = cfg.core_url.rstrip("/")
        self._lock = threading.RLock()
        self._chains: dict[str, tuple[float, dict[str, Any]]] = {}
        self._contracts: dict[str, dict[str, Any]] = {}
        self._provider_session_id: str | None = None
        self.last_chain_success_at: str | None = None
        self.last_error: dict[str, Any] | None = None

    @property
    def provider_session_ready(self) -> bool:
        with self._lock:
            return bool(self._provider_session_id)

    @property
    def market_data_ready(self) -> bool:
        return self.provider_session_ready and self.last_chain_success_at is not None

    def status(self) -> dict[str, Any]:
        return {
            "provider": "alpaca_core",
            "provider_session_ready": self.provider_session_ready,
            "market_data_ready": self.market_data_ready,
            "last_chain_success_at": self.last_chain_success_at,
            "last_error": self.last_error,
        }

    def _auth_headers(self) -> dict[str, str]:
        """Build hosted-core guest context; credentials never enter GET requests."""
        token = os.environ.get("CIPHER_INTERNAL_PROXY_TOKEN", "")
        if not token:
            return {"Accept": "application/json"}
        with self._lock:
            session_id = self._provider_session_id
        if not session_id:
            session_id = self._connect_provider_session(token)
        return {"Accept": "application/json", "X-Cipher-Internal-Token": token,
                "X-Cipher-User-Id": "guest", "X-Cipher-Guest": "1",
                "X-Cipher-Provider-Session": session_id}

    def _connect_provider_session(self, token: str) -> str:
        key = (os.environ.get("ALPACA_ALGO_KEY") or os.environ.get("ALPACA_ALGO_PLUS_KEY")
               or os.environ.get("ALPACA_API_KEY"))
        secret = (os.environ.get("ALPACA_ALGO_SECRET") or os.environ.get("ALPACA_ALGO_PLUS_SECRET")
                  or os.environ.get("ALPACA_API_SECRET"))
        if not key or not secret:
            raise RuntimeError("hosted Alpaca credentials are not configured for the paper executor")
        body = json.dumps({"action": "connect", "key": key, "secret": secret,
                           "options_feed": "opra", "stock_feed": "sip"}).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/internal/provider-session", data=body,
            method="POST", headers={"Content-Type": "application/json", "Accept": "application/json",
            "X-Cipher-Internal-Token": token, "X-Cipher-User-Id": "guest", "X-Cipher-Guest": "1"})
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("Cipher core provider-session connection failed") from exc
        session_id = str(payload.get("provider_session_id") or "") if isinstance(payload, dict) else ""
        if not session_id:
            raise RuntimeError("Cipher core did not return a provider session")
        with self._lock:
            self._provider_session_id = session_id
        return session_id

    def _clear_provider_session(self) -> None:
        with self._lock:
            self._provider_session_id = None

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if path not in {"/api/quote", "/api/options-chain"}:
            raise ValueError("Alpaca core adapter is restricted to read-only quote endpoints.")
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=self._auth_headers())
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and os.environ.get("CIPHER_INTERNAL_PROXY_TOKEN"):
                self._clear_provider_session()
                retry = urllib.request.Request(url, headers=self._auth_headers())
                try:
                    with urllib.request.urlopen(retry, timeout=self.cfg.request_timeout_seconds) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                except Exception as retry_exc:
                    self._remember_error(path, retry_exc)
                    raise RuntimeError(self._error_message(path, retry_exc)) from retry_exc
            else:
                self._remember_error(path, exc)
                raise RuntimeError(self._error_message(path, exc)) from exc
        except Exception as exc:
            self._remember_error(path, exc)
            raise RuntimeError(self._error_message(path, exc)) from exc
        if not isinstance(payload, dict) or payload.get("error"):
            self.last_error = {
                "at": datetime.now(timezone.utc).isoformat(), "path": path,
                "status": None, "reason": str(payload.get("error") if isinstance(payload, dict) else "invalid response")[:240],
            }
            raise RuntimeError(f"Cipher core returned an invalid market-data response for {path}")
        return payload

    def _remember_error(self, path: str, exc: Exception) -> None:
        self.last_error = {
            "at": datetime.now(timezone.utc).isoformat(),
            "path": path,
            "status": getattr(exc, "code", None),
            "reason": getattr(exc, "reason", None) or type(exc).__name__,
        }

    @staticmethod
    def _error_message(path: str, exc: Exception) -> str:
        status = getattr(exc, "code", None)
        reason = getattr(exc, "reason", None) or type(exc).__name__
        suffix = f" HTTP {status}" if status is not None else ""
        return f"Cipher core market-data request failed for {path}{suffix}: {reason}"

    def _chain_payload(self, ticker: str, *, force: bool = False) -> dict[str, Any]:
        ticker = ticker.upper()
        with self._lock:
            cached = self._chains.get(ticker)
            if not force and cached and time.monotonic() - cached[0] <= self.cfg.chain_cache_seconds:
                return cached[1]
        params = {
            "ticker": ticker,
            "feed": "opra",
            "expirations": self.cfg.chain_expiration_count,
            "fresh": "1" if force else "0",
        }
        payload = self._request("/api/options-chain", params)
        if payload.get("feed") != "opra" and self.provider_session_ready:
            # A restarted core silently serves fallback data for unknown
            # provider sessions instead of answering 401, which left entries
            # blocked until the executor itself was restarted. Treat any
            # non-OPRA body while holding a session as a stale session: drop
            # it and retry once before failing closed.
            self._clear_provider_session()
            self.last_error = {
                "at": datetime.now(timezone.utc).isoformat(), "path": "/api/options-chain",
                "status": None, "reason": "STALE_PROVIDER_SESSION_RETRY",
            }
            params["fresh"] = "1"
            payload = self._request("/api/options-chain", params)
        if payload.get("feed") != "opra":
            self.last_error = {
                "at": datetime.now(timezone.utc).isoformat(), "path": "/api/options-chain",
                "status": None, "reason": "OPRA_REQUIRED",
            }
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
            self.last_chain_success_at = datetime.now(timezone.utc).isoformat()
            self.last_error = None
        return payload

    @staticmethod
    def _quote(row: dict[str, Any]) -> Quote | None:
        try:
            bid, ask = float(row["bid"]), float(row["ask"])
            stamp = _timestamp(row.get("quote_time") or row.get("as_of"))
            last = float(row["last"]) if row.get("last") is not None else None
            volume = int(row["volume"]) if row.get("volume") is not None else None
            oi = int(row["open_interest"]) if row.get("open_interest") is not None else None
            bid_size = int(row["bid_size"]) if row.get("bid_size") is not None else None
            ask_size = int(row["ask_size"]) if row.get("ask_size") is not None else None
        except (KeyError, TypeError, ValueError):
            return None
        if (not all(math.isfinite(value) for value in (bid, ask))
                or (last is not None and not math.isfinite(last))
                or bid < 0 or ask <= 0 or ask < bid
                or any(value is not None and value < 0 for value in (volume, oi, bid_size, ask_size))):
            return None
        return Quote(
            symbol=str(row.get("symbol") or row.get("ticker") or "").upper(),
            bid=bid,
            ask=ask,
            last=last, timestamp=stamp, volume=volume, open_interest=oi,
            bid_size=bid_size, ask_size=ask_size,
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
