from core.paper_executor.option_timesales_downloader import nearest_mark, parse_dt, summarize_real


def test_nearest_mark_uses_timesales_close():
    bars = [
        {"time": "2026-07-28T14:30:00Z", "close": 1.10},
        {"time": "2026-07-28T14:31:00Z", "close": 1.25},
    ]

    mark, ts = nearest_mark(bars, parse_dt("2026-07-28T14:30:45Z"), after=True)

    assert mark == 1.25
    assert ts == "2026-07-28T14:31:00+00:00"


def test_real_summary_groups_marked_trades():
    rows = [
        {"scan_type": "flash", "real_option_pnl_dollars": 25, "real_option_pnl_pct": 10, "real_win": True},
        {"scan_type": "flash", "real_option_pnl_dollars": -10, "real_option_pnl_pct": -5, "real_win": False},
        {"scan_type": "cluster"},
    ]

    summary = summarize_real(rows)

    assert summary == [{
        "scan_type": "flash",
        "trades_with_real_marks": 2,
        "wins": 1,
        "win_rate": 50.0,
        "total_real_option_pnl_dollars": 15.0,
        "average_real_option_pnl_pct": 2.5,
    }]
