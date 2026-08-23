from pathlib import Path
import sqlite3

from core import skew_map


def test_skew_map_preserves_formula_quadrants_and_provisional_quality(tmp_path: Path):
    db = tmp_path / "options.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        create table snapshots(ticker text, observed_at text, market_session_date text,
          front_expiry text, front_atm_iv real, front_skew_25d real, iv_coverage real,
          quote_coverage real, median_spread_pct real, contract_count integer, feed text);
        """)
        conn.execute("insert into snapshots values(?,?,?,?,?,?,?,?,?,?,?)",
          ("MU", "2026-08-21T20:00:00+00:00", "2026-08-21", "2026-08-28", .50, -.10, .9, .8, 4.0, 100, "opra"))

    def bars(_ticker, _timeframe, _limit):
        return {"bars": [{"time": "2026-07-22", "close": 100}, {"time": "2026-08-21", "close": 90}]}

    payload = skew_map.build_skew_map(bars, db_path=db, universe=("MU",))
    point = payload["points"][0]
    assert point["raw_skew"] == -.10
    assert point["normalized_skew"] == -.20
    assert point["return_1m_pct"] == -10
    assert point["quadrant"] == "contrarian_bid"
    assert point["quality"] == "provisional"
    assert payload["execution_capability"] is False


def test_skew_map_keeps_missing_price_missing(tmp_path: Path):
    db = tmp_path / "options.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        create table snapshots(ticker text, observed_at text, market_session_date text,
          front_expiry text, front_atm_iv real, front_skew_25d real, iv_coverage real,
          quote_coverage real, median_spread_pct real, contract_count integer, feed text);
        """)
        conn.execute("insert into snapshots values(?,?,?,?,?,?,?,?,?,?,?)",
          ("NVDA", "2026-08-21T20:00:00+00:00", "2026-08-21", "2026-08-28", .40, .05, .9, .8, 4.0, 100, "opra"))

    payload = skew_map.build_skew_map(lambda *_: (_ for _ in ()).throw(RuntimeError("offline")), db_path=db, universe=("NVDA",))
    point = payload["points"][0]
    assert point["return_1m_pct"] is None
    assert point["quadrant"] == "insufficient_data"
    assert "aligned one-month return unavailable" in point["quality_reasons"]
