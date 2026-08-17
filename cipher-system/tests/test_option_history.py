from datetime import timedelta

from core import option_history


def payload(day=1, iv=.30, missing_oi=False):
    rows = []
    # Bracket 30 calendar days so v2 can interpolate constant-maturity variance.
    for expiry, bump in (("2026-08-21", 0), ("2026-09-18", .03)):
        for kind, delta, strike in (("call", .5, 100), ("put", -.5, 100), ("call", .25, 105), ("put", -.25, 95)):
            rows.append({"expiry": expiry, "type": kind, "strike": strike, "delta": delta,
                         "iv": iv + bump + (.02 if kind == "put" and abs(delta) == .25 else 0),
                         "bid": 1, "ask": 1.1, "open_interest": None if missing_oi else 100, "volume": 10})
    return {"ticker": "NVDA", "timestamp": f"2026-08-{day:02d}T20:00:00+00:00", "feed": "opra", "contracts": rows}


def test_surface_derivation_preserves_unknown_oi():
    row = option_history.derive_snapshot(payload(missing_oi=True))
    assert round(row["front_skew_25d"], 4) == .02
    assert round(row["term_slope"], 4) == .03
    assert row["total_open_interest"] is None and row["oi_coverage"] == 0
    assert row["market_session_date"] == "2026-08-01"
    assert row["iv_30d"] is not None


def test_rank_stays_unavailable_until_distinct_session_minimum(tmp_path):
    db = tmp_path / "h.sqlite"
    for day in range(1, 5):
        option_history.record_snapshot(payload(day, .20 + day / 100), db)
        # Same-day duplicate must not inflate the session count.
        duplicate = payload(day, .40)
        duplicate["timestamp"] = f"2026-08-{day:02d}T21:00:00+00:00"
        option_history.record_snapshot(duplicate, db)
    status = option_history.history_status("NVDA", db, min_sessions=5)
    assert status["sessions"] == 4 and status["iv_rank"] is None


def test_rank_and_percentile_appear_after_minimum(tmp_path):
    db = tmp_path / "h.sqlite"
    for day, iv in enumerate((.20, .25, .30, .35, .40), 1):
        option_history.record_snapshot(payload(day, iv), db)
    status = option_history.history_status("NVDA", db, min_sessions=5)
    assert status["iv_history_status"] == "AVAILABLE"
    assert status["iv_rank"] == 100 and status["iv_percentile"] == 100
    assert status["metric"] == "iv_30d_constant_maturity"


def test_utc_timestamp_uses_new_york_market_date():
    sample = payload()
    sample["timestamp"] = "2026-08-15T00:30:00+00:00"
    row = option_history.derive_snapshot(sample)
    assert row["market_session_date"] == "2026-08-14"
