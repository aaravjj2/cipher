from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Iterable

from .config import ContractConfig
from .models import ContractCandidate, OptionContract, OptionType, Quote, SignalCard, SpreadCandidate

OCC_RE = re.compile(r"^[A-Z.]{1,6}\d{6}[CP]\d{8}$")


def occ_symbol(root: str, expiry: str, option_type: OptionType, strike: float) -> str:
    year, month, day = expiry.split("-")
    cp = "C" if option_type == OptionType.CALL else "P"
    return f"{root.upper()}{year[2:]}{month}{day}{cp}{int(round(float(strike) * 1000)):08d}"


def dte(expiration: str, now: datetime | None = None) -> int:
    now_date = (now or datetime.now(timezone.utc)).date()
    return (date.fromisoformat(expiration) - now_date).days


def contracts_from_chain(ticker: str, chain: Iterable[dict], option_type: OptionType) -> list[OptionContract]:
    out = []
    for row in chain:
        symbol = str(row.get("symbol") or "").upper()
        expiration = str(row.get("expiration_date") or row.get("expiration") or "")
        try:
            strike = float(row.get("strike"))
        except Exception:
            continue
        typ = str(row.get("option_type") or row.get("type") or "").lower()
        if typ and not typ.startswith(option_type.value[0]):
            continue
        out.append(OptionContract(symbol=symbol, ticker=ticker, expiration=expiration, strike=strike, option_type=option_type, active=bool(row.get("active", True))))
    return out


def evaluate_contract(card: SignalCard, contract: OptionContract, quote: Quote | None, cfg: ContractConfig, now: datetime | None = None) -> ContractCandidate:
    reasons: list[str] = []
    contract_dte = dte(contract.expiration, now)
    if contract_dte < cfg.minimum_dte or contract_dte > cfg.maximum_dte or (contract_dte == 0 and not cfg.allow_0dte):
        reasons.append("invalid_dte")
    if not contract.active:
        reasons.append("inactive_contract")
    if not OCC_RE.match(contract.symbol):
        reasons.append("invalid_option_symbol")
    if quote is None:
        reasons.append("missing_quote")
        return ContractCandidate(contract, quote, contract_dte, tuple(reasons), 999999.0)
    if quote.bid <= 0 or quote.bid < cfg.minimum_bid:
        reasons.append("bid_below_minimum")
    if quote.ask <= quote.bid:
        reasons.append("ask_not_above_bid")
    if quote.spread_pct > cfg.maximum_spread_pct:
        reasons.append("wide_spread")
    if quote.ask * 100 > cfg.maximum_contract_cost:
        reasons.append("max_cost")
    if quote.open_interest is not None and quote.open_interest < cfg.minimum_open_interest:
        reasons.append("open_interest_below_minimum")
    if quote.volume is not None and quote.volume < cfg.minimum_volume:
        reasons.append("volume_below_minimum")
    atm_distance = abs(contract.strike - card.spot)
    itm_penalty = 0.0
    if card.option_type == OptionType.CALL and contract.strike < card.spot:
        itm_penalty = 0.01
    if card.option_type == OptionType.PUT and contract.strike > card.spot:
        itm_penalty = 0.01
    score = round(atm_distance + quote.spread_pct / 100.0 + contract_dte / 1000.0 + itm_penalty, 6)
    return ContractCandidate(contract, quote, contract_dte, tuple(reasons), score)


def select_contract(card: SignalCard, contracts: Iterable[OptionContract], quotes: dict[str, Quote], cfg: ContractConfig, now: datetime | None = None) -> tuple[ContractCandidate | None, list[ContractCandidate]]:
    candidates = [evaluate_contract(card, c, quotes.get(c.symbol.upper()), cfg, now) for c in contracts if c.option_type == card.option_type]
    candidates.sort(key=lambda c: (not c.accepted, c.ranking_score, c.contract.expiration, c.contract.strike, c.contract.symbol))
    accepted = [c for c in candidates if c.accepted]
    return (accepted[0] if accepted else None), candidates


def select_debit_spread(
    card: SignalCard,
    contracts: Iterable[OptionContract],
    quotes: dict[str, Quote],
    cfg: ContractConfig,
    *,
    minimum_width: float = 1.0,
    maximum_width: float = 10.0,
    now: datetime | None = None,
) -> tuple[SpreadCandidate | None, list[ContractCandidate], list[SpreadCandidate]]:
    _, leg_candidates = select_contract(card, contracts, quotes, cfg, now)
    accepted_legs = [candidate for candidate in leg_candidates if candidate.accepted]
    spreads: list[SpreadCandidate] = []
    for long_leg in accepted_legs:
        long_contract = long_leg.contract
        long_quote = long_leg.quote
        if not long_quote:
            continue
        for short_leg in accepted_legs:
            short_contract = short_leg.contract
            short_quote = short_leg.quote
            if not short_quote or short_contract.symbol == long_contract.symbol:
                continue
            if short_contract.expiration != long_contract.expiration or short_contract.option_type != long_contract.option_type:
                continue
            width = abs(short_contract.strike - long_contract.strike)
            reasons: list[str] = []
            if width < minimum_width or width > maximum_width:
                reasons.append("invalid_spread_width")
            if card.option_type == OptionType.CALL and short_contract.strike <= long_contract.strike:
                reasons.append("short_leg_not_otm")
            if card.option_type == OptionType.PUT and short_contract.strike >= long_contract.strike:
                reasons.append("short_leg_not_otm")
            entry_debit = round(long_quote.ask - short_quote.bid, 4)
            if entry_debit <= 0:
                reasons.append("invalid_spread_debit")
            if entry_debit * 100 > cfg.maximum_contract_cost:
                reasons.append("max_cost")
            if entry_debit >= width:
                reasons.append("debit_exceeds_width")
            target_anchor = card.target if card.target is not None else card.spot
            target_distance = abs(target_anchor - card.spot)
            short_target_penalty = abs(abs(short_contract.strike - long_contract.strike) - max(target_distance, minimum_width))
            score = round(long_leg.ranking_score + short_target_penalty / 100.0 + entry_debit / 1000.0, 6)
            spreads.append(SpreadCandidate(
                long_leg=long_leg,
                short_leg=short_leg,
                width=round(width, 4),
                entry_debit=entry_debit,
                max_profit=round(width - entry_debit, 4),
                rejection_reasons=tuple(reasons),
                ranking_score=score,
            ))
    spreads.sort(key=lambda s: (not s.accepted, s.ranking_score, s.long_leg.contract.expiration, s.width, s.symbol))
    accepted = [spread for spread in spreads if spread.accepted]
    return (accepted[0] if accepted else None), leg_candidates, spreads
