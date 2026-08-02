from datetime import datetime, timezone

from core.paper_executor.config import ContractConfig
from core.paper_executor.contract_selector import OptionContract, OptionType, select_contract
from core.paper_executor.models import Direction, Quote, SignalCard


def test_contract_selection_prefers_nearest_atm_with_valid_quote():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    card = SignalCard("AAPL", "flash", Direction.BULLISH, "ceiling rejection", now, 100, 102, 99, {})
    contracts = [
        OptionContract("AAPL260730C00105000", "AAPL", "2026-07-30", 105, OptionType.CALL),
        OptionContract("AAPL260730C00100000", "AAPL", "2026-07-30", 100, OptionType.CALL),
    ]
    quotes = {c.symbol: Quote(c.symbol, 1.0, 1.05, now, volume=50, open_interest=500) for c in contracts}
    selected, candidates = select_contract(card, contracts, quotes, ContractConfig(), now)
    assert selected is not None
    assert selected.contract.strike == 100
    assert len(candidates) == 2


def test_missing_open_interest_is_not_treated_as_zero():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    card = SignalCard("AAPL", "flash", Direction.BULLISH, "ceiling rejection", now, 100, 102, 99, {})
    contract = OptionContract("AAPL260730C00100000", "AAPL", "2026-07-30", 100, OptionType.CALL)
    quote = Quote(contract.symbol, 1.0, 1.05, now, volume=None, open_interest=None)
    selected, _ = select_contract(card, [contract], {contract.symbol: quote}, ContractConfig(), now)
    assert selected is not None
