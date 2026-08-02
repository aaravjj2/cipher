from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .config import MarketDataConfig
from .models import Quote


ALLOWED_PATHS = {
    "/v1/markets/quotes",
    "/v1/markets/options/chains",
    "/v1/markets/options/expirations",
    "/v1/markets/history",
    "/v1/markets/timesales",
    "/v1/markets/events/session",
}
FORBIDDEN_FRAGMENTS = ("accounts", "orders", "placeorder", "positions", "balances", "gainloss", "trading")


class TradierEndpointBlocked(ValueError):
    pass


def assert_allowed_path(path: str) -> None:
    lowered = path.lower()
    if any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS) or path not in ALLOWED_PATHS:
        raise TradierEndpointBlocked("Tradier adapter is market-data-only; requested path is forbidden.")


def load_token(cfg: MarketDataConfig) -> str:
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise RuntimeError("Install keyring and store the Tradier market-data token in Windows Credential Manager.") from exc
    token = keyring.get_password(cfg.credential_service, cfg.credential_username)
    if not token:
        raise RuntimeError("Tradier market-data token was not found in the configured credential store.")
    return str(token)


class TradierMarketData:
    base_url = "https://api.tradier.com"

    def __init__(self, cfg: MarketDataConfig, token: str | None = None):
        self.cfg = cfg
        self._token = token

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = load_token(self.cfg)
        return self._token

    def request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        assert_allowed_path(path)
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Tradier market-data request failed for {path}") from exc

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        payload = self.request("/v1/markets/quotes", {"symbols": ",".join(symbols), "greeks": "false"})
        rows = (payload.get("quotes") or {}).get("quote") or []
        if isinstance(rows, dict):
            rows = [rows]
        now = datetime.now(timezone.utc)
        out: dict[str, Quote] = {}
        for row in rows:
            try:
                symbol = str(row.get("symbol")).upper()
                out[symbol] = Quote(
                    symbol=symbol,
                    bid=float(row.get("bid") or 0),
                    ask=float(row.get("ask") or 0),
                    last=float(row["last"]) if row.get("last") not in (None, "") else None,
                    timestamp=now,
                    bid_size=int(row["bidsize"]) if row.get("bidsize") not in (None, "") else None,
                    ask_size=int(row["asksize"]) if row.get("asksize") not in (None, "") else None,
                    volume=int(row["volume"]) if row.get("volume") not in (None, "") else None,
                    open_interest=int(row["open_interest"]) if row.get("open_interest") not in (None, "") else None,
                )
            except Exception:
                continue
        return out

    def expirations(self, ticker: str) -> list[str]:
        payload = self.request("/v1/markets/options/expirations", {"symbol": ticker, "includeAllRoots": "true", "strikes": "false"})
        dates = ((payload.get("expirations") or {}).get("date") or [])
        return [str(d) for d in (dates if isinstance(dates, list) else [dates])]

    def chain(self, ticker: str, expiration: str) -> list[dict[str, Any]]:
        payload = self.request("/v1/markets/options/chains", {"symbol": ticker, "expiration": expiration, "greeks": "false"})
        rows = ((payload.get("options") or {}).get("option") or [])
        return rows if isinstance(rows, list) else [rows]

    def timesales(self, symbol: str, start: str, end: str, interval: str = "1min", session_filter: str = "open") -> list[dict[str, Any]]:
        payload = self.request(
            "/v1/markets/timesales",
            {"symbol": symbol, "interval": interval, "start": start, "end": end, "session_filter": session_filter},
        )
        rows = ((payload.get("series") or {}).get("data") or [])
        return rows if isinstance(rows, list) else [rows]
