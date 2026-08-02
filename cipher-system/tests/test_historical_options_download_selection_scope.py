from __future__ import annotations

from pathlib import Path

from core.historical_options_download import ContractSelection, HistoricalOptionsStore


def _insert_contract(
    store: HistoricalOptionsStore,
    *,
    symbol: str,
    underlying: str,
    option_type: str,
    strike: float,
) -> None:
    with store.connect() as db:
        db.execute(
            """insert into contracts(
                   symbol,underlying,expiration_date,strike,option_type,
                   metadata_observed_at,raw_json
               ) values (?,?,?,?,?,?,?)""",
            (
                symbol,
                underlying,
                "2026-02-20",
                strike,
                option_type,
                "2026-01-20T21:00:00Z",
                "{}",
            ),
        )


def _selection(symbol: str, option_type: str, strike: float) -> ContractSelection:
    return ContractSelection(
        decision_date="2026-01-20",
        symbol=symbol,
        expiration_date="2026-02-20",
        strike=strike,
        option_type=option_type,
        spot=28.0,
        dte=31,
        moneyness=strike / 28.0,
        rank=1,
    )


def test_save_selections_preserves_other_underlyings(tmp_path: Path) -> None:
    store = HistoricalOptionsStore(tmp_path)
    _insert_contract(
        store,
        symbol="NVDL_TEST_P",
        underlying="NVDL",
        option_type="put",
        strike=25.0,
    )
    _insert_contract(
        store,
        symbol="TSLL_TEST_P",
        underlying="TSLL",
        option_type="put",
        strike=15.0,
    )

    store.save_selections(
        [_selection("NVDL_TEST_P", "put", 25.0)],
        ["2026-01-20"],
        underlying="NVDL",
        option_type="put",
    )
    store.save_selections(
        [_selection("TSLL_TEST_P", "put", 15.0)],
        ["2026-01-20"],
        underlying="TSLL",
        option_type="put",
    )

    with store.connect() as db:
        symbols = db.execute(
            "select symbol from decision_selections order by symbol"
        ).fetchall()
    assert symbols == [("NVDL_TEST_P",), ("TSLL_TEST_P",)]


def test_save_selections_preserves_other_option_types(tmp_path: Path) -> None:
    store = HistoricalOptionsStore(tmp_path)
    _insert_contract(
        store,
        symbol="NVDL_TEST_P",
        underlying="NVDL",
        option_type="put",
        strike=25.0,
    )
    _insert_contract(
        store,
        symbol="NVDL_TEST_C",
        underlying="NVDL",
        option_type="call",
        strike=29.0,
    )

    store.save_selections(
        [_selection("NVDL_TEST_P", "put", 25.0)],
        ["2026-01-20"],
        underlying="NVDL",
        option_type="put",
    )
    store.save_selections(
        [_selection("NVDL_TEST_C", "call", 29.0)],
        ["2026-01-20"],
        underlying="NVDL",
        option_type="call",
    )

    with store.connect() as db:
        symbols = db.execute(
            "select symbol from decision_selections order by symbol"
        ).fetchall()
    assert symbols == [("NVDL_TEST_C",), ("NVDL_TEST_P",)]
