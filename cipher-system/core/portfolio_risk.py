"""Manual stocks-and-options portfolio risk ledger.

The ledger records positions the user declares and marks them with injected market
data readers.  It never imports a broker client and cannot stage or place orders.
Unknown option marks and Greeks remain unknown and are surfaced as exceptions.
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "portfolio_risk"
STORE_PATH = DATA_DIR / "portfolio.json"
SCHEMA_VERSION = 1
GREEKS = ("delta", "gamma", "theta", "vega", "rho")
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank() -> dict:
    return {"schema_version": SCHEMA_VERSION, "cash": 0.0, "positions": []}


def _load(path: Path = STORE_PATH) -> dict:
    if not path.is_file():
        return _blank()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("cash", 0.0)
    data.setdefault("positions", [])
    return data


def _save(data: dict, path: Path = STORE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _number(value: Any, name: str, *, nonnegative: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if nonnegative and result < 0:
        raise ValueError(f"{name} cannot be negative")
    return result


def _normalize(raw: dict, *, preserve_id: bool = False) -> dict:
    ticker = str(raw.get("ticker") or "").strip().upper()
    if not ticker or len(ticker) > 12 or not ticker.replace(".", "").replace("-", "").isalnum():
        raise ValueError("ticker must be a valid symbol")
    asset_type = str(raw.get("asset_type") or "stock").lower()
    if asset_type not in {"stock", "option"}:
        raise ValueError("asset_type must be stock or option")
    quantity = _number(raw.get("quantity"), "quantity")
    if quantity == 0:
        raise ValueError("quantity cannot be zero; positive is long and negative is short")
    entry_price = _number(raw.get("entry_price"), "entry_price", nonnegative=True)
    fees = _number(raw.get("fees", 0), "fees", nonnegative=True)
    option_type = expiration = None
    strike = None
    contract_symbol = None
    if asset_type == "option":
        option_type = str(raw.get("option_type") or "").lower()
        if option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        strike = _number(raw.get("strike"), "strike", nonnegative=True)
        try:
            expiration = date.fromisoformat(str(raw.get("expiration") or "")).isoformat()
        except ValueError:
            raise ValueError("expiration must be YYYY-MM-DD") from None
        contract_symbol = str(raw.get("contract_symbol") or "").strip().upper() or None
    now = _now()
    return {
        "id": str(raw.get("id")) if preserve_id and raw.get("id") else uuid.uuid4().hex,
        "strategy": str(raw.get("strategy") or "Ungrouped").strip()[:80] or "Ungrouped",
        "asset_type": asset_type, "ticker": ticker, "contract_symbol": contract_symbol,
        "option_type": option_type, "strike": strike, "expiration": expiration,
        "quantity": quantity, "entry_price": entry_price, "fees": fees,
        "opened_at": str(raw.get("opened_at") or now)[:32],
        "notes": str(raw.get("notes") or "").strip()[:500] or None,
        "created_at": str(raw.get("created_at") or now), "updated_at": now,
    }


def _repository_data(repository) -> dict:
    settings = repository.list_rows("portfolio_risk_settings", query={}) or []
    cash = float(settings[0].get("cash") or 0) if settings else 0.0
    positions = []
    for row in repository.list_rows("portfolio_risk_positions", query={}) or []:
        metadata = dict(row.get("metadata") or {})
        metadata.update({
            "id": row.get("id"),
            "ticker": row.get("ticker"),
            "contract_symbol": row.get("contract") or metadata.get("contract_symbol"),
            "quantity": row.get("quantity"),
            "entry_price": row.get("entry_price"),
        })
        positions.append(_normalize(metadata, preserve_id=True))
    return {"schema_version": SCHEMA_VERSION, "cash": cash, "positions": positions}


def add_position(raw: dict, path: Path = STORE_PATH, *, repository=None) -> dict:
    row = _normalize(raw)
    if repository is not None:
        rows = repository.insert_row(
            "portfolio_risk_positions",
            {
                "ticker": row["ticker"],
                "contract": row["contract_symbol"],
                "quantity": row["quantity"],
                "entry_price": row["entry_price"],
                "direction": "LONG" if row["quantity"] > 0 else "SHORT",
                "metadata": row,
            },
        ) or []
        if not rows:
            raise ValueError("portfolio position was not saved")
        saved = dict(rows[0])
        persisted_id = str(saved.get("id") or row["id"])
        saved.update(row)
        saved["id"] = persisted_id
        return saved
    with _LOCK:
        data = _load(path)
        data["positions"].append(row)
        _save(data, path)
    return row


def delete_position(position_id: str, path: Path = STORE_PATH, *, repository=None) -> dict:
    if repository is not None:
        if not repository.get_row("portfolio_risk_positions", str(position_id)):
            raise ValueError("unknown portfolio position")
        repository.delete_row("portfolio_risk_positions", str(position_id))
        return {"deleted": str(position_id)}
    with _LOCK:
        data = _load(path)
        before = len(data["positions"])
        data["positions"] = [r for r in data["positions"] if r.get("id") != str(position_id)]
        if len(data["positions"]) == before:
            raise ValueError("unknown portfolio position")
        _save(data, path)
    return {"deleted": str(position_id)}


def set_cash(value: Any, path: Path = STORE_PATH, *, repository=None) -> dict:
    cash = _number(value, "cash")
    if repository is not None:
        saved = repository.upsert_row("portfolio_risk_settings", {"cash": cash, "settings": {}}, conflict_column="user_id")
        if not saved:
            raise ValueError("portfolio cash was not saved")
        return {"cash": cash}
    with _LOCK:
        data = _load(path)
        data["cash"] = cash
        _save(data, path)
    return {"cash": cash}


CSV_FIELDS = ("strategy", "asset_type", "ticker", "contract_symbol", "option_type", "strike",
              "expiration", "quantity", "entry_price", "fees", "opened_at", "notes")


def export_csv(path: Path = STORE_PATH, *, repository=None) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    source = _repository_data(repository) if repository is not None else _load(path)
    for row in source["positions"]:
        writer.writerow(row)
    return output.getvalue()


def import_csv(text: str, path: Path = STORE_PATH, *, replace: bool = False, repository=None) -> dict:
    if len(text.encode("utf-8")) > 1_000_000:
        raise ValueError("CSV is limited to 1 MB")
    rows = [_normalize(dict(row)) for row in csv.DictReader(io.StringIO(text))]
    if len(rows) > 2000:
        raise ValueError("CSV is limited to 2000 positions")
    if repository is not None:
        if replace:
            for existing in repository.list_rows("portfolio_risk_positions", query={}) or []:
                repository.delete_row("portfolio_risk_positions", str(existing.get("id")))
        for row in rows:
            add_position(row, repository=repository)
        return {"imported": len(rows), "replaced": bool(replace)}
    with _LOCK:
        data = _load(path)
        data["positions"] = rows if replace else data["positions"] + rows
        _save(data, path)
    return {"imported": len(rows), "replaced": bool(replace)}


def _match_option(position: dict, contracts: list[dict]) -> dict | None:
    if position.get("contract_symbol"):
        exact = next((r for r in contracts if r.get("symbol") == position["contract_symbol"]), None)
        if exact:
            return exact
    return next((r for r in contracts if r.get("expiry") == position["expiration"]
                 and r.get("type") == position["option_type"]
                 and abs(float(r.get("strike") or 0) - float(position["strike"])) < 1e-6), None)


def status(*, quote_fn: Callable[[str], dict], chain_fn: Callable[[str, str, str], list[dict]],
           path: Path = STORE_PATH, repository=None) -> dict:
    data = _repository_data(repository) if repository is not None else _load(path)
    positions = [dict(row) for row in data["positions"]]
    tickers = sorted({row["ticker"] for row in positions})
    quotes: dict[str, dict | None] = {}
    contracts: dict[str, list[dict]] = {}
    exceptions: list[dict] = []
    today = date.today().isoformat()
    for ticker in tickers:
        try:
            quotes[ticker] = quote_fn(ticker)
        except Exception as exc:  # one failed mark must not erase the portfolio
            quotes[ticker] = None
            exceptions.append({"ticker": ticker, "kind": "QUOTE_UNAVAILABLE", "detail": str(exc)})
        options = [r for r in positions if r["ticker"] == ticker and r["asset_type"] == "option"]
        if options:
            latest = max(r["expiration"] for r in options)
            try:
                contracts[ticker] = chain_fn(ticker, today, latest)
            except Exception as exc:
                contracts[ticker] = []
                exceptions.append({"ticker": ticker, "kind": "CHAIN_UNAVAILABLE", "detail": str(exc)})

    greek_totals = {name: 0.0 for name in GREEKS}
    greek_unknown = {name: False for name in GREEKS}
    total_value = total_basis = total_pnl = 0.0
    marked_value = 0.0
    exposures: dict[str, float] = defaultdict(float)
    expiry: dict[str, dict] = {}
    output = []
    for position in positions:
        q = quotes.get(position["ticker"])
        spot = float(q["price_context"]) if q and q.get("price_context") is not None else None
        multiplier = 1 if position["asset_type"] == "stock" else 100
        option = _match_option(position, contracts.get(position["ticker"], [])) if position["asset_type"] == "option" else None
        mark = spot if position["asset_type"] == "stock" else (
            option.get("mid") if option and option.get("mid") is not None else option.get("last") if option else None
        )
        row = {**position, "multiplier": multiplier, "current_mark": mark,
               "mark_as_of": q.get("as_of") if position["asset_type"] == "stock" and q else option.get("quote_time") if option else None,
               "mark_source": "underlying_quote" if position["asset_type"] == "stock" else "option_mid" if option and option.get("mid") is not None else "option_last" if option else None,
               "spot": spot, "greeks": {}}
        basis = position["entry_price"] * position["quantity"] * multiplier
        row["signed_cost_basis"] = basis
        total_basis += basis
        if mark is None:
            row.update({"market_value": None, "unrealized_pnl": None})
            exceptions.append({"position_id": position["id"], "ticker": position["ticker"], "kind": "MARK_UNKNOWN", "detail": "No current executable option midpoint/last or stock quote."})
        else:
            value = mark * position["quantity"] * multiplier
            pnl = value - basis - position["fees"]
            row.update({"market_value": value, "unrealized_pnl": pnl})
            total_value += value
            total_pnl += pnl
            marked_value += abs(value)
        if position["asset_type"] == "stock":
            values = {"delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
        else:
            values = {name: option.get(name) if option else None for name in GREEKS}
        for name, value in values.items():
            exposure = None if value is None else float(value) * position["quantity"] * multiplier
            row["greeks"][name] = exposure
            if exposure is None:
                greek_unknown[name] = True
            else:
                greek_totals[name] += exposure
        delta_units = row["greeks"]["delta"]
        if delta_units is not None and spot is not None:
            exposures[position["ticker"]] += delta_units * spot
        if position.get("expiration"):
            bucket = expiry.setdefault(position["expiration"], {"expiration": position["expiration"], "positions": 0, "short_contracts": 0, "tickers": set()})
            bucket["positions"] += 1
            bucket["short_contracts"] += abs(position["quantity"]) if position["quantity"] < 0 else 0
            bucket["tickers"].add(position["ticker"])
            if position["expiration"] < today:
                exceptions.append({"position_id": position["id"], "ticker": position["ticker"], "kind": "EXPIRED_POSITION", "detail": position["expiration"]})
        output.append(row)

    gross_delta_dollars = sum(abs(v) for v in exposures.values())
    concentration = [{"ticker": k, "delta_dollars": v,
                      "weight_pct": abs(v) / gross_delta_dollars * 100 if gross_delta_dollars else None}
                     for k, v in sorted(exposures.items(), key=lambda item: abs(item[1]), reverse=True)]
    groups: dict[str, list[str]] = defaultdict(list)
    for row in output:
        groups[row["strategy"]].append(row["id"])
    return {
        "as_of": _now(), "cash": data["cash"], "positions": output,
        "summary": {"position_count": len(output), "signed_cost_basis": total_basis,
                    "signed_market_value": total_value, "marked_gross_value": marked_value,
                    "unrealized_pnl": total_pnl, "net_liquidating_value": data["cash"] + total_value,
                    "aggregate_greeks": {name: None if greek_unknown[name] else value for name, value in greek_totals.items()}},
        "concentration": concentration,
        "expiration_calendar": [{**v, "tickers": sorted(v["tickers"])} for _, v in sorted(expiry.items())],
        "strategy_groups": [{"name": name, "position_ids": ids} for name, ids in sorted(groups.items())],
        "exceptions": exceptions,
        "read_only_market_data": True, "manual_ledger": True, "execution_capability": False,
        "caveat": "Manual ledger, not a broker statement. Option marks use current mid when available, then last trade; unknown marks and Greeks remain unknown. Short options require assignment and ex-dividend review.",
    }
