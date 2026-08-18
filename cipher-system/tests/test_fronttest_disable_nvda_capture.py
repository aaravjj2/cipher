"""Fronttest portfolio disable + V6 continuation (C1/P1) capture routing tests."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import fronttest_portfolios as fronttest
from core.fronttest_portfolios import ACTIVE_SPECS, SPECS, detect_signals, portfolio_status
from core.structural_fib_bars import Bar

NY = ZoneInfo("America/New_York")


def bar(hhmm: str, o: float, h: float, l: float, c: float) -> Bar:
    hh, mm = map(int, hhmm.split(":"))
    return Bar(datetime(2026, 3, 2, hh, mm, tzinfo=NY), o, h, l, c, 1000)


def test_qqq_portfolios_are_disabled() -> None:
    by_id = {spec.portfolio_id: spec for spec in SPECS}
    assert by_id["qqq_validated"].enabled is False
    assert by_id["qqq_early"].enabled is False
    active_ids = {spec.portfolio_id for spec in ACTIVE_SPECS}
    assert "qqq_validated" not in active_ids
    assert "qqq_early" not in active_ids
    assert {"v6_nvda_p05", "v6_nvda_c1", "v6_nvda_p1", "mu_pm_liquidity"} <= active_ids


def test_v6_p1_continuation_signal_routes_to_nvda_p1_portfolio() -> None:
    """A P1 continuation signal on the latest closed bar must reach v6_nvda_p1.

    Regression guard for the zero-signal NVDA portfolios: the study emits P1
    rarely (continuation setups), and the fronttest captures only the latest
    closed bar — but when one fires it must route correctly and no C05/P05
    from earlier bars may leak in.
    """
    bars = [
        bar("09:00", 100.0, 101.0, 100.0, 100.5),
        bar("09:05", 100.5, 100.8, 100.4, 100.6),
        bar("09:10", 100.6, 100.9, 100.5, 100.7),
        bar("09:15", 100.7, 101.0, 100.6, 100.9),
        bar("09:20", 100.9, 101.1, 100.8, 101.0),
        bar("09:25", 101.0, 101.2, 100.9, 101.1),
        bar("09:30", 101.1, 101.3, 100.9, 101.2),
        # leg 1 down crosses the 0.5% put level; P05 fires and is later stopped out
        bar("09:35", 101.0, 101.1, 100.4, 100.5),
        bar("09:40", 100.6, 101.4, 100.6, 101.5),
        bar("09:45", 101.5, 101.6, 101.2, 101.3),
        # leg 2 down re-crosses 0.5% (P05 already fired this session), then 1%
        bar("09:50", 101.2, 101.3, 100.3, 100.4),
        bar("09:55", 100.4, 100.5, 99.8, 99.9),
    ]
    signals = detect_signals({"NVDA": bars})
    nvda = [s for s in signals if s.get("symbol") == "NVDA"]
    assert [s["setup_id"] for s in nvda] == ["P1"]
    assert nvda[0]["portfolio_id"] == "v6_nvda_p1"
    assert nvda[0]["direction"] == "short"
    # The earlier P05 bar is not the latest bar and must not be backfilled.
    assert all(s["setup_id"] != "P05" for s in signals)


def test_portfolio_status_reports_enabled_state(tmp_path: Path) -> None:
    db = fronttest.connect(tmp_path / "fronttest.sqlite")
    try:
        status = portfolio_status(db)
    finally:
        db.close()
    by_id = {row["portfolio_id"]: row for row in status}
    assert by_id["qqq_early"]["enabled"] is False
    assert by_id["qqq_validated"]["enabled"] is False
    assert by_id["v6_nvda_p05"]["enabled"] is True
    assert by_id["mu_pm_liquidity"]["enabled"] is True


def test_daily_report_snapshot_excludes_disabled_portfolios(tmp_path: Path) -> None:
    from core import portfolio_daily_report as report

    db = fronttest.connect(tmp_path / "fronttest.sqlite")
    try:
        data = report.snapshot(db, datetime(2026, 8, 18).date(),
                               prospective_db_path=tmp_path / "missing.sqlite")
    finally:
        db.close()
    ids = {row["portfolio_id"] for row in data["portfolios"]}
    assert "qqq_early" not in ids
    assert "qqq_validated" not in ids
    assert {"v6_nvda_p05", "v6_nvda_c1", "v6_nvda_p1", "mu_pm_liquidity"} <= ids
