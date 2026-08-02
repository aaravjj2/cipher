from datetime import datetime, timezone

from core.paper_executor.config import ContractConfig, SimulationConfig
from core.paper_executor.fill_simulator import simulate_entry, simulate_exit, slippage
from core.paper_executor.models import Quote


def test_entry_uses_ask_plus_slippage_and_exit_uses_bid_minus_slippage():
    now = datetime.now(timezone.utc)
    quote = Quote("AAPL260730C00100000", 1.00, 1.10, now)
    sim = SimulationConfig()
    contract = ContractConfig()
    entry = simulate_entry(quote, sim, contract, 1, 2, now)
    exit_fill = simulate_exit(quote, sim, contract, 1, 2, now)
    assert entry.fill_price == round(1.10 + slippage(1.10, sim), 4)
    assert exit_fill.fill_price == round(1.00 - slippage(1.00, sim), 4)
