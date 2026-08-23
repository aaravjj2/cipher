"""Fail-closed Alpaca paper-order adapter.

The base URL and credential class are intentionally not configurable: this
adapter cannot address Alpaca's live brokerage API.  The LLM/tool layer never
receives an instance; only the deterministic paper executor calls it after its
existing policy and contract gates pass.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import TradeIntent


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
TERMINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected", "suspended"}


class AlpacaPaperBrokerError(RuntimeError):
    pass


Transport = Callable[[str, str, dict[str, Any] | None], Any]


class AlpacaPaperBroker:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = PAPER_BASE_URL,
        account_id: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        if base_url != PAPER_BASE_URL:
            raise ValueError("Alpaca paper broker is locked to paper-api.alpaca.markets.")
        if not api_key.startswith("PK"):
            raise ValueError("Alpaca paper broker requires a PK-prefixed paper key.")
        if not api_secret:
            raise ValueError("Alpaca paper broker secret is required.")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.expected_account_id = account_id or None
        self._transport = transport or self._http

    @classmethod
    def from_environment(cls) -> "AlpacaPaperBroker":
        key = os.environ.get("ALPACA_PAPER_API_KEY") or os.environ.get("ALPACA_ALGO_KEY") or ""
        secret = os.environ.get("ALPACA_PAPER_API_SECRET") or os.environ.get("ALPACA_ALGO_SECRET") or ""
        return cls(key, secret, account_id=os.environ.get("CIPHER_ALPACA_PAPER_ACCOUNT_ID"))

    def _http(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        if not path.startswith("/") or "//" in path:
            raise ValueError("Invalid Alpaca paper API path.")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Content-Type": "application/json",
                "User-Agent": "Cipher-Paper-Agent/1.0",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AlpacaPaperBrokerError(f"Alpaca paper HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise AlpacaPaperBrokerError("Alpaca paper API is unreachable.") from exc

    def account(self) -> dict[str, Any]:
        raw = self._transport("GET", "/v2/account", None)
        if not isinstance(raw, dict):
            raise AlpacaPaperBrokerError("Alpaca paper account response was not an object.")
        account_id = str(raw.get("id") or "")
        if self.expected_account_id and account_id != self.expected_account_id:
            raise AlpacaPaperBrokerError("Connected Alpaca paper account does not match the configured account.")
        return {
            "id": account_id,
            "status": str(raw.get("status") or "unknown"),
            "currency": str(raw.get("currency") or "USD"),
            "buying_power": _float(raw.get("buying_power")),
            "equity": _float(raw.get("equity")),
            "trading_blocked": bool(raw.get("trading_blocked")),
            "account_blocked": bool(raw.get("account_blocked")),
            "paper_only": True,
        }

    def orders(self, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        query = urlencode({"status": status, "limit": min(max(limit, 1), 500), "direction": "desc"})
        raw = self._transport("GET", f"/v2/orders?{query}", None)
        if not isinstance(raw, list):
            raise AlpacaPaperBrokerError("Alpaca paper orders response was not a list.")
        return [self._normalize_order(item) for item in raw if isinstance(item, dict)]

    def positions(self) -> list[dict[str, Any]]:
        raw = self._transport("GET", "/v2/positions", None)
        if not isinstance(raw, list):
            raise AlpacaPaperBrokerError("Alpaca paper positions response was not a list.")
        return [{
            "symbol": str(item.get("symbol") or ""),
            "quantity": _float(item.get("qty")),
            "side": str(item.get("side") or ""),
            "average_entry_price": _float(item.get("avg_entry_price")),
            "market_value": _float(item.get("market_value")),
            "unrealized_pl": _float(item.get("unrealized_pl")),
        } for item in raw if isinstance(item, dict)]

    def order(self, broker_order_id: str) -> dict[str, Any]:
        raw = self._transport("GET", f"/v2/orders/{broker_order_id}", None)
        if not isinstance(raw, dict):
            raise AlpacaPaperBrokerError("Alpaca paper order response was not an object.")
        return self._normalize_order(raw)

    def find_client_order(self, client_order_id: str) -> dict[str, Any] | None:
        for order in self.orders(limit=500):
            if order["client_order_id"] == client_order_id:
                return order
        return None

    def submit(self, intent: TradeIntent) -> dict[str, Any]:
        if intent.side not in {"buy", "sell"}:
            raise ValueError("Paper order side must be buy or sell.")
        if intent.quantity < 1 or intent.limit_price <= 0:
            raise ValueError("Paper order quantity and limit price must be positive.")
        existing = self.find_client_order(intent.client_order_id)
        if existing:
            return existing
        raw = self._transport("POST", "/v2/orders", {
            "symbol": intent.contract_symbol,
            "qty": str(intent.quantity),
            "side": intent.side,
            "type": "limit",
            "time_in_force": "day",
            "limit_price": f"{intent.limit_price:.2f}",
            "client_order_id": intent.client_order_id,
        })
        if not isinstance(raw, dict):
            raise AlpacaPaperBrokerError("Alpaca paper submit response was not an object.")
        return self._normalize_order(raw)

    def cancel(self, broker_order_id: str) -> dict[str, Any]:
        raw = self._transport("DELETE", f"/v2/orders/{broker_order_id}", None)
        return self._normalize_order(raw) if isinstance(raw, dict) and raw else {
            "id": broker_order_id, "status": "cancel_requested", "paper_only": True,
        }

    def wait_for_terminal(self, broker_order_id: str, timeout_seconds: int, poll_interval_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        latest = self.order(broker_order_id)
        while latest["status"] not in TERMINAL_ORDER_STATUSES and time.monotonic() < deadline:
            time.sleep(poll_interval_seconds)
            latest = self.order(broker_order_id)
        if latest["status"] not in TERMINAL_ORDER_STATUSES:
            self.cancel(broker_order_id)
            latest = self.order(broker_order_id)
        return latest

    @staticmethod
    def _normalize_order(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(raw.get("id") or ""),
            "client_order_id": str(raw.get("client_order_id") or ""),
            "symbol": str(raw.get("symbol") or ""),
            "side": str(raw.get("side") or ""),
            "type": str(raw.get("type") or ""),
            "time_in_force": str(raw.get("time_in_force") or ""),
            "status": str(raw.get("status") or "unknown").lower(),
            "quantity": _float(raw.get("qty")),
            "filled_quantity": _float(raw.get("filled_qty")),
            "limit_price": _float(raw.get("limit_price")),
            "average_fill_price": _float(raw.get("filled_avg_price")),
            "submitted_at": raw.get("submitted_at"),
            "filled_at": raw.get("filled_at"),
            "paper_only": True,
        }


def intent_payload(intent: TradeIntent) -> dict[str, Any]:
    payload = asdict(intent)
    payload["expires_at"] = intent.expires_at.isoformat()
    payload["client_order_id"] = intent.client_order_id
    payload["paper_only"] = True
    return payload


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
