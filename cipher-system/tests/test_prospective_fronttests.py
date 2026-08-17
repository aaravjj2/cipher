from __future__ import annotations

from datetime import date, datetime, timezone

from core import prospective_fronttests as fronttests


class FakeMarket:
    def stock(self, ticker):
        return 100.0

    def chain(self, ticker, start, end):
        return [{
            "symbol": f"{ticker}C", "type": "call", "expiration": end.isoformat(),
            "strike": 790.0, "bid": 1.0, "ask": 1.1, "open_interest": 1000, "volume": 100,
        }]

    def quotes(self, symbols):
        return {symbol: {"bid": 1.2, "ask": 1.3, "timestamp": "2026-08-17T14:00:00Z"} for symbol in symbols}


def _bar(timestamp, *, op=100, high=101, low=99, close=100):
    return {"time": timestamp, "open": op, "high": high, "low": low, "close": close, "volume": 1000}


def test_registration_is_read_only(tmp_path):
    db = fronttests.connect(tmp_path / "fronttest.sqlite")
    try:
        rows = fronttests.status(db)
        assert {row["program_id"] for row in rows} == {
            "tsla_stable_wall_rejection_v1", "spartan_weekly_radar_2026_08_17"
        }
        assert all(row["execution_authority"] is False for row in rows)
        assert db.execute("select max(execution_authority) from programs").fetchone()[0] == 0
    finally:
        db.close()


def test_radar_uses_latest_closed_bar_and_does_not_backfill():
    now = datetime(2026, 8, 17, 13, 36, tzinfo=timezone.utc)
    bars = {"SPY": [_bar("2026-08-17T13:30:00Z", close=780.0)]}
    signals = fronttests.detect_radar(bars, now=now)
    spy = next(row for row in signals if row["ticker"] == "SPY")
    assert spy["direction"] == "long"
    stale = fronttests.detect_radar(bars, now=datetime(2026, 8, 17, 13, 40, tzinfo=timezone.utc))
    assert not any(row["ticker"] == "SPY" for row in stale)


def test_between_five_minute_closes_is_fresh_coverage_but_not_signal_eligible():
    now = datetime(2026, 8, 17, 13, 43, 6, tzinfo=timezone.utc)
    bars = [_bar("2026-08-17T13:35:00Z", close=780.0)]
    coverage = fronttests._bar_coverage(bars, now)
    diagnostics = {}
    signals = fronttests.detect_radar({"SPY": bars}, now=now, diagnostics=diagnostics)
    assert coverage["coverage_status"] == "FRESH"
    assert coverage["reason"] == "BETWEEN_SIGNAL_WINDOWS"
    assert not any(row["ticker"] == "SPY" for row in signals)
    assert diagnostics["SPY"]["reason"] == "BETWEEN_SIGNAL_WINDOWS"
    assert diagnostics["SPY"]["coverage_status"] == "FRESH"


def test_tsla_rule_is_frozen_and_requires_stable_positive_gex(monkeypatch):
    now = datetime(2026, 8, 17, 14, 11, tzinfo=timezone.utc)
    bars = [
        _bar("2026-08-17T13:30:00Z", op=340, high=340.2, low=339.8, close=340),
        _bar("2026-08-17T14:05:00Z", op=344, high=345.2, low=342, close=342.5),
    ]
    monkeypatch.setattr(fronttests, "latest_tsla_gex", lambda *args, **kwargs: {
        "snapshot_id": "7", "captured_at": "2026-08-17T14:00:00+00:00",
        "call_wall": 344.0, "put_wall": 335.0,
        "call_wall_move": 0.0, "put_wall_move": 0.0,
        "gex_balance": 0.5, "available_rate": 1.0, "total_oi": 50_000,
    })
    signal = fronttests.detect_tsla(bars, now=now)
    assert signal is not None
    assert signal["direction"] == "short"
    assert signal["setup_id"] == "call_wall_rejection"
    assert signal["feature_snapshot_ids"] == ["7"]


def test_run_records_observed_ask_without_order_capability(tmp_path, monkeypatch):
    now = datetime(2026, 8, 17, 13, 36, tzinfo=timezone.utc)
    bars = {spec.ticker: [] for spec in fronttests.RADAR_SPECS}
    bars["SPY"] = [_bar("2026-08-17T13:30:00Z", close=780.0)]
    monkeypatch.setattr(fronttests, "detect_tsla", lambda *args, **kwargs: None)
    result = fronttests.run_once(bars, market=FakeMarket(), db_path=tmp_path / "fronttest.sqlite", now=now)
    assert result["paper_only"] is True
    assert result["execution_authority"] is False
    assert result["opened_signals"] == 1
    db = fronttests.connect(tmp_path / "fronttest.sqlite")
    try:
        signal = db.execute("select * from signals").fetchone()
        leg = db.execute("select * from option_legs").fetchone()
        observations = db.execute("select * from observations").fetchall()
        assert signal["ticker"] == "SPY"
        assert len(__import__("json").loads(signal["payload_json"])["configuration_sha256"]) == 64
        assert leg["entry_fill"] > leg["entry_ask"]
        assert len(observations) == len(fronttests.RADAR_SPECS) + 1
        spy = next(row for row in observations if row["program_id"].startswith("spartan") and row["ticker"] == "SPY")
        assert spy["decision"] == "SIGNAL_OPENED"
        assert spy["coverage_status"] == "FRESH"
    finally:
        db.close()


