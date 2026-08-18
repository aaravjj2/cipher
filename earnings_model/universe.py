"""Universe management for the earnings model.

Reads the cipher-system optionable universe JSON file which stores tickers
grouped by market-cap tier under the 'sorted_tickers' key.
"""
import json
from .config import UNIVERSE_PATH, CAP_TIERS_PRIORITY


def load_universe(tiers=None):
    """Load optionable tickers from the cipher-system universe file.

    Args:
        tiers: List of cap tier names to include, e.g. ['mega', 'large'].
               Defaults to ['mega', 'large'] if None.

    Returns:
        Sorted list of unique ticker strings.
    """
    if tiers is None:
        tiers = ['mega', 'large']

    try:
        with open(UNIVERSE_PATH, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        return []

    # Tickers are nested under 'sorted_tickers' in the JSON structure
    sorted_tickers = data.get('sorted_tickers', {})

    symbols = set()
    for tier in tiers:
        tier_list = sorted_tickers.get(tier, [])
        if isinstance(tier_list, list):
            symbols.update(tier_list)

    return sorted(symbols)


def tier_for_ticker(ticker):
    """Return the cap tier string for a given ticker, or 'unknown'."""
    ticker = str(ticker).upper()
    try:
        with open(UNIVERSE_PATH, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        return 'unknown'

    sorted_tickers = data.get('sorted_tickers', {})
    for tier, symbols in sorted_tickers.items():
        if isinstance(symbols, list) and ticker in symbols:
            return tier
    return 'unknown'
