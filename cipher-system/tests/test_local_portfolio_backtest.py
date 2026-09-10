import json
import sqlite3
from datetime import date

from core.paper_executor.config import ExecutorConfig, PortfolioConfig, ScannerConfig, StrategyConfig
from core.paper_executor.local_portfolio_backtest import run_backtest


def _capture(root, stamp, cards, *, scan_type="flash"):
    folder = root / "uploaded"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{scan_type}_{stamp}_test.json").write_text(
        json.dumps(
            {
                "captured_at": (
                    f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T"
                    f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}+00:00"
                ),
                "scan_type": scan_type,
                "cards": cards,
            }
        )
    )


def _bars(path, rows):
    connection = sqlite3.connect(path)
    connection.execute(
        """create table bars (
        symbol text, timeframe text, timestamp text, open real, high real,
        low real, close real, volume real, primary key(symbol,timeframe,timestamp))"""
    )
    connection.executemany("insert into bars values (?, '1Min', ?, ?, ?, ?, ?, 100)", rows)
    connection.commit()
    connection.close()


def _config():
    return ExecutorConfig(
        scanner=ScannerConfig(episode_cooldown_minutes=30),
        strategy=StrategyConfig(
            allowed_tickers=("SPY",),
            entry_window_et_start="09:45",
            entry_window_et_end="11:30",
            allowed_patterns=(
                {"scanner_type": "flash", "setup": "floor bounce", "direction": "bullish"},
            ),
        ),
        portfolio=PortfolioConfig(
            starting_cash=25_000,
            maximum_new_positions_per_day=5,
            maximum_new_positions_per_ticker_per_day=1,
        ),
    )


def test_grounded_replay_uses_next_bar_and_never_claims_option_pnl(tmp_path):
    capture_root = tmp_path / "captures"
    bars_db = tmp_path / "bars.sqlite"
    base = {
        "scanner_type": "flash",
        "setup": "floor bounce",
        "direction": "bullish",
        "spot": 100,
        "target": 101,
        "invalidation": 99,
    }
    _capture(capture_root, "20260728T135015Z", [{"ticker": "SPY", **base}])
    # A conflicting future scanner spot used to manufacture an instant profit.
    _capture(
        capture_root,
        "20260728T135016Z",
        [{"ticker": "SPY", **{**base, "spot": 150}}],
        scan_type="flash_agentic",
    )
    _bars(
        bars_db,
        [
            ("SPY", "2026-07-28T13:50:00Z", 100, 100.4, 99.8, 100.2),
            ("SPY", "2026-07-28T13:51:00Z", 100.2, 101.2, 100.1, 101.1),
        ],
    )

    report = run_backtest(
        capture_root, date(2026, 7, 28), date(2026, 7, 28), _config(), bars_db
    )

    assert report["evidence_grade"] == "DIAGNOSTIC_ONLY"
    assert report["claims"] == {
        "option_pnl": False,
        "executable_option_fills": False,
        "out_of_sample_performance": False,
        "live_trading_readiness": False,
    }
    assert report["coverage"]["selected_signals"] == 1
    assert report["signals"][0]["entry_underlying_open"] == 100.2
    assert report["signals"][0]["outcome"] == "underlying_target"
    assert "portfolio" not in report
    assert not any("pnl_dollars" in row for row in report["signals"])


def test_grounded_replay_scores_actual_ohlc_target(tmp_path):
    capture_root = tmp_path / "captures"
    bars_db = tmp_path / "bars.sqlite"
    base = {
        "scanner_type": "flash",
        "setup": "floor bounce",
        "direction": "bullish",
        "spot": 100,
        "target": 101,
        "invalidation": 99,
    }
    _capture(
        capture_root,
        "20260728T135015Z",
        [{"ticker": "SPY", **base}, {"ticker": "BAD", **base}],
    )
    _bars(
        bars_db,
        [
            ("SPY", "2026-07-28T13:51:00Z", 100, 100.5, 99.8, 100.4),
            ("SPY", "2026-07-28T13:52:00Z", 100.4, 101.2, 100.2, 101.1),
        ],
    )

    report = run_backtest(
        capture_root, date(2026, 7, 28), date(2026, 7, 28), _config(), bars_db
    )

    assert report["coverage"]["observations"] == 2
    assert report["coverage"]["selected_signals"] == 1
    signal = report["signals"][0]
    assert signal["entry_underlying_open"] == 100
    assert signal["outcome"] == "underlying_target"
    assert signal["directional_underlying_return_bps"] == 100
    assert report["diagnostics"]["directionally_positive_rate_pct"] == 100


def test_grounded_replay_excludes_ambiguous_barriers(tmp_path):
    capture_root = tmp_path / "captures"
    bars_db = tmp_path / "bars.sqlite"
    _capture(
        capture_root,
        "20260728T135015Z",
        [{
            "ticker": "SPY",
            "scanner_type": "flash",
            "setup": "floor bounce",
            "direction": "bullish",
            "spot": 100,
            "target": 101,
            "invalidation": 99,
        }],
    )
    _bars(bars_db, [("SPY", "2026-07-28T13:51:00Z", 100, 101.2, 98.8, 100)])

    report = run_backtest(
        capture_root, date(2026, 7, 28), date(2026, 7, 28), _config(), bars_db
    )

    assert report["coverage"]["selected_signals"] == 0
    assert report["coverage"]["exclusions"]["ambiguous_barrier_order"] == 1


def test_grounded_replay_excludes_missing_symbol_history(tmp_path):
    capture_root = tmp_path / "captures"
    bars_db = tmp_path / "bars.sqlite"
    _capture(
        capture_root,
        "20260728T135015Z",
        [{
            "ticker": "SPY",
            "scanner_type": "flash",
            "setup": "floor bounce",
            "direction": "bullish",
            "spot": 100,
            "target": 101,
            "invalidation": 99,
        }],
    )
    _bars(bars_db, [("QQQ", "2026-07-28T13:51:00Z", 100, 101, 99, 100)])

    report = run_backtest(
        capture_root, date(2026, 7, 28), date(2026, 7, 28), _config(), bars_db
    )

    assert report["coverage"]["selected_signals"] == 0
    assert report["coverage"]["exclusions"]["no_underlying_bars"] == 1
