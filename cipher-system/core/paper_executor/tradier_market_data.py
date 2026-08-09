from __future__ import annotations

from pathlib import Path

import os

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
    """Resolve the market-data token from whichever store this machine has.

    Windows Credential Manager first, so an existing Windows deployment keeps
    working untouched. Then an environment variable, which is how the GCP VM
    receives its secrets (cipher-secrets.service materialises /etc/cipher/cipher.env
    from Google Secret Manager). Then a 0600 file, checked for permissions rather
    than trusted: a market-data token in a world-readable file is still a leaked
    credential even though it can only read quotes.
    """
    try:
        import keyring  # type: ignore

        token = keyring.get_password(cfg.credential_service, cfg.credential_username)
        if token:
            return str(token)
    except Exception:
        pass  # no keyring backend on this platform; fall through

    for name in ("TRADIER_MARKET_TOKEN", "TRADIER_ACCESS_TOKEN", "TRADIER_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()

    token_file = Path(
        os.environ.get("CIPHER_TRADIER_TOKEN_FILE")
        or (Path.home() / ".config" / "cipher" / "tradier_token")
    )
    if token_file.exists():
        mode = token_file.stat().st_mode & 0o077
        if mode:
            raise RuntimeError(
                f"{token_file} is group/world readable ({oct(mode)}); chmod 600 it "
                f"before it will be used."
            )
        text = token_file.read_text(encoding="utf-8").strip()
        if text:
            return text

    raise RuntimeError(
        "No Tradier market-data token found. Set TRADIER_MARKET_TOKEN, store it in "
        "the platform credential manager, or write it to "
        f"{token_file} with mode 600."
    )


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
