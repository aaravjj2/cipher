from datetime import datetime, timezone

from core.paper_executor.config import ContractConfig
from core.paper_executor.contract_selector import OptionContract, OptionType, select_contract, select_debit_spread
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


def test_unaffordable_atm_does_not_fall_through_to_far_otm_lottery_ticket():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    card = SignalCard("MU", "cipher", Direction.BULLISH, "triple cluster", now, 1037, 1050, 1030, {})
    contracts = [
        OptionContract("MU260911C01035000", "MU", "2026-09-11", 1035, OptionType.CALL),
        OptionContract("MU260911C01030000", "MU", "2026-09-11", 1030, OptionType.CALL),
        OptionContract("MU260911C01105000", "MU", "2026-09-11", 1105, OptionType.CALL),
    ]
    quotes = {
        contracts[0].symbol: Quote(contracts[0].symbol, 8.0, 8.2, now, volume=50, open_interest=500),
        contracts[1].symbol: Quote(contracts[1].symbol, 11.0, 11.2, now, volume=50, open_interest=500),
        contracts[2].symbol: Quote(contracts[2].symbol, 4.6, 4.8, now, volume=50, open_interest=500),
    }
    selected, candidates = select_contract(card, contracts, quotes, ContractConfig(maximum_contract_cost=500), now)
    assert selected is None
    far_otm = next(row for row in candidates if row.contract.strike == 1105)
    assert "moneyness_not_allowed" in far_otm.rejection_reasons


def test_debit_spread_caps_net_debit_not_each_leg_price():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    card = SignalCard("MU", "cipher", Direction.BULLISH, "triple cluster", now, 1037, 1050, 1030, {})
    contracts = [
        OptionContract("MU260911C01035000", "MU", "2026-09-11", 1035, OptionType.CALL),
        OptionContract("MU260911C01045000", "MU", "2026-09-11", 1045, OptionType.CALL),
    ]
    quotes = {
        contracts[0].symbol: Quote(contracts[0].symbol, 8.0, 8.2, now, volume=50, open_interest=500),
        contracts[1].symbol: Quote(contracts[1].symbol, 5.4, 5.6, now, volume=50, open_interest=500),
    }
    selected, legs, _ = select_debit_spread(card, contracts, quotes, ContractConfig(maximum_contract_cost=500), now=now)
    assert all("max_cost" not in leg.rejection_reasons for leg in legs)
    assert selected is not None
    assert selected.long_leg.contract.strike == 1035
    assert selected.entry_debit == 2.8