def test_normalise_bars_uses_new_york_session_across_dst():
    summer = fronttests._normalise_bars(
        [_bar("2026-08-17T13:30:00Z")],
        datetime.fromisoformat("2026-08-17T13:36:00+00:00"),
    )
    winter = fronttests._normalise_bars(
        [_bar("2026-11-17T14:30:00Z")],
        datetime.fromisoformat("2026-11-17T14:36:00+00:00"),
    )
    assert len(summer) == len(winter) == 1
    assert summer.iloc[0]["date"] == "2026-08-17"
    assert winter.iloc[0]["date"] == "2026-11-17"


def test_run_records_provider_errors_and_deduplicates_restart(tmp_path, monkeypatch):
    now = datetime(2026, 8, 17, 13, 36, tzinfo=timezone.utc)
    path = tmp_path / "fronttest.sqlite"
    bars = {spec.ticker: [] for spec in fronttests.RADAR_SPECS}
    bars["SPY"] = [_bar("2026-08-17T13:30:00Z", close=780.0)]
    monkeypatch.setattr(fronttests, "detect_tsla", lambda *args, **kwargs: None)
    first = fronttests.run_once(
        bars, market=FakeMarket(), db_path=path, now=now,
        bar_errors={"AAPL": "TimeoutError: source timed out"},
    )
    second = fronttests.run_once(
        bars, market=FakeMarket(), db_path=path,
        now=datetime(2026, 8, 17, 13, 36, 30, tzinfo=timezone.utc),
        bar_errors={"AAPL": "TimeoutError: source timed out"},
    )
    assert first["opened_signals"] == 1
    assert second["opened_signals"] == 0
    db = fronttests.connect(path)
    try:
        assert db.execute("select count(*) from signals").fetchone()[0] == 1
        aapl = db.execute(
            "select * from observations where ticker='AAPL' order by run_id desc limit 1"
        ).fetchone()
        spy = db.execute(
            "select * from observations where ticker='SPY' and program_id like 'spartan%' order by run_id desc limit 1"
        ).fetchone()
        assert aapl["reason"] == "PROVIDER_ERROR"
        assert aapl["coverage_status"] == "MISSING"
        assert spy["decision"] == "SIGNAL_ALREADY_RECORDED"
        assert spy["reason"] == "DEDUPLICATED"
    finally:
        db.close()


def test_tsla_diagnostics_explain_missing_gex(monkeypatch):
    now = datetime(2026, 8, 17, 14, 11, tzinfo=timezone.utc)
    bars = [_bar("2026-08-17T14:05:00Z", op=344, high=345, low=342, close=342.5)]
    monkeypatch.setattr(fronttests, "latest_tsla_gex", lambda *args, **kwargs: None)
    diagnostics = {}
    assert fronttests.detect_tsla(bars, now=now, diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "GEX_UNAVAILABLE_OR_STALE"
    assert diagnostics["decision"] == "NO_SIGNAL"


def test_radar_rejects_a_target_already_passed_at_signal():
    now = datetime(2026, 8, 17, 13, 36, tzinfo=timezone.utc)
    diagnostics = {}
    bars = {"META": [_bar("2026-08-17T13:30:00Z", close=579.225)]}
    signals = fronttests.detect_radar(bars, now=now, diagnostics=diagnostics)
    assert not any(row["ticker"] == "META" for row in signals)
    assert diagnostics["META"]["reason"] == "TARGET_ALREADY_PASSED_AT_SIGNAL"


def test_connect_quarantines_preexisting_invalid_target_geometry(tmp_path):
    path = tmp_path / "fronttest.sqlite"
    db = fronttests.connect(path)
    db.execute(
        """insert into signals values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("bad", "spartan_weekly_radar_2026_08_17", "META", "pivot_failure", "short",
         "2026-08-17T13:30:00+00:00", "2026-08-17T13:35:00+00:00", 579.225,
         588.60, None, "2026-08-21T19:55:00+00:00", "OPEN", None, None, None,
         None, "[]", "{}", "2026-08-17T13:35:00+00:00"),
    )
    db.commit()
    db.close()
    migrated = fronttests.connect(path)
    try:
        row = migrated.execute("select status,outcome from signals where signal_id='bad'").fetchone()
        event = migrated.execute("select event_type from events where signal_id='bad'").fetchone()
        assert tuple(row) == ("VOID", "TARGET_ALREADY_PASSED_AT_SIGNAL")
        assert event["event_type"] == "SIGNAL_VOIDED"
        summary = next(row for row in fronttests.status(migrated) if row["program_id"].startswith("spartan"))
        assert summary["signals"] == 1
        assert summary["eligible_signals"] == 0
        assert summary["void_signals"] == 1
    finally:
        migrated.close()
