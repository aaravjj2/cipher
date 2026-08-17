from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core import structural_fib_v6_paper as paper
from core.structural_fib_bars import Bar

NY = ZoneInfo("America/New_York")


def bar(hhmm: str, o: float, h: float, l: float, c: float) -> Bar:
    hh, mm = map(int, hhmm.split(":"))
    return Bar(datetime(2026, 3, 2, hh, mm, tzinfo=NY), o, h, l, c, 1000)


def session() -> list[Bar]:
    return [
        bar("09:00", 100, 101, 100, 100.5),
        bar("09:30", 100, 100.2, 100, 100.1),
        bar("09:35", 100.1, 100.75, 100.1, 100.7),
        bar("09:40", 100.8, 101.2, 100.7, 100.95),
    ]


def test_signal_is_recorded_only_on_latest_closed_bar_and_fills_next_open(tmp_path: Path):
    db = tmp_path / "paper.sqlite"
    now = datetime(2026, 3, 2, 14, 41, tzinfo=timezone.utc)  # 09:41 ET
    first = paper.run_pass(lambda _symbol: session()[:-1], symbols=["T"], db_path=db, now=now)
    assert first["new_signals"] == 1
    assert first["account"]["positions"] == {"PENDING": 1}
    second = paper.run_pass(lambda _symbol: session(), symbols=["T"], db_path=db, now=now)
    assert second["new_signals"] == 0
    assert second["account"]["positions"] == {"CLOSED": 1}


def test_old_signal_is_not_backfilled_when_latest_bar_has_no_signal(tmp_path: Path):
    bars = session() + [bar("09:45", 101.1, 101.2, 101.0, 101.1)]
    result = paper.run_pass(
        lambda _symbol: bars, symbols=["T"], db_path=tmp_path / "paper.sqlite",
        now=datetime(2026, 3, 2, 14, 51, tzinfo=timezone.utc),
    )
    assert result["new_signals"] == 0
    assert result["account"]["signals"] == 0


def test_database_is_a_separate_paper_only_account(tmp_path: Path):
    conn = paper.connect(tmp_path / "paper.sqlite")
    try:
        status = paper.account_status(conn)
    finally:
        conn.close()
    assert status["paper_only"] is True
    assert status["starting_equity"] == 100_000.0
    assert status["database_integrity"] == "ok"
